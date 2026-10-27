"""C043 DAG scheduler / parallel safety tests."""

from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import BudgetV1, PlanRevisionV1, StepContractV1
from backend.aix_core.runtime.planner.experimental.dag_scheduler import (
    EffectLeaseTable,
    compute_ready_set,
    parallel_dispatch_batch,
    serialize_dispatch_batch,
)


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=5, concurrency=1)


def _step(
    idx: int,
    *,
    purpose: str,
    scope: tuple[str, ...],
    depends_on: tuple[str, ...] = (),
) -> StepContractV1:
    return StepContractV1(
        cycle_id="cyc-1",
        tenant_id="t1",
        step_index=idx,
        purpose=purpose,
        target_scope=scope,
        depends_on=depends_on,
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("ok",),
        validation_commands=("pytest -q",),
        assigned_role="executor",
        step_budget=_budget(),
        status="PENDING",
    )


def test_independent_steps_ready_in_parallel() -> None:
    a = _step(0, purpose="edit a", scope=("a.py",))
    b = _step(1, purpose="edit b", scope=("b.py",))
    plan = PlanRevisionV1(objective_id="o", cycle_id="cyc-1", revision=1, steps=(a, b))
    ready = compute_ready_set(plan=plan, completed_step_ids=set())
    assert set(ready.ready_step_ids) == {a.id, b.id}
    assert set(parallel_dispatch_batch(ready)) == {a.id, b.id}
    assert serialize_dispatch_batch(ready) == ()


def test_overlapping_effects_serialize() -> None:
    a = _step(0, purpose="edit shared", scope=("shared.py",))
    b = _step(1, purpose="edit shared again", scope=("shared.py", "x.py"))
    plan = PlanRevisionV1(objective_id="o", cycle_id="cyc-1", revision=1, steps=(a, b))
    ready = compute_ready_set(plan=plan, completed_step_ids=set())
    assert set(ready.ready_step_ids) == {a.id, b.id}
    assert parallel_dispatch_batch(ready) == ()
    assert set(serialize_dispatch_batch(ready)) == {a.id, b.id}
    # while a in flight, b blocked
    ready2 = compute_ready_set(plan=plan, completed_step_ids=set(), in_flight_step_ids={a.id})
    assert a.id not in ready2.ready_step_ids  # in-flight not re-queued as ready
    assert b.id in ready2.blocked_step_ids


def test_deps_gate_ready_and_leases() -> None:
    a = _step(0, purpose="first", scope=("a.py",))
    b = _step(1, purpose="second", scope=("b.py",), depends_on=(a.id,))
    plan = PlanRevisionV1(objective_id="o", cycle_id="cyc-1", revision=1, steps=(a, b))
    r0 = compute_ready_set(plan=plan, completed_step_ids=set())
    assert r0.ready_step_ids == (a.id,)
    assert b.id in r0.blocked_step_ids
    r1 = compute_ready_set(plan=plan, completed_step_ids={a.id})
    assert r1.ready_step_ids == (b.id,)

    leases = EffectLeaseTable()
    assert leases.try_acquire(resource="a.py", step_id=a.id, cycle_id="cyc-1") is not None
    assert leases.try_acquire(resource="a.py", step_id=b.id, cycle_id="cyc-1") is None
    leases.release(resource="a.py", step_id=a.id)
    assert leases.try_acquire(resource="a.py", step_id=b.id, cycle_id="cyc-1") is not None
