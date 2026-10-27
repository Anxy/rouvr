"""Lifecycle: all ACCEPTED plan steps PASS + STEP_PASSED must reach COMPLETED_FOR_REVIEW.

Pins the ERP hang where final-step ok:True parked WAITING_APPROVAL, finalize stamped
STEP_PASSED without continue_after_approval, and resume returned ok via
approval_already_applied forever.
"""
from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    PlanRevisionV1,
    RiskTier,
    StepContractV1,
)
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.objective.step_runtime import init_step_runtime, replace_runtime_status

_BUDGET = BudgetV1(
    time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1
)


def _two_step_plan(*, objective_id: str, cycle_id: str, tenant_id: str) -> PlanRevisionV1:
    s1 = StepContractV1(
        id="step-1",
        cycle_id=cycle_id,
        tenant_id=tenant_id,
        step_index=0,
        purpose="first",
        depends_on=(),
        target_scope=("a.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("done",),
        validation_commands=("true",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        status="PENDING",
    )
    s2 = StepContractV1(
        id="step-2",
        cycle_id=cycle_id,
        tenant_id=tenant_id,
        step_index=1,
        purpose="second",
        depends_on=("step-1",),
        target_scope=("b.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("done",),
        validation_commands=("true",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        status="PENDING",
    )
    return PlanRevisionV1(
        objective_id=objective_id,
        cycle_id=cycle_id,
        revision=1,
        steps=(s1, s2),
        status="ACCEPTED",
    )


@pytest.mark.asyncio
async def test_resume_terminalizes_when_all_steps_pass_while_waiting_approval() -> None:
    """STEP_PASSED + WAITING_APPROVAL + all steps PASS → COMPLETED_FOR_REVIEW on resume."""
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=False, require_workhub_load=False
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-term",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-term-1",
            "objective": "Prove plan-complete terminal handoff",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": ("a.py", "b.py"),
        },
        idempotency_key="admit-term-1",
        expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-term",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 4}},
        idempotency_key="start-term-1",
        expected_version=None,
    )
    cycle_id = started["cycle_id"]
    objective_id = admit["objective_id"]
    cycle = backend._cycles[cycle_id]  # noqa: SLF001
    assert str(cycle.engine.state.phase) == "WAITING_APPROVAL"

    plan = _two_step_plan(
        objective_id=objective_id, cycle_id=cycle_id, tenant_id="tenant-term"
    )
    backend._plan_revisions[objective_id] = [plan]  # noqa: SLF001
    runtime = init_step_runtime(plan)
    for sid in ("step-1", "step-2"):
        runtime[sid] = replace_runtime_status(
            runtime[sid], status="PASS", verification_verdict="PASS"
        )
    backend._step_runtime[objective_id] = runtime  # noqa: SLF001
    cycle.dispatch_state = "STEP_PASSED"
    cycle.dispatch_required = False
    cycle.execution_claim_step_id = "step-2"
    cycle.current_step_cursor = "step-2"
    cycle.real_execute = {
        "ok": True,
        "code": "REAL_EXECUTE_OK",
        "objective_status": "COMPLETED",
        "verification_status": "PASSED",
        "execution_status": "COMPLETED",
    }
    # Simulate the live hang: same phase-keyed approval id already applied.
    cycle.last_approval_request_id = f"{cycle_id}:WAITING_APPROVAL"

    resumed = await backend.command(
        operation="resume_cycle",
        tenant_id="tenant-term",
        actor_id="human-1",
        resource_id=cycle_id,
        body={"approval_reference": "workhub://approvals/term-handoff"},
        idempotency_key="resume-term-1",
        expected_version=None,
    )
    assert resumed["ok"] is True
    assert resumed["phase"] == "COMPLETED_FOR_REVIEW"
    assert str(cycle.engine.state.phase) == "COMPLETED_FOR_REVIEW"


@pytest.mark.asyncio
async def test_resume_redispatches_when_approval_already_applied_but_dispatch_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate approval id with ACCEPTED plan + IDLE dispatch must re-arm execute."""
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=False, require_workhub_load=False
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-redispatch",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-redispatch-1",
            "objective": "Prove approval idempotency redispatch",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": ("a.py",),
        },
        idempotency_key="admit-redispatch-1",
        expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-redispatch",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-redispatch-1",
        expected_version=None,
    )
    cycle_id = started["cycle_id"]
    objective_id = admit["objective_id"]
    cycle = backend._cycles[cycle_id]  # noqa: SLF001
    assert str(cycle.engine.state.phase) == "WAITING_APPROVAL"

    plan = _two_step_plan(
        objective_id=objective_id, cycle_id=cycle_id, tenant_id="tenant-redispatch"
    )
    backend._plan_revisions[objective_id] = [plan]  # noqa: SLF001
    backend._step_runtime[objective_id] = init_step_runtime(plan)  # noqa: SLF001
    cycle.dispatch_state = "IDLE"
    cycle.real_execute = None
    cycle.last_approval_request_id = f"{cycle_id}:WAITING_APPROVAL"

    redispatch_reasons: list[str] = []
    original = backend._plan_then_dispatch_after_approval

    async def _track(**kwargs: object) -> dict[str, object]:
        redispatch_reasons.append(str(kwargs.get("reason")))
        return await original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(backend, "_plan_then_dispatch_after_approval", _track)

    async def _no_mid_plan_advance(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(backend, "_maybe_advance_mid_plan_dispatch", _no_mid_plan_advance)

    resumed = await backend.command(
        operation="resume_cycle",
        tenant_id="tenant-redispatch",
        actor_id="human-1",
        resource_id=cycle_id,
        body={"approval_reference": "workhub://approvals/redispatch"},
        idempotency_key="resume-redispatch-1",
        expected_version=None,
    )
    assert resumed.get("ok") is True, resumed
    assert resumed.get("note") != "approval_already_applied", resumed
    assert redispatch_reasons == ["approval_already_applied_redispatch"]


@pytest.mark.asyncio
async def test_try_terminalize_refuses_blocked_objective_status() -> None:
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=False, require_workhub_load=False
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-term",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-term-2",
            "objective": "Blocked must not terminalize",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": ("a.py",),
        },
        idempotency_key="admit-term-2",
        expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-term",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 2}},
        idempotency_key="start-term-2",
        expected_version=None,
    )
    cycle_id = started["cycle_id"]
    objective_id = admit["objective_id"]
    cycle = backend._cycles[cycle_id]  # noqa: SLF001
    plan = _two_step_plan(
        objective_id=objective_id, cycle_id=cycle_id, tenant_id="tenant-term"
    )
    backend._plan_revisions[objective_id] = [plan]  # noqa: SLF001
    runtime = init_step_runtime(plan)
    for sid in ("step-1", "step-2"):
        runtime[sid] = replace_runtime_status(
            runtime[sid], status="PASS", verification_verdict="PASS"
        )
    backend._step_runtime[objective_id] = runtime  # noqa: SLF001
    cycle.dispatch_state = "STEP_PASSED"
    cycle.real_execute = {
        "ok": True,
        "objective_status": "BLOCKED",
        "reason_code": "OBJECTIVE_NOT_PROVEN",
    }
    out = await backend._try_terminalize_all_steps_passed(cycle=cycle)  # noqa: SLF001
    assert out is None
    assert str(cycle.engine.state.phase) == "WAITING_APPROVAL"
