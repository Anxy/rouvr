"""F11-P3: Hibernate / resume via durable store + F6 checkpoint callback.

Not a second recovery engine — persists hibernation on WorkGraphStore and
emits CheckpointEventV1 through the OC-provided append hook.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.framework.multi_agent.models import StepContextCapsuleV1
from backend.aix_core.framework.scale.batch_scheduler import BatchScheduler
from backend.aix_core.framework.scale.work_graph_store import DurableWorkGraphStore, WorkGraphStore
from backend.aix_core.framework.work.models import UnitStatus


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class WakeupTrigger(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger_type: Literal["MANUAL", "TIMER", "EVENT", "APPROVAL"]
    trigger_payload: dict[str, Any] = Field(default_factory=dict)
    scheduled_at: str = Field(default_factory=_utc_now_iso)


class CycleHibernationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cycle_id: str
    graph_id: str
    work_id: str
    status: Literal["SUSPENDED", "RESUMED"]
    completed_units: int
    total_units: int
    suspended_at: str
    reason: str
    wakeup_trigger: WakeupTrigger | None = None
    resumed_at: str | None = None
    context_capsule: dict[str, Any] | None = None
    checkpoint_id: str | None = None


CheckpointAppendFn = Callable[..., str | None]


class CycleHibernationManager:
    """Durable suspend/resume — state on WorkGraphStore (+ optional F6 checkpoint)."""

    def __init__(
        self,
        store: WorkGraphStore | DurableWorkGraphStore | Any,
        scheduler: BatchScheduler,
        *,
        checkpoint_append: CheckpointAppendFn | None = None,
    ) -> None:
        self.store = store
        self.scheduler = scheduler
        self._checkpoint_append = checkpoint_append
        self._memory_fallback: dict[str, CycleHibernationRecord] = {}

    def suspend_cycle(
        self,
        cycle_id: str,
        graph_id: str,
        *,
        reason: str = "SCHEDULED_HIBERNATION",
        wakeup_trigger: WakeupTrigger | None = None,
        simulated_elapsed_hours: float = 0.0,
    ) -> CycleHibernationRecord:
        snapshot = self.store.get_snapshot(graph_id)
        if not snapshot:
            raise KeyError(f"Graph {graph_id} not found")

        for unit in self.store.list_units(graph_id):
            if unit.status == UnitStatus.RUNNING:
                self.scheduler.lane_pool.lock_manager.release_scope_locks(unit.unit_id)
                self.store.update_unit_status(graph_id, unit.unit_id, UnitStatus.PENDING)

        # Capture next-ready capsule for cold resume (F3-style context without chat history)
        ready = self.store.query_ready_wavefront(graph_id, limit=1)
        capsule_data: dict[str, Any] | None = None
        if ready:
            u = ready[0]
            capsule = StepContextCapsuleV1(
                unit_id=u.unit_id,
                work_id=u.work_id,
                assigned_role="executor_coder",  # type: ignore[arg-type]
                purpose=u.purpose,
                target_scope=u.target_scope,
            )
            capsule = capsule.model_copy(update={"capsule_hash": capsule.compute_hash()})
            capsule_data = capsule.model_dump(mode="json")

        trigger = wakeup_trigger
        if simulated_elapsed_hours and trigger is None:
            trigger = WakeupTrigger(
                trigger_type="TIMER",
                trigger_payload={"simulated_elapsed_hours": simulated_elapsed_hours},
            )

        record = CycleHibernationRecord(
            cycle_id=cycle_id,
            graph_id=graph_id,
            work_id=snapshot.work_id,
            status="SUSPENDED",
            completed_units=snapshot.completed_units,
            total_units=snapshot.total_units,
            suspended_at=_utc_now_iso(),
            reason=reason,
            wakeup_trigger=trigger,
            context_capsule=capsule_data,
        )

        checkpoint_id = None
        if self._checkpoint_append is not None:
            checkpoint_id = self._checkpoint_append(
                cycle_id=cycle_id,
                graph_id=graph_id,
                reason="BLOCKED",
                decision_state={
                    "hibernation": "SUSPENDED",
                    "hibernation_record": record.model_dump(mode="json"),
                    "workflow_phase": "SUSPENDED",
                },
            )
            if checkpoint_id:
                record = record.model_copy(update={"checkpoint_id": checkpoint_id})

        self._persist_record(record)
        return record

    def resume_cycle(
        self,
        cycle_id: str,
        *,
        resumed_by: str = "operator",
    ) -> tuple[CycleHibernationRecord, StepContextCapsuleV1 | None]:
        record = self._load_record(cycle_id)
        if record is None:
            raise KeyError(f"Cycle {cycle_id} hibernation record not found")
        if record.status == "RESUMED":
            # Idempotent resume
            capsule = None
            if record.context_capsule:
                capsule = StepContextCapsuleV1.model_validate(record.context_capsule)
            return record, capsule

        updated = record.model_copy(
            update={"status": "RESUMED", "resumed_at": _utc_now_iso()}
        )
        if self._checkpoint_append is not None:
            cp = self._checkpoint_append(
                cycle_id=cycle_id,
                graph_id=record.graph_id,
                reason="BLOCKED",
                decision_state={
                    "hibernation": "RESUMED",
                    "resumed_by": resumed_by,
                    "prior_checkpoint_id": record.checkpoint_id,
                    "workflow_phase": "RUNNING_STEP",
                },
            )
            if cp:
                updated = updated.model_copy(update={"checkpoint_id": cp})

        self._persist_record(updated)

        capsule: StepContextCapsuleV1 | None = None
        if updated.context_capsule:
            capsule = StepContextCapsuleV1.model_validate(updated.context_capsule)
        else:
            ready = self.store.query_ready_wavefront(record.graph_id, limit=1)
            if ready:
                u = ready[0]
                capsule = StepContextCapsuleV1(
                    unit_id=u.unit_id,
                    work_id=u.work_id,
                    assigned_role="executor_coder",  # type: ignore[arg-type]
                    purpose=u.purpose,
                    target_scope=u.target_scope,
                )
                capsule = capsule.model_copy(update={"capsule_hash": capsule.compute_hash()})

        return updated, capsule

    def _persist_record(self, record: CycleHibernationRecord) -> None:
        payload = record.model_dump_json()
        if hasattr(self.store, "save_hibernation_record"):
            self.store.save_hibernation_record(
                record.cycle_id,
                payload,
                graph_id=record.graph_id,
                work_id=record.work_id,
                status=record.status,
            )
        self._memory_fallback[record.cycle_id] = record

    def _load_record(self, cycle_id: str) -> CycleHibernationRecord | None:
        if hasattr(self.store, "load_hibernation_record"):
            raw = self.store.load_hibernation_record(cycle_id)
            if raw is not None:
                return CycleHibernationRecord.model_validate(raw)
        return self._memory_fallback.get(cycle_id)


__all__ = [
    "CycleHibernationManager",
    "CycleHibernationRecord",
    "WakeupTrigger",
]
