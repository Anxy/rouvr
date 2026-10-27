"""STEP_READY must never emit with null plan_revision_id or step_id."""
from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    PlanRevisionV1,
    RiskTier,
    StepContractV1,
)
from backend.aix_core.runtime.objective.durable_api_backend import (
    DurableObjectiveCycleApiBackend,
    _CycleRecord,
)

_BUDGET = BudgetV1(
    time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=1, concurrency=1
)


def _step(*, step_id: str, cycle_id: str) -> StepContractV1:
    return StepContractV1(
        id=step_id,
        cycle_id=cycle_id,
        tenant_id="tenant-1",
        step_index=0,
        purpose="executable step",
        target_scope=("a.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("done",),
        validation_commands=("true",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        status="PENDING",
    )


@pytest.mark.asyncio
async def test_mark_dispatch_ready_refuses_without_accepted_plan() -> None:
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=False, require_workhub_load=False
    )
    cycle = _CycleRecord(
        cycle_id="cyc-no-plan",
        objective_id="obj-no-plan",
        tenant_id="tenant-1",
        candidate_sha="abc",
        budgets={},
        phase="WAITING_APPROVAL",
    )
    backend._cycles[cycle.cycle_id] = cycle
    before = len(cycle.events)
    result = await backend._mark_dispatch_ready(
        cycle=cycle,
        state="STEP_READY",
        command_id="cmd-refuse-plan",
        reason="resume_approved",
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "STEP_READY_REQUIRES_ACCEPTED_PLAN"
    assert cycle.dispatch_state != "STEP_READY"
    assert cycle.phase != "STEP_READY"
    assert len(cycle.events) == before
    assert not any(
        (e.get("payload_refs") or {}).get("step_id") is None
        and (e.get("event") == "cycle.next_step_ready" or e.get("event_type") == "cycle.next_step_ready")
        for e in cycle.events
    )


@pytest.mark.asyncio
async def test_mark_dispatch_ready_refuses_planning_placeholder_step() -> None:
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=False, require_workhub_load=False
    )
    cycle = _CycleRecord(
        cycle_id="cyc-placeholder",
        objective_id="obj-placeholder",
        tenant_id="tenant-1",
        candidate_sha="abc",
        budgets={},
        phase="RUNNING_STEP",
    )
    backend._cycles[cycle.cycle_id] = cycle
    real = _step(step_id="step-real", cycle_id=cycle.cycle_id)
    plan = PlanRevisionV1(
        objective_id=cycle.objective_id,
        cycle_id=cycle.cycle_id,
        revision=1,
        status="ACCEPTED",
        steps=(real,),
    )
    backend._admit_plan_revision(plan)
    fake = _step(step_id="planning:pending", cycle_id=cycle.cycle_id)
    result = await backend._mark_dispatch_ready(
        cycle=cycle,
        state="STEP_READY",
        command_id="cmd-refuse-placeholder",
        step=fake,
        reason="resume_approved",
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "STEP_READY_REQUIRES_EXECUTABLE_STEP"
    assert cycle.dispatch_state != "STEP_READY"


@pytest.mark.asyncio
async def test_mark_dispatch_ready_emits_non_null_ids_when_valid() -> None:
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=False, require_workhub_load=False
    )
    cycle = _CycleRecord(
        cycle_id="cyc-ok",
        objective_id="obj-ok",
        tenant_id="tenant-1",
        candidate_sha="abc",
        budgets={},
        phase="RUNNING_STEP",
    )
    backend._cycles[cycle.cycle_id] = cycle
    step = _step(step_id="step-1", cycle_id=cycle.cycle_id)
    plan = PlanRevisionV1(
        objective_id=cycle.objective_id,
        cycle_id=cycle.cycle_id,
        revision=1,
        status="ACCEPTED",
        steps=(step,),
    )
    backend._admit_plan_revision(plan)
    backend._step_contracts[cycle.objective_id] = step
    result = await backend._mark_dispatch_ready(
        cycle=cycle,
        state="STEP_READY",
        command_id="cmd-ok",
        step=step,
        reason="resume_approved",
    )
    assert result["ok"] is True, result
    assert result["plan_revision_id"] == plan.plan_id
    assert result["active_step_id"] == "step-1"
    ready_payloads = [
        e.get("payload_refs") or e
        for e in cycle.events
        if "next_step_ready" in str(e.get("event") or e.get("event_type") or "")
    ]
    assert ready_payloads, cycle.events
    last = ready_payloads[-1]
    assert last.get("plan_revision_id") == plan.plan_id
    assert last.get("step_id") == "step-1"
    assert last.get("plan_revision_id") is not None
    assert last.get("step_id") is not None


@pytest.mark.no_plan_bootstrap
@pytest.mark.asyncio
async def test_resume_never_emits_step_ready_with_null_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: resume must not emit STEP_READY before ACCEPTED plan + step."""
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=False, require_workhub_load=False
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-step-ready-inv",
            "objective": "prove STEP_READY invariants",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": (),
        },
        idempotency_key="admit-step-ready-inv",
        expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-step-ready-inv",
        expected_version=None,
    )
    cycle_id = started["cycle_id"]
    cycle = backend._cycles[cycle_id]
    assert str(cycle.engine.state.phase) == "WAITING_APPROVAL"
    # No plan admitted yet — and pretend planner succeeded without creating one.
    async def _fake_ensure(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(backend, "_ensure_plan_accepted_before_claim", _fake_ensure)

    resumed = await backend.command(
        operation="resume_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=cycle_id,
        body={"approval_reference": "workhub://approvals/step-ready-inv"},
        idempotency_key="resume-step-ready-inv",
        expected_version=None,
    )
    assert resumed.get("ok") is False, resumed
    err = (resumed.get("error") or {}).get("code")
    assert err in {
        "STEP_READY_REQUIRES_ACCEPTED_PLAN",
        "STEP_READY_REQUIRES_EXECUTABLE_STEP",
    }, resumed

    null_ready = []
    for e in cycle.events:
        et = e.get("event") or e.get("event_type")
        if et != "cycle.next_step_ready":
            continue
        refs = e.get("payload_refs") or e
        if refs.get("plan_revision_id") is None or refs.get("step_id") is None:
            null_ready.append(refs)
    assert null_ready == [], f"resume emitted STEP_READY with null ids: {null_ready}"
    assert cycle.dispatch_state != "STEP_READY"


@pytest.mark.asyncio
async def test_mid_plan_advance_rearms_failed_step_when_dispatch_idle() -> None:
    """IDLE + FAILED runtime after replan must still reach STEP_READY."""
    from backend.aix_core.runtime.objective.step_runtime import init_step_runtime, replace_runtime_status

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=False, require_workhub_load=False
    )
    cycle = _CycleRecord(
        cycle_id="cyc-idle-failed",
        objective_id="obj-idle-failed",
        tenant_id="tenant-1",
        candidate_sha="abc",
        budgets={},
        phase="WAITING_APPROVAL",
    )
    backend._cycles[cycle.cycle_id] = cycle
    step = _step(step_id="step-1", cycle_id=cycle.cycle_id)
    plan = PlanRevisionV1(
        objective_id=cycle.objective_id,
        cycle_id=cycle.cycle_id,
        revision=1,
        status="ACCEPTED",
        steps=(step,),
    )
    backend._admit_plan_revision(plan)
    runtime = init_step_runtime(plan)
    runtime[step.id] = replace_runtime_status(runtime[step.id], status="FAILED")
    backend._step_runtime[cycle.objective_id] = runtime
    cycle.dispatch_state = "IDLE"
    cycle.terminal_reason = "OBJECTIVE_COVERAGE_FAILED"

    recovered = await backend._maybe_advance_mid_plan_dispatch(
        cycle=cycle, command_id="cmd-idle-failed"
    )
    assert recovered is not None and recovered.get("ok") is True, recovered
    assert cycle.dispatch_state in {"STEP_READY", "NEXT_STEP_READY"}
    assert backend._step_runtime[cycle.objective_id][step.id].status == "READY"


@pytest.mark.asyncio
async def test_mid_plan_advance_clears_zombie_running_when_dispatch_idle() -> None:
    """IDLE + RUNNING runtime must not block observe/select_next_step forever."""
    from backend.aix_core.runtime.objective.step_runtime import init_step_runtime, replace_runtime_status

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=False, require_workhub_load=False
    )
    cycle = _CycleRecord(
        cycle_id="cyc-idle-running",
        objective_id="obj-idle-running",
        tenant_id="tenant-1",
        candidate_sha="abc",
        budgets={},
        phase="WAITING_APPROVAL",
    )
    backend._cycles[cycle.cycle_id] = cycle
    step = _step(step_id="step-1", cycle_id=cycle.cycle_id)
    plan = PlanRevisionV1(
        objective_id=cycle.objective_id,
        cycle_id=cycle.cycle_id,
        revision=1,
        status="ACCEPTED",
        steps=(step,),
    )
    backend._admit_plan_revision(plan)
    runtime = init_step_runtime(plan)
    runtime[step.id] = replace_runtime_status(runtime[step.id], status="RUNNING")
    backend._step_runtime[cycle.objective_id] = runtime
    cycle.dispatch_state = "IDLE"

    recovered = await backend._maybe_advance_mid_plan_dispatch(
        cycle=cycle, command_id="cmd-idle-running"
    )
    assert recovered is not None and recovered.get("ok") is True, recovered
    assert cycle.dispatch_state in {"STEP_READY", "NEXT_STEP_READY"}
    assert backend._step_runtime[cycle.objective_id][step.id].status == "READY"


@pytest.mark.asyncio
async def test_mid_plan_advance_rearms_verifying_step_when_dispatch_idle() -> None:
    from backend.aix_core.runtime.objective.step_runtime import init_step_runtime, replace_runtime_status

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=False, require_workhub_load=False
    )
    cycle = _CycleRecord(
        cycle_id="cyc-idle-verifying",
        objective_id="obj-idle-verifying",
        tenant_id="tenant-1",
        candidate_sha="abc",
        budgets={},
        phase="WAITING_APPROVAL",
    )
    backend._cycles[cycle.cycle_id] = cycle
    step = _step(step_id="step-1", cycle_id=cycle.cycle_id)
    plan = PlanRevisionV1(
        objective_id=cycle.objective_id,
        cycle_id=cycle.cycle_id,
        revision=1,
        status="ACCEPTED",
        steps=(step,),
    )
    backend._admit_plan_revision(plan)
    runtime = init_step_runtime(plan)
    runtime[step.id] = replace_runtime_status(runtime[step.id], status="VERIFYING")
    backend._step_runtime[cycle.objective_id] = runtime
    cycle.dispatch_state = "IDLE"

    recovered = await backend._maybe_advance_mid_plan_dispatch(
        cycle=cycle, command_id="cmd-idle-verifying"
    )
    assert recovered is not None and recovered.get("ok") is True, recovered
    assert cycle.dispatch_state in {"STEP_READY", "NEXT_STEP_READY"}
    assert backend._step_runtime[cycle.objective_id][step.id].status == "READY"


@pytest.mark.asyncio
async def test_mid_plan_advance_rearms_stale_pass_when_dispatch_idle() -> None:
    from backend.aix_core.runtime.objective.step_runtime import init_step_runtime, replace_runtime_status

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=False, require_workhub_load=False
    )
    cycle = _CycleRecord(
        cycle_id="cyc-idle-pass",
        objective_id="obj-idle-pass",
        tenant_id="tenant-1",
        candidate_sha="abc",
        budgets={},
        phase="WAITING_APPROVAL",
    )
    backend._cycles[cycle.cycle_id] = cycle
    step = _step(step_id="step-1", cycle_id=cycle.cycle_id)
    plan = PlanRevisionV1(
        objective_id=cycle.objective_id,
        cycle_id=cycle.cycle_id,
        revision=1,
        status="ACCEPTED",
        steps=(step,),
    )
    backend._admit_plan_revision(plan)
    runtime = init_step_runtime(plan)
    runtime[step.id] = replace_runtime_status(
        runtime[step.id], status="PASS", verification_verdict="PASS"
    )
    backend._step_runtime[cycle.objective_id] = runtime
    cycle.dispatch_state = "IDLE"
    cycle.real_execute = {"ok": False, "objective_status": "BLOCKED"}

    recovered = await backend._maybe_advance_mid_plan_dispatch(
        cycle=cycle, command_id="cmd-idle-pass"
    )
    assert recovered is not None and recovered.get("ok") is True, recovered
    assert cycle.dispatch_state in {"STEP_READY", "NEXT_STEP_READY"}
    assert backend._step_runtime[cycle.objective_id][step.id].status == "READY"
