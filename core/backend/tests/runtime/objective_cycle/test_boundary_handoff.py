"""Focused handoff tests: accepted plan → dispatcher → executor → verifier."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.boundaries.dispatcher import observe_accepted_plan
from backend.aix_core.runtime.boundaries.verifier import normalize_verifier_status
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    PlanRevisionV1,
    RiskTier,
    StepContractV1,
)
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.objective.step_runtime import init_step_runtime
from backend.aix_core.runtime.planner.step_contract_planner import StepContractProposalV1
from backend.aix_core.runtime.verification.semantic_verifier import SemanticVerificationOutcome

_BUDGET = BudgetV1(
    time_seconds=3600, tokens=200_000, cost_usd=10.0,
    retries=3, steps=20, concurrency=1,
)


def _step(*, step_id: str, cycle_id: str, index: int = 0) -> StepContractV1:
    return StepContractV1(
        id=step_id,
        cycle_id=cycle_id,
        tenant_id="tenant-handoff",
        step_index=index,
        purpose=f"handoff step {index}",
        target_scope=("service.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=(f"step {index} complete",),
        validation_commands=("python -m py_compile service.py",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        status="PENDING",
    )


def test_accepted_plan_dispatcher_observes_ready_step() -> None:
    plan = PlanRevisionV1(
        objective_id="obj-1",
        cycle_id="cyc-1",
        revision=1,
        status="ACCEPTED",
        steps=(_step(step_id="step-ready", cycle_id="cyc-1"),),
    )
    observed = observe_accepted_plan(plan, init_step_runtime(plan))
    assert observed is not None
    assert observed.plan.plan_id == plan.plan_id
    assert observed.step.id == "step-ready"


def test_dispatcher_prefers_already_claimed_running_step() -> None:
    plan = PlanRevisionV1(
        objective_id="obj-1",
        cycle_id="cyc-1",
        revision=1,
        status="ACCEPTED",
        steps=(
            _step(step_id="step-0", cycle_id="cyc-1", index=0),
            _step(step_id="step-1", cycle_id="cyc-1", index=1),
        ),
    )
    runtime = init_step_runtime(plan)
    from backend.aix_core.runtime.objective.step_runtime import replace_runtime_status

    runtime["step-0"] = replace_runtime_status(runtime["step-0"], status="PASS")
    runtime["step-1"] = replace_runtime_status(runtime["step-1"], status="RUNNING")
    # Without preferred id, RUNNING is not selectable.
    assert observe_accepted_plan(plan, runtime) is None
    observed = observe_accepted_plan(plan, runtime, preferred_step_id="step-1")
    assert observed is not None
    assert observed.step.id == "step-1"


def test_dispatcher_ignores_non_accepted_plan() -> None:
    plan = PlanRevisionV1(
        objective_id="obj-1",
        cycle_id="cyc-1",
        revision=1,
        status="DRAFT",
        steps=(_step(step_id="step-draft", cycle_id="cyc-1"),),
    )
    assert observe_accepted_plan(plan) is None


@pytest.mark.asyncio
async def test_dispatcher_binds_accepted_plan_step_for_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        json.dumps({"handoff-repo": repo.as_posix()}),
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED",
        False,
    )

    captured_steps: list[str] = []
    verifier_contracts: list[str | None] = []

    async def propose(**kwargs: Any) -> StepContractProposalV1:
        step = _step(step_id="step-from-plan", cycle_id=kwargs["cycle_id"], index=0)
        step = step.model_copy(
            update={"obligation_refs": [item.id for item in kwargs.get("obligations") or ()]}
        )
        plan = PlanRevisionV1(
            objective_id=kwargs["objective_id"],
            cycle_id=kwargs["cycle_id"],
            revision=1,
            steps=(step,),
            obligation_coverage=tuple(step.obligation_refs),
        )
        return StepContractProposalV1(contract=step, plan=plan, reply="handoff plan", reason=None)

    async def execute(**kwargs: Any) -> RealExecuteResult:
        contract = kwargs.get("step_contract")
        captured_steps.append(contract.id if contract is not None else "")
        return RealExecuteResult(
            ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=["ev-1"],
            workhub_loaded=True, artifacts={}, execution_status="COMPLETED",
            verification_status="PASSED", objective_status="COMPLETED",
            real_operations=1, target_files_modified=1,
            diff_text="+ handoff\n",
        )

    async def verify(**kwargs: Any) -> SemanticVerificationOutcome:
        contract = kwargs.get("step_contract")
        verifier_contracts.append(contract.id if contract is not None else None)
        assert kwargs.get("result") is not None
        assert getattr(kwargs["result"], "evidence_refs", None)
        return SemanticVerificationOutcome(
            verdict="PASS",
            reasons=("handoff ok",),
            mechanical_passed=True,
            semantic_checked=False,
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract", propose
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", execute
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.verify_step", verify
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False,
    )
    with caplog.at_level(logging.INFO):
        admit = await backend.command(
            operation="admit_objective", tenant_id="tenant-handoff", actor_id="human",
            resource_id=None,
            body={
                "workhub_task_id": "wh-handoff",
                "objective": "Update service.py through a single handoff step.",
                "task_class": "single_file_helper",
                "definition_of_done": ("handoff step passes",),
                "repository_id": "handoff-repo",
                "allowed_files": ("service.py",),
            },
            idempotency_key="admit-handoff", expected_version=None,
        )
        started = await backend.command(
            operation="start_cycle", tenant_id="tenant-handoff", actor_id="human",
            resource_id=admit["objective_id"],
            body={"candidate_sha": "abc1234", "budgets": {"max_steps": 4}},
            idempotency_key="start-handoff", expected_version=None,
        )
        approved = await backend.command(
            operation="validate_approval_reference", tenant_id="tenant-handoff", actor_id="human",
            resource_id=started["cycle_id"],
            body={"approval_reference": "workhub://approvals/handoff"},
            idempotency_key="approve-handoff", expected_version=started["version"],
        )

    assert approved.get("ok") is not False
    assert captured_steps == ["step-from-plan"]
    # Verifier may run more than once on the close path; first call must see
    # the executor step + evidence from the dispatcher handoff.
    assert verifier_contracts[:1] == ["step-from-plan"]
    assert all(item == "step-from-plan" for item in verifier_contracts)

    plan = backend._active_plan_revision(admit["objective_id"])  # noqa: SLF001
    assert plan is not None and str(plan.status) == "ACCEPTED"
    assert backend._step_contracts[admit["objective_id"]].id == "step-from-plan"  # noqa: SLF001

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "[aix.planner]" in text and "event=planning_started" in text
    assert "[aix.planner]" in text and "event=plan_accepted" in text
    assert "[aix.dispatch]" in text and "event=plan_observed" in text
    assert "[aix.dispatch]" in text and "event=step_ready" in text
    assert "[aix.dispatch]" in text and "event=step_claimed" in text
    assert "[aix.executor]" in text and "event=execution_started" in text
    assert "[aix.executor]" in text and "event=execution_completed" in text
    assert "[aix.verifier]" in text and "event=PASS" in text

    claims = [
        event for event in backend._cycles[started["cycle_id"]].events  # noqa: SLF001
        if event.get("event_type") == "step.execution_claimed"
    ]
    assert claims
    assert all(not str(c["payload_refs"]["step_id"]).startswith("planning:") for c in claims)
    assert claims[0]["payload_refs"]["step_id"] == "step-from-plan"


@pytest.mark.asyncio
async def test_no_execution_claim_before_accepted_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """before ACCEPTED plan → no execution claim; planner accept → execute count 0."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        json.dumps({"order-repo": repo.as_posix()}),
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED",
        False,
    )

    execute_calls = {"n": 0}
    claim_ids_at_plan_accept: list[str | None] = []

    async def propose(**kwargs: Any) -> StepContractProposalV1:
        step = _step(step_id="step-ordered", cycle_id=kwargs["cycle_id"], index=0)
        step = step.model_copy(
            update={"obligation_refs": [item.id for item in kwargs.get("obligations") or ()]}
        )
        plan = PlanRevisionV1(
            objective_id=kwargs["objective_id"],
            cycle_id=kwargs["cycle_id"],
            revision=1,
            steps=(step,),
            obligation_coverage=tuple(step.obligation_refs),
        )
        return StepContractProposalV1(contract=step, plan=plan, reply="ordered", reason=None)

    async def execute(**kwargs: Any) -> RealExecuteResult:
        execute_calls["n"] += 1
        contract = kwargs["step_contract"]
        assert contract is not None
        assert not str(contract.id).startswith("planning:")
        return RealExecuteResult(
            ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=["ev-1"],
            workhub_loaded=True, artifacts={}, execution_status="COMPLETED",
            verification_status="PASSED", objective_status="COMPLETED",
            real_operations=1, target_files_modified=1,
            diff_text="+ ordered\n",
        )

    real_admit = None

    def _wrap_admit(original):
        def wrapped(plan):
            # Capture claim state exactly when plan becomes ACCEPTED.
            for cycle in backend._cycles.values():  # noqa: SLF001
                if cycle.objective_id == plan.objective_id:
                    claim_ids_at_plan_accept.append(cycle.execution_claim_id)
            return original(plan)
        return wrapped

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract", propose
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", execute
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False,
    )
    backend._admit_plan_revision = _wrap_admit(backend._admit_plan_revision)  # noqa: SLF001

    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-handoff", actor_id="human",
        resource_id=None,
        body={
            "workhub_task_id": "wh-order",
            "objective": "Update service.py with ordered claim boundaries.",
            "task_class": "single_file_helper",
            "definition_of_done": ("ordered step passes",),
            "repository_id": "order-repo",
            "allowed_files": ("service.py",),
        },
        idempotency_key="admit-order", expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-handoff", actor_id="human",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 4}},
        idempotency_key="start-order", expected_version=None,
    )
    # Before approval/dispatch there is no claim and no execute.
    assert backend._cycles[started["cycle_id"]].execution_claim_id is None  # noqa: SLF001
    assert execute_calls["n"] == 0

    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-handoff", actor_id="human",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/order"},
        idempotency_key="approve-order", expected_version=started["version"],
    )
    assert approved.get("ok") is not False
    assert claim_ids_at_plan_accept, "expected plan acceptance during prepare"
    assert all(claim is None for claim in claim_ids_at_plan_accept)
    assert execute_calls["n"] == 1

    claims = [
        event for event in backend._cycles[started["cycle_id"]].events  # noqa: SLF001
        if event.get("event_type") == "step.execution_claimed"
    ]
    assert len(claims) == 1
    assert claims[0]["payload_refs"]["step_id"] == "step-ordered"
    assert backend._step_contracts[admit["objective_id"]].id == "step-ordered"  # noqa: SLF001


def test_verifier_status_surface() -> None:
    assert normalize_verifier_status("PASS") == "PASS"
    assert normalize_verifier_status("REPAIR") == "REPAIR"
    assert normalize_verifier_status("HALT") == "HALT"
    assert normalize_verifier_status("REPLAN") == "REPLAN"
    assert normalize_verifier_status("REUNDERSTAND") == "REUNDERSTAND"
