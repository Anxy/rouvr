"""AIX-MPE-003 Packets MPE-10–MPE-15 / MPE-19–MPE-24: verify → next / repair / replan / budgets."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.objective.experimental.autonomous_controller import next_controller_action, route_verifier_feedback
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    LoopBudgetV1,
    PlanRevisionV1,
    RiskTier,
    StepContractV1,
)
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.infrastructure.model_gateway.hyeve_integration_client import ChatCompletionResultV1
from backend.aix_core.runtime.recovery.loop_budget import AutonomousLoopTracker
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.recovery.repair_directive import build_repair_directive
from backend.aix_core.runtime.planner.step_contract_planner import StepContractProposalV1
from backend.aix_core.runtime.objective.step_evidence import missing_required_evidence
from backend.aix_core.runtime.objective.step_runtime import init_step_runtime

_BUDGET = BudgetV1(time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1)


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture
def cron_repo(tmp_path: Path) -> Path:
    (tmp_path / "CronJobController.php").write_text(
        "<?php\nclass CronJobController {\n    public function runBillingCron() { return true; }\n}\n",
        encoding="utf-8", newline="\n",
    )
    _init_git_repo(tmp_path)
    return tmp_path


def _step(
    *,
    step_id: str,
    index: int,
    depends_on: tuple[str, ...] = (),
    purpose: str = "do step",
    required_evidence: tuple[str, ...] = (),
) -> StepContractV1:
    return StepContractV1(
        id=step_id,
        cycle_id="placeholder",
        tenant_id="tenant-1",
        step_index=index,
        purpose=purpose,
        depends_on=depends_on,
        target_scope=("CronJobController.php",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("methods are grouped by domain in CronJobController.php",),
        validation_commands=("php -l CronJobController.php",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        required_evidence=required_evidence,
        status="PENDING",
    )


def _plan_from_steps(kwargs: dict[str, Any], steps: tuple[StepContractV1, ...]) -> PlanRevisionV1:
    data = [s.model_dump() for s in steps]
    for item in data:
        item["cycle_id"] = kwargs["cycle_id"]
        item["obligation_refs"] = [o.id for o in kwargs["obligations"]]
    resolved = tuple(StepContractV1.model_validate(item) for item in data)
    return PlanRevisionV1(
        objective_id=kwargs["objective_id"],
        cycle_id=kwargs["cycle_id"],
        revision=1,
        steps=resolved,
    )


async def _admit_start_approve(
    backend: DurableObjectiveCycleApiBackend, *, key: str = "mpe10",
) -> dict[str, Any]:
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={
            "workhub_task_id": f"wh-{key}",
            "objective": "refactor CronJobController.php: group methods then extract helpers",
            "task_class": "single_file_helper",
            "definition_of_done": ("group methods", "extract helpers"),
            "repository_id": "visions",
            "allowed_files": ("CronJobController.php",),
        },
        idempotency_key=f"admit-{key}", expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 5}},
        idempotency_key=f"start-{key}", expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": f"workhub://approvals/{key}"},
        idempotency_key=f"approve-{key}", expected_version=started["version"],
    )
    return {"admit": admit, "started": started, "approved": approved}


async def _dispatch(
    backend: DurableObjectiveCycleApiBackend, *, cycle_id: str, key: str,
) -> dict[str, Any]:
    return await backend.command(
        operation="dispatch_ready_step", tenant_id="tenant-1", actor_id="dispatcher",
        resource_id=cycle_id, body={}, idempotency_key=f"dispatch-{key}",
        expected_version=None,
    )


def test_controller_routes_verifier_feedback() -> None:
    assert route_verifier_feedback("PASS").action == "CONTINUE"
    assert route_verifier_feedback("REPAIR").action == "REPAIR"
    assert route_verifier_feedback("REUNDERSTAND").action == "REUNDERSTAND"
    assert route_verifier_feedback("REPLAN").action == "REPLAN"
    assert route_verifier_feedback("HALT").action == "HALT"


def test_controller_does_not_complete_when_required_obligations_remain() -> None:
    s1 = _step(step_id="step-s1", index=0)
    plan = PlanRevisionV1(objective_id="obj-1", cycle_id="c1", revision=1, steps=(s1,))
    runtime = init_step_runtime(plan)
    from backend.aix_core.runtime.objective.step_runtime import replace_runtime_status
    runtime[s1.id] = replace_runtime_status(runtime[s1.id], status="PASS")
    decision = next_controller_action(
        plan=plan,
        runtime=runtime,
        last_verdict="PASS",
        remaining_required_obligation_ids=("ob-missing",),
    )
    assert decision.action == "REPLAN"


def test_missing_diff_evidence_cannot_pass() -> None:
    step = _step(step_id="step-s1", index=0, required_evidence=("diff",))
    result = RealExecuteResult(
        ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=True,
        artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
        objective_status="COMPLETED", real_operations=1, target_files_modified=1, diff_text="",
    )
    assert missing_required_evidence(step=step, result=result) == ("diff",)


def test_repair_directive_is_structured_not_prose() -> None:
    step = _step(step_id="step-s1", index=0)
    directive = build_repair_directive(step=step, reasons=("cosmetic only",))
    assert directive.step_id == step.id
    assert directive.required_correction == "cosmetic only"
    assert "try again" not in directive.required_correction.lower()


def test_loop_tracker_halts_on_repeated_patch_and_attempt_ceiling() -> None:
    tracker = AutonomousLoopTracker(budget=LoopBudgetV1(max_total_attempts=2, max_step_repairs=1))
    assert tracker.record_attempt() is None
    assert tracker.record_attempt() is None
    assert tracker.record_attempt() == "MAX_TOTAL_ATTEMPTS"
    tracker2 = AutonomousLoopTracker()
    assert tracker2.record_patch("+ a") is None
    assert tracker2.record_patch("+ a") is None
    assert tracker2.record_patch("+ a") == "REPEATED_IDENTICAL_PATCH"


@pytest.mark.asyncio
async def test_s1_pass_dispatches_s2_without_another_human_command(
    cron_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"visions": "{cron_repo.as_posix()}"}}'
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED", True
    )
    captured: list[str] = []

    async def _fake_execute(**kwargs: Any) -> RealExecuteResult:
        captured.append(kwargs["step_contract"].id)
        return RealExecuteResult(
            ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=True,
            artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
            objective_status="COMPLETED", real_operations=1, target_files_modified=1,
            diff_text="+ helper\n",
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _fake_execute
    )

    async def _fake_propose(**kwargs: Any) -> StepContractProposalV1:
        plan = _plan_from_steps(
            kwargs,
            (
                _step(step_id="step-s1", index=0, purpose="group methods"),
                _step(step_id="step-s2", index=1, depends_on=("step-s1",), purpose="extract helpers"),
            ),
        )
        return StepContractProposalV1(contract=plan.steps[0], reply="p", reason=None, plan=plan)

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract", _fake_propose
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    result = await _admit_start_approve(backend, key="mpe10")
    assert result["approved"]["ok"] is True, result["approved"]
    assert result["approved"]["phase"] == "NEXT_STEP_READY"
    assert captured == ["step-s1"]
    dispatched = await _dispatch(
        backend, cycle_id=result["started"]["cycle_id"], key="mpe10-s2"
    )
    assert dispatched["ok"] is True, dispatched
    assert captured == ["step-s1", "step-s2"]
    runtime = backend._step_runtime[result["admit"]["objective_id"]]  # noqa: SLF001
    assert runtime["step-s1"].status == "PASS"
    assert runtime["step-s2"].status == "PASS"


@pytest.mark.asyncio
async def test_repair_retries_same_step_then_advances(
    cron_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"visions": "{cron_repo.as_posix()}"}}'
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED", True
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", True
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED", True
    )
    captured: list[tuple[str, str]] = []
    # One verdict per step attempt, followed by the distinct final objective proof.
    verdicts = iter(["REPAIR", "PASS", "PASS", "PASS"])

    async def _fake_execute(**kwargs: Any) -> RealExecuteResult:
        captured.append((kwargs["step_contract"].id, kwargs.get("workspace_attempt_id") or ""))
        return RealExecuteResult(
            ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=True,
            artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
            objective_status="COMPLETED", real_operations=1, target_files_modified=1,
            diff_text=f"+ repair {len(captured)}\n",
            completion_gates={"gates": {"diff_exists": True, "verification_passed": True}, "verification_verdict": "PASSED"},
        )

    async def _fake_chat(**kwargs: Any) -> ChatCompletionResultV1:
        verdict = next(verdicts)
        return ChatCompletionResultV1(
            output_text=json.dumps({"verdict": verdict, "reasons": ["x"], "criteria_results": []}),
            provider="test", resolved_provider="test", model="test-model", usage={},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _fake_execute
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.verification.semantic_verifier.chat_completion", _fake_chat
    )

    async def _fake_propose(**kwargs: Any) -> StepContractProposalV1:
        plan = _plan_from_steps(
            kwargs,
            (
                _step(step_id="step-s1", index=0, purpose="group methods"),
                _step(step_id="step-s2", index=1, depends_on=("step-s1",), purpose="extract helpers"),
            ),
        )
        return StepContractProposalV1(contract=plan.steps[0], reply="p", reason=None, plan=plan)

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract", _fake_propose
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    result = await _admit_start_approve(backend, key="mpe11")
    assert result["approved"]["ok"] is True, result["approved"]
    assert result["approved"]["phase"] == "STEP_READY"
    repaired = await _dispatch(
        backend, cycle_id=result["started"]["cycle_id"], key="mpe11-repair"
    )
    assert repaired["ok"] is True, repaired
    assert repaired["phase"] == "NEXT_STEP_READY"
    finished = await _dispatch(
        backend, cycle_id=result["started"]["cycle_id"], key="mpe11-s2"
    )
    assert finished["ok"] is True, finished
    assert captured[0][0] == "step-s1"
    assert captured[1][0] == "step-s1"
    assert captured[2][0] == "step-s2"
    assert captured[0][1].startswith("att_")
    assert captured[1][1].startswith("att_")
    assert captured[0][1] != captured[1][1]
    objective_id = result["admit"]["objective_id"]
    assert backend._repair_directives[objective_id]  # noqa: SLF001


@pytest.mark.asyncio
async def test_replan_clears_cached_contract_and_admits_revision_two(
    cron_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"visions": "{cron_repo.as_posix()}"}}'
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED", True
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", True
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED", True
    )
    propose_calls = {"n": 0}
    # The replanned step must pass, then the admitted objective receives its own proof.
    verdicts = iter(["REPLAN", "PASS", "PASS"])

    async def _fake_execute(**kwargs: Any) -> RealExecuteResult:
        return RealExecuteResult(
            ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=True,
            artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
            objective_status="COMPLETED", real_operations=1, target_files_modified=1,
            diff_text="+ replan\n",
            completion_gates={"gates": {"diff_exists": True, "verification_passed": True}, "verification_verdict": "PASSED"},
        )

    async def _fake_chat(**kwargs: Any) -> ChatCompletionResultV1:
        verdict = next(verdicts)
        return ChatCompletionResultV1(
            output_text=json.dumps({"verdict": verdict, "reasons": ["strategy wrong"], "criteria_results": []}),
            provider="test", resolved_provider="test", model="test-model", usage={},
        )

    async def _fake_propose(**kwargs: Any) -> StepContractProposalV1:
        propose_calls["n"] += 1
        suffix = "r1" if propose_calls["n"] == 1 else "r2"
        plan = _plan_from_steps(
            kwargs,
            (_step(step_id=f"step-{suffix}", index=0, purpose=f"approach {suffix}"),),
        )
        return StepContractProposalV1(contract=plan.steps[0], reply="p", reason=None, plan=plan)

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _fake_execute
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.verification.semantic_verifier.chat_completion", _fake_chat
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract", _fake_propose
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    result = await _admit_start_approve(backend, key="mpe12")
    assert result["approved"]["ok"] is True, result["approved"]
    assert result["approved"]["phase"] == "STEP_READY"
    replanned = await _dispatch(
        backend, cycle_id=result["started"]["cycle_id"], key="mpe12-r2"
    )
    assert replanned["ok"] is True, replanned
    assert propose_calls["n"] == 2
    objective_id = result["admit"]["objective_id"]
    revisions = backend._plan_revisions[objective_id]  # noqa: SLF001
    assert len(revisions) == 2
    assert revisions[0].steps[0].id == "step-r1"
    assert revisions[1].steps[0].id == "step-r2"
    assert revisions[1].revision == 2
    assert backend._step_contracts[objective_id].id == "step-r2"  # noqa: SLF001
