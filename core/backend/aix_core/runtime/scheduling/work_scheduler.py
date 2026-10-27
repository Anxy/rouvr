"""P19: Durable Scheduling & Background Work.

Provides scheduled, deferred, and event-triggered work execution.
Integrates with the existing step scheduler and Temporal infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Literal
from backend.aix_core.runtime.objective.ulid_compat import new_ulid
class ScheduleState(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


TriggerKind = Literal["once", "recurring", "cron", "event", "condition"]


@dataclass(frozen=True)
class ScheduleTriggerV1:
    """Defines when work should execute."""
    kind: TriggerKind
    at: str | None = None  # ISO-8601 for "once"
    interval_seconds: int | None = None  # for "recurring"
    cron_expression: str | None = None  # for "cron"
    event_type: str | None = None  # for "event"
    condition: str | None = None  # for "condition" (evaluated expression)
    max_occurrences: int | None = None


@dataclass
class ScheduledWorkV1:
    """A unit of scheduled/background work."""
    work_id: str
    tenant_id: str
    objective_id: str
    description: str
    trigger: ScheduleTriggerV1
    state: ScheduleState = ScheduleState.PENDING
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )
    next_run_at: str | None = None
    last_run_at: str | None = None
    run_count: int = 0
    max_runs: int | None = None
    priority: int = 5  # 1=highest, 10=lowest
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class WorkExecutionRecord:
    """Record of a single execution of scheduled work."""
    execution_id: str
    work_id: str
    started_at: str
    completed_at: str | None = None
    status: Literal["RUNNING", "SUCCESS", "FAILED", "TIMEOUT"] = "RUNNING"
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class WorkScheduler:
    """In-memory work scheduler. Production targets Temporal + Postgres."""

    def __init__(self) -> None:
        self._work: dict[str, ScheduledWorkV1] = {}
        self._executions: list[WorkExecutionRecord] = []

    def schedule(self, work: ScheduledWorkV1) -> ScheduledWorkV1:
        if work.trigger.kind == "once" and work.trigger.at:
            work.next_run_at = work.trigger.at
        elif work.trigger.kind == "recurring" and work.trigger.interval_seconds:
            dt = datetime.now(UTC) + timedelta(seconds=work.trigger.interval_seconds)
            work.next_run_at = dt.isoformat().replace("+00:00", "Z")
        work.state = ScheduleState.ACTIVE
        self._work[work.work_id] = work
        return work

    def cancel(self, work_id: str) -> bool:
        w = self._work.get(work_id)
        if w is None or w.state in (ScheduleState.COMPLETED, ScheduleState.CANCELLED):
            return False
        w.state = ScheduleState.CANCELLED
        return True

    def pause(self, work_id: str) -> bool:
        w = self._work.get(work_id)
        if w is None or w.state != ScheduleState.ACTIVE:
            return False
        w.state = ScheduleState.PAUSED
        return True

    def resume(self, work_id: str) -> bool:
        w = self._work.get(work_id)
        if w is None or w.state != ScheduleState.PAUSED:
            return False
        w.state = ScheduleState.ACTIVE
        return True

    def get_due_work(self, now: str | None = None) -> list[ScheduledWorkV1]:
        """Return work items due for execution."""
        now = now or datetime.now(UTC).isoformat().replace("+00:00", "Z")
        due = []
        for w in self._work.values():
            if w.state != ScheduleState.ACTIVE:
                continue
            if w.next_run_at and w.next_run_at <= now:
                due.append(w)
            elif w.trigger.kind == "event":
                continue  # event-triggered, not time-based
        due.sort(key=lambda x: x.priority)
        return due

    def record_execution_start(self, work_id: str) -> WorkExecutionRecord | None:
        w = self._work.get(work_id)
        if w is None:
            return None
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        record = WorkExecutionRecord(
            execution_id=f"exec-{new_ulid()[:12]}",
            work_id=work_id,
            started_at=now,
        )
        self._executions.append(record)
        w.run_count += 1
        w.last_run_at = now
        return record

    def record_execution_complete(
        self, execution_id: str, *, success: bool, result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        for i, ex in enumerate(self._executions):
            if ex.execution_id == execution_id:
                self._executions[i] = WorkExecutionRecord(
                    execution_id=ex.execution_id,
                    work_id=ex.work_id,
                    started_at=ex.started_at,
                    completed_at=now,
                    status="SUCCESS" if success else "FAILED",
                    result=result or {},
                    error=error,
                )
                w = self._work.get(ex.work_id)
                if w:
                    self._advance_schedule(w, success)
                return

    def fire_event(self, event_type: str) -> list[ScheduledWorkV1]:
        """Trigger event-based work."""
        triggered = []
        for w in self._work.values():
            if w.state != ScheduleState.ACTIVE:
                continue
            if w.trigger.kind == "event" and w.trigger.event_type == event_type:
                triggered.append(w)
        return triggered

    def get(self, work_id: str) -> ScheduledWorkV1 | None:
        return self._work.get(work_id)

    def list_work(self, *, tenant_id: str | None = None) -> list[ScheduledWorkV1]:
        items = list(self._work.values())
        if tenant_id:
            items = [w for w in items if w.tenant_id == tenant_id]
        return items

    def _advance_schedule(self, w: ScheduledWorkV1, success: bool) -> None:
        if not success:
            w.error = "last execution failed"
        if w.max_runs and w.run_count >= w.max_runs:
            w.state = ScheduleState.COMPLETED
            return
        if w.trigger.max_occurrences and w.run_count >= w.trigger.max_occurrences:
            w.state = ScheduleState.COMPLETED
            return
        if w.trigger.kind == "once":
            w.state = ScheduleState.COMPLETED
        elif w.trigger.kind == "recurring" and w.trigger.interval_seconds:
            dt = datetime.now(UTC) + timedelta(seconds=w.trigger.interval_seconds)
            w.next_run_at = dt.isoformat().replace("+00:00", "Z")


__all__ = [
    "ScheduleState", "TriggerKind", "ScheduleTriggerV1",
    "ScheduledWorkV1", "WorkExecutionRecord", "WorkScheduler",
]
