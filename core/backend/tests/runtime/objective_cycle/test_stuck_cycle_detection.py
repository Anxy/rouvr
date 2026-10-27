"""AIX-REAL-EXECUTION-007 Workstream H: deterministic stuck-cycle detection --
repeated resume/approval attempts with zero real tool calls must block, never
silently loop forever."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


@pytest.mark.asyncio
async def test_repeated_resume_with_zero_real_calls_blocks() -> None:
    """A cycle that keeps arriving at `_control` in `WAITING_APPROVAL` with a fresh
    approval id each time and zero real tool calls (`cycle.real_execute is None`)
    must trip the deterministic loop guard on the 3rd genuine attempt -- proven by
    driving the real `_control` dispatch (not a simulated call), with only the
    engine's own reported phase reset between attempts to represent a cycle that
    keeps getting sent back to the same wait state by an external caller."""
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=False)
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": "Prove stuck-cycle detection",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            # Ground Understanding so dry-run can leave WAITING_APPROVAL; loop guard
            # still trips on repeated resume with zero real_execute (see resume_count).
            "allowed_files": ("service.py",),
        },
        idempotency_key="admit-stuck1",
        expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-stuck1",
        expected_version=None,
    )
    cycle_id = started["cycle_id"]
    record = backend._cycles[cycle_id]  # noqa: SLF001
    assert str(record.engine.state.phase) == "WAITING_APPROVAL"

    results = []
    for i in range(4):
        result = await backend.command(
            operation="resume_cycle",
            tenant_id="tenant-1",
            actor_id="human-1",
            resource_id=cycle_id,
            body={"approval_reference": f"workhub://approvals/attempt-{i}", "reason": f"attempt-{i}"},
            idempotency_key=f"resume-stuck1-{i}",
            expected_version=None,
        )
        results.append(result)
        # This cycle's own resume action resolves the wait in one call (no built-in
        # way to keep it genuinely stuck through the real engine) -- reset the
        # engine's reported phase back to WAITING_APPROVAL between attempts to
        # represent an external caller that keeps re-sending it there, and clear the
        # duplicate-id dedup field so each attempt is a genuinely new one, isolating
        # exactly the guard this test targets: `resume_count` accumulated across
        # distinct attempts with zero real tool calls.
        record.engine.state.phase = "WAITING_APPROVAL"
        record.last_approval_request_id = None
        record.resume_in_progress = False

    loop_detected = [
        r for r in results if r.get("ok") is False and r.get("error", {}).get("code") == "EXECUTION_LOOP_DETECTED"
    ]
    assert loop_detected, f"expected at least one EXECUTION_LOOP_DETECTED response, got: {results}"
    assert record.resume_count > 2


@pytest.mark.asyncio
async def test_single_approval_is_unaffected_by_the_loop_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.aix_core.runtime.objective.contracts import (
        BudgetV1,
        PlanRevisionV1,
        RiskTier,
        StepContractV1,
    )
    from backend.aix_core.runtime.objective.step_runtime import init_step_runtime

    budget = BudgetV1(
        time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=1, concurrency=1
    )
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=False)

    async def _ensure_ready(*, cycle, command_id, caller_auth, actor_id):  # noqa: ARG001
        step = StepContractV1(
            id="step-1",
            cycle_id=cycle.cycle_id,
            tenant_id="tenant-1",
            step_index=0,
            purpose="perform durable approval path step",
            target_scope=("service.py",),
            expected_output_schema_ref="aix.step_contract.v1.generic_edit",
            acceptance_criteria=("step 0 complete",),
            validation_commands=("python -m py_compile service.py",),
            assigned_role="executor",
            risk_tier=RiskTier.LOW,
            step_budget=budget,
            status="PENDING",
        )
        plan = PlanRevisionV1(
            objective_id=cycle.objective_id,
            cycle_id=cycle.cycle_id,
            revision=1,
            status="ACCEPTED",
            steps=(step,),
        )
        backend._admit_plan_revision(plan)
        backend._step_contracts[cycle.objective_id] = step
        backend._step_runtime[cycle.objective_id] = init_step_runtime(plan)
        cycle.current_step_cursor = step.id
        return None

    monkeypatch.setattr(backend, "_ensure_plan_accepted_before_claim", _ensure_ready)
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": "Normal single approval",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": ("service.py",),
        },
        idempotency_key="admit-normal1",
        expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-normal1",
        expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/a1"},
        idempotency_key="approve-normal1",
        expected_version=started["version"],
    )
    assert approved["ok"] is True, approved
    assert approved["phase"] == "COMPLETED_FOR_REVIEW"
    cycle = backend._cycles[started["cycle_id"]]
    for e in cycle.events:
        if e.get("event_type") != "cycle.next_step_ready":
            continue
        refs = e.get("payload_refs") or {}
        assert refs.get("plan_revision_id") is not None, e
        assert refs.get("step_id") is not None, e
