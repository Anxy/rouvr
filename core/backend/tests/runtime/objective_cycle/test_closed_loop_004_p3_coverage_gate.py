"""AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P3: Objective Fidelity / Obligation
Coverage, wired end-to-end through `DurableObjectiveCycleApiBackend._maybe_real_
execute`. The planner model call is stubbed (no live LLM in this environment, same
discipline as `test_al12_contradictory_contract.py`/`test_w6_governance_scope.py`) --
what's under test is real: `durable_api_backend.py` actually calls `validate_
obligation_coverage()` before `run_real_execute` and refuses dispatch when it fails.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    RiskTier,
    StepContractV1,
    UnderstandingEvidenceV1,
)
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.planner.step_contract_planner import StepContractProposalV1


def _admit_body(*, definition_of_done: tuple[str, ...]) -> dict[str, Any]:
    return {
        "workhub_task_id": "task-601",
        "objective": "Refactor CronJobController.php",
        "task_class": "single_file_helper",
        "definition_of_done": definition_of_done,
        "repository_id": "aix-frontend",
        "allowed_files": ("CronJobController.php",),
        "project_id": "proj-1",
        "task_number": 601,
    }


def _fake_contract(*, obligation_refs: tuple[str, ...]) -> StepContractV1:
    return StepContractV1(
        cycle_id="cyc-1",
        tenant_id="tenant-1",
        step_index=0,
        purpose="Group CronJobController public methods without changing call sites",
        target_scope=("CronJobController.php",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("public methods are grouped by domain in CronJobController.php",),
        validation_commands=("echo ok",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1),
        obligation_refs=obligation_refs,
        status="PENDING",
    )


async def _admit_start_approve(
    backend: DurableObjectiveCycleApiBackend, *, definition_of_done: tuple[str, ...]
) -> dict[str, Any]:
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body=_admit_body(definition_of_done=definition_of_done), idempotency_key="admit-1", expected_version=None,
    )
    assert admit["ok"] is True, admit
    backend._understanding_evidence[admit["objective_id"]] = UnderstandingEvidenceV1(  # noqa: SLF001
        objective_id=admit["objective_id"], cycle_id=None, files_read=("CronJobController.php",),
        observed_problem="test evidence", understanding_complete=True,
    )
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-1",
        expected_version=None,
    )
    assert started["ok"] is True
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-1", expected_version=started["version"],
    )
    return {"admit": admit, "started": started, "approved": approved}


@pytest.mark.asyncio
async def test_plan_dropping_a_required_obligation_is_rejected_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P3's own exit criterion, proven end-to-end: a planner that drops 'remove unused
    imports' from a five-part (here: three-part) objective is rejected before
    execution -- run_real_execute must never be called."""
    executed = {"called": False}

    async def _run_real_execute_tracked(**kwargs: Any) -> RealExecuteResult:
        executed["called"] = True
        return RealExecuteResult(
            ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=True,
            artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
            objective_status="COMPLETED", real_operations=1, target_files_modified=1,
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_tracked
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED", True
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body=_admit_body(
            definition_of_done=(
                "Group public methods by domain",
                "Extract repeated DB/query boilerplate",
                "Remove unused imports",
            )
        ),
        idempotency_key="admit-1", expected_version=None,
    )
    assert admit["ok"] is True
    obligation_ids = admit["obligation_ids"]
    assert len(obligation_ids) == 3

    # The stubbed planner "drops" the third obligation (remove unused imports) --
    # obligation_refs covers only the first two.
    async def _fake_propose_incomplete(**kwargs: Any) -> StepContractProposalV1:
        return StepContractProposalV1(
            contract=_fake_contract(obligation_refs=tuple(obligation_ids[:2])),
            reply="Grouped methods and extracted boilerplate.",
            reason=None,
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract", _fake_propose_incomplete
    )

    backend._understanding_evidence[admit["objective_id"]] = UnderstandingEvidenceV1(  # noqa: SLF001
        objective_id=admit["objective_id"], cycle_id=None, files_read=("CronJobController.php",),
        observed_problem="test evidence", understanding_complete=True,
    )
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-1",
        expected_version=None,
    )
    assert started["ok"] is True
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-1", expected_version=started["version"],
    )

    assert approved["ok"] is False, approved
    assert approved["error"]["code"] in {"PLAN_INCOMPLETE", "OBLIGATION_COVERAGE_INCOMPLETE"}
    assert "Remove unused imports" in approved["error"]["detail"]
    assert executed["called"] is False, "run_real_execute must never be called when the plan is incomplete"
    assert approved["phase"] != "COMPLETED_FOR_REVIEW"


@pytest.mark.asyncio
async def test_plan_covering_every_required_obligation_proceeds_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full coverage must not be blocked -- the gate only refuses genuine shortfalls."""
    async def _run_real_execute_ok(**kwargs: Any) -> RealExecuteResult:
        return RealExecuteResult(
            ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=True,
            artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
            objective_status="COMPLETED", real_operations=1, target_files_modified=1,
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED", True
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body=_admit_body(definition_of_done=("Group public methods by domain", "Remove unused imports")),
        idempotency_key="admit-1", expected_version=None,
    )
    assert admit["ok"] is True
    obligation_ids = admit["obligation_ids"]
    assert len(obligation_ids) == 2

    async def _fake_propose_complete(**kwargs: Any) -> StepContractProposalV1:
        return StepContractProposalV1(
            contract=_fake_contract(obligation_refs=tuple(obligation_ids)),
            reply="Grouped methods and removed unused imports.",
            reason=None,
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract", _fake_propose_complete
    )

    backend._understanding_evidence[admit["objective_id"]] = UnderstandingEvidenceV1(  # noqa: SLF001
        objective_id=admit["objective_id"], cycle_id=None, files_read=("CronJobController.php",),
        observed_problem="test evidence", understanding_complete=True,
    )
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-1",
        expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-1", expected_version=started["version"],
    )

    assert approved["ok"] is True, approved
    assert approved["phase"] == "COMPLETED_FOR_REVIEW"


@pytest.mark.no_plan_bootstrap
@pytest.mark.asyncio
async def test_flag_off_default_behavior_is_unaffected_by_the_coverage_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AIX-PA-002: planner disabled cannot mutate. No step contract, no accepted
    plan, executor is never invoked. Coverage gate is not reached."""
    executed = {"called": False}

    async def _run_real_execute_ok(**kwargs: Any) -> RealExecuteResult:
        executed["called"] = True
        return RealExecuteResult(
            ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=True,
            artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
            objective_status="COMPLETED", real_operations=1, target_files_modified=1,
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED",
        False,
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    result = await _admit_start_approve(
        backend, definition_of_done=("Group public methods by domain", "Remove unused imports")
    )
    assert result["approved"]["ok"] is False
    assert (result["approved"].get("error") or {}).get("code") == "PLANNER_DISABLED"
    assert executed["called"] is False
    assert result["admit"]["objective_id"] not in backend._step_contracts  # noqa: SLF001
