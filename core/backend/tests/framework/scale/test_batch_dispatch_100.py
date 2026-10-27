"""F11-AC-02: 100-unit batch / 8 lanes via step_scheduler authority + lock adapter."""

from __future__ import annotations

from backend.aix_core.framework.scale.batch_scheduler import BatchScheduler, ExecutionLanePool
from backend.aix_core.framework.scale.work_graph_store import WorkGraphStore
from backend.aix_core.framework.work.models import UnitStatus, WorkContract, WorkGraph, WorkUnit
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    PlanRevisionV1,
    StepContractV1,
    StepRuntimeStateV1,
)
from backend.aix_core.runtime.objective.step_scheduler import get_ready_steps, select_ready_batch


def _budget() -> BudgetV1:
    return BudgetV1(
        time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=100, concurrency=1
    )


def _make_plan_and_graph(n: int = 100) -> tuple[PlanRevisionV1, WorkGraph, dict[str, StepRuntimeStateV1]]:
    budget = _budget()
    steps: list[StepContractV1] = []
    units: list[WorkUnit] = []
    # 8 independent chains of 12 + 4 shared roots = ~100 with deps
    for i in range(n):
        sid = f"step_{i:03d}"
        # Chain every 8th for dependency waves; others independent roots
        deps = (f"step_{i-8:03d}",) if i >= 8 else ()
        scope = (f"src/lane_{i % 8}/file_{i}.py",)
        steps.append(
            StepContractV1(
                id=sid,
                cycle_id="cyc_batch",
                tenant_id="t1",
                step_index=i,
                purpose=f"unit {i}",
                depends_on=deps,
                target_scope=scope,
                expected_output_schema_ref="aix.step_contract.v1.generic_edit",
                acceptance_criteria=("ok",),
                validation_commands=("true",),
                assigned_role="executor",
                step_budget=budget,
                status="PENDING",
            )
        )
        units.append(
            WorkUnit(
                unit_id=sid,
                work_id="obj_batch",
                unit_index=i,
                purpose=f"unit {i}",
                depends_on=deps,
                target_scope=scope,
                contract=WorkContract(unit_id=sid, assigned_role="executor"),
            )
        )
    plan = PlanRevisionV1(
        plan_id="plan_batch",
        objective_id="obj_batch",
        cycle_id="cyc_batch",
        revision=1,
        status="ACCEPTED",
        steps=tuple(steps),
    )
    graph = WorkGraph(graph_id="g_batch", work_id="obj_batch", units=tuple(units))
    runtime = {
        s.id: StepRuntimeStateV1(
            step_id=s.id,
            plan_id="plan_batch",
            plan_revision=1,
            status="PENDING",
        )
        for s in steps
    }
    return plan, graph, runtime


def test_batch_dispatch_100_eight_lanes_step_scheduler_authority():
    plan, graph, runtime = _make_plan_and_graph(100)
    store = WorkGraphStore()
    store.save_work_graph(graph)
    pool = ExecutionLanePool(max_lanes=8)
    scheduler = BatchScheduler(store, pool)

    # Authority parity: adapter must not invent a different ready set
    canonical = select_ready_batch(plan, runtime, max_batch=8)
    assert len(canonical) == 8
    assert get_ready_steps(plan, runtime)[:8] == canonical

    batch = scheduler.schedule_next_batch_from_plan(
        graph_id=graph.graph_id, plan=plan, runtime=runtime, max_batch_size=8
    )
    assert batch.active_lane_count == 8
    assert len(batch.dispatched_steps) == 8
    assert {s.id for s in batch.dispatched_steps} == {s.id for s in canonical}

    # Conflicting lock: second unit same scope cannot dispatch while first holds
    conflict_unit = WorkUnit(
        unit_id="conflict_b",
        work_id="obj_batch",
        unit_index=999,
        purpose="conflict",
        target_scope=batch.dispatched_units[0].target_scope,
        contract=WorkContract(unit_id="conflict_b"),
    )
    # Holding lock from first batch — acquire same scope with other unit fails
    from backend.aix_core.framework.multi_agent.sync import ScopeLockConflictError
    import pytest

    with pytest.raises(ScopeLockConflictError):
        pool.lock_manager.acquire_scope_locks(
            "conflict_b", batch.dispatched_units[0].target_scope
        )

    # Complete one lane; others remain RUNNING (no corruption)
    first = batch.dispatched_units[0].unit_id
    scheduler.complete_unit_execution(graph.graph_id, first, success=True)
    assert store.get_unit(graph.graph_id, first).status == UnitStatus.SUCCEEDED
    still_running = [
        u
        for u in batch.dispatched_units[1:]
        if store.get_unit(graph.graph_id, u.unit_id).status == UnitStatus.RUNNING
    ]
    assert len(still_running) == 7


def test_batch_dispatch_dependent_units_wait():
    plan, graph, runtime = _make_plan_and_graph(16)
    store = WorkGraphStore()
    store.save_work_graph(graph)
    scheduler = BatchScheduler(store, ExecutionLanePool(max_lanes=8))
    batch = scheduler.schedule_next_batch_from_plan(
        graph_id=graph.graph_id, plan=plan, runtime=runtime, max_batch_size=8
    )
    # Only roots (i < 8) are ready
    assert all(int(s.id.split("_")[1]) < 8 for s in batch.dispatched_steps)
