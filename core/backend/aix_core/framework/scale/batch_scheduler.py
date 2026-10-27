"""F11-P2: Multi-lane batch adapter around canonical ``step_scheduler``.

Authority: ``get_ready_steps`` / ``select_ready_batch`` from step_scheduler.
This module only adds lane pool, scope locks, and WorkGraphStore status sync.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.aix_core.framework.multi_agent.sync import ScopeLockConflictError, ScopeLockManager
from backend.aix_core.framework.scale.work_graph_store import (
    StoredWorkUnitRecord,
    WorkGraphStore,
)
from backend.aix_core.framework.work.models import UnitStatus
from backend.aix_core.runtime.objective.contracts import (
    PlanRevisionV1,
    StepContractV1,
    StepRuntimeStateV1,
)
from backend.aix_core.runtime.objective.step_scheduler import select_ready_batch


class DispatchBatch(BaseModel):
    """Batch of ready units/steps assigned to execution lanes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    graph_id: str
    batch_index: int
    dispatched_units: tuple[StoredWorkUnitRecord, ...]
    dispatched_steps: tuple[StepContractV1, ...] = ()
    active_lane_count: int
    deferred_due_to_locks: int = 0


class ExecutionLanePool:
    """Bounded lane allocation + scope locking (not a scheduler)."""

    def __init__(self, max_lanes: int = 8) -> None:
        self.max_lanes = max(1, max_lanes)
        self._lock_manager = ScopeLockManager()

    @property
    def lock_manager(self) -> ScopeLockManager:
        return self._lock_manager


class BatchScheduler:
    """Multi-lane dispatch adapter — scheduling authority remains step_scheduler."""

    def __init__(
        self,
        store: WorkGraphStore | Any,
        lane_pool: ExecutionLanePool | None = None,
    ) -> None:
        self.store = store
        self.lane_pool = lane_pool or ExecutionLanePool()
        self._batch_counter = 0

    def schedule_next_batch_from_plan(
        self,
        *,
        graph_id: str,
        plan: PlanRevisionV1,
        runtime: dict[str, StepRuntimeStateV1],
        max_batch_size: int | None = None,
    ) -> DispatchBatch:
        """Select ready steps via step_scheduler, then apply lane locks + store sync."""
        limit = max_batch_size or self.lane_pool.max_lanes
        # Canonical scheduling authority
        ready_steps = select_ready_batch(plan, runtime, max_batch=limit * 2)
        return self._dispatch_steps(graph_id=graph_id, ready_steps=ready_steps, limit=limit)

    def schedule_next_batch(
        self,
        graph_id: str,
        *,
        max_batch_size: int | None = None,
        plan: PlanRevisionV1 | None = None,
        runtime: dict[str, StepRuntimeStateV1] | None = None,
    ) -> DispatchBatch:
        """Prefer plan+runtime path; fall back to store wavefront for pure graph tests."""
        limit = max_batch_size or self.lane_pool.max_lanes
        if plan is not None and runtime is not None:
            return self.schedule_next_batch_from_plan(
                graph_id=graph_id,
                plan=plan,
                runtime=runtime,
                max_batch_size=limit,
            )
        ready_units = self.store.query_ready_wavefront(graph_id, limit=limit * 2)
        return self._dispatch_units(graph_id=graph_id, ready_units=ready_units, limit=limit)

    def _dispatch_steps(
        self,
        *,
        graph_id: str,
        ready_steps: tuple[StepContractV1, ...],
        limit: int,
    ) -> DispatchBatch:
        dispatched_units: list[StoredWorkUnitRecord] = []
        dispatched_steps: list[StepContractV1] = []
        deferred = 0
        for step in ready_steps:
            if len(dispatched_units) >= limit:
                break
            unit = self.store.get_unit(graph_id, step.id)
            if unit is None:
                # Project missing unit from step for lock bookkeeping
                continue
            try:
                self.lane_pool.lock_manager.acquire_scope_locks(unit.unit_id, unit.target_scope)
            except ScopeLockConflictError:
                deferred += 1
                continue
            lane_id = f"lane_{(len(dispatched_units) % self.lane_pool.max_lanes) + 1}"
            updated = self.store.update_unit_status(
                graph_id,
                unit.unit_id,
                UnitStatus.RUNNING,
                assigned_lane_id=lane_id,
            )
            dispatched_units.append(updated)
            dispatched_steps.append(step)
        self._batch_counter += 1
        return DispatchBatch(
            graph_id=graph_id,
            batch_index=self._batch_counter,
            dispatched_units=tuple(dispatched_units),
            dispatched_steps=tuple(dispatched_steps),
            active_lane_count=len(dispatched_units),
            deferred_due_to_locks=deferred,
        )

    def _dispatch_units(
        self,
        *,
        graph_id: str,
        ready_units: tuple[StoredWorkUnitRecord, ...],
        limit: int,
    ) -> DispatchBatch:
        dispatched: list[StoredWorkUnitRecord] = []
        deferred = 0
        for unit in ready_units:
            if len(dispatched) >= limit:
                break
            try:
                self.lane_pool.lock_manager.acquire_scope_locks(unit.unit_id, unit.target_scope)
            except ScopeLockConflictError:
                deferred += 1
                continue
            lane_id = f"lane_{(len(dispatched) % self.lane_pool.max_lanes) + 1}"
            updated = self.store.update_unit_status(
                graph_id,
                unit.unit_id,
                UnitStatus.RUNNING,
                assigned_lane_id=lane_id,
            )
            dispatched.append(updated)
        self._batch_counter += 1
        return DispatchBatch(
            graph_id=graph_id,
            batch_index=self._batch_counter,
            dispatched_units=tuple(dispatched),
            dispatched_steps=(),
            active_lane_count=len(dispatched),
            deferred_due_to_locks=deferred,
        )

    def complete_unit_execution(
        self,
        graph_id: str,
        unit_id: str,
        success: bool,
    ) -> StoredWorkUnitRecord:
        status = UnitStatus.SUCCEEDED if success else UnitStatus.FAILED
        try:
            return self.store.update_unit_status(graph_id, unit_id, status)
        finally:
            self.lane_pool.lock_manager.release_scope_locks(unit_id)


def select_next_step_via_scale(
    plan: PlanRevisionV1,
    runtime: dict[str, StepRuntimeStateV1],
    *,
    store: WorkGraphStore | Any | None = None,
    graph_id: str | None = None,
    scheduler: BatchScheduler | None = None,
) -> StepContractV1 | None:
    """OC single-step selection when scale is on — still uses step_scheduler authority.

    Applies lock-aware batching then returns the first dispatched step (or plain
    select_next_step if no store/graph).
    """
    from backend.aix_core.runtime.objective.step_scheduler import select_next_step

    if store is None or graph_id is None:
        return select_next_step(plan, runtime)
    sch = scheduler or BatchScheduler(store)
    batch = sch.schedule_next_batch_from_plan(
        graph_id=graph_id, plan=plan, runtime=runtime, max_batch_size=1
    )
    if batch.dispatched_steps:
        return batch.dispatched_steps[0]
    # Lock contention: fall back to unlocked canonical pick (OC single-lane safety)
    return select_next_step(plan, runtime)


__all__ = [
    "BatchScheduler",
    "DispatchBatch",
    "ExecutionLanePool",
    "select_next_step_via_scale",
]
