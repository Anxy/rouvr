"""Maintained AIX event consumer: one event in, one durable action out.

Pipeline per raw event: parse (fail-closed) -> admission -> event-ID dedupe
-> projection freshness gate -> detail resolution -> Temporal start/signal ->
durable cursor advance. Anything unresolved is deferred or blocked with the
pending action preserved — never dropped, never double-acted.

``recover`` reconciles the durable cursor against Temporal handles, using
WorkHub task state as the tiebreaker: terminal WorkHub state resolves the
entry without a Temporal action; anything else is requeued exactly once.
"""
from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .admission import AdmissionPolicy, DefaultAdmissionPolicy
from .cursor import DurableCursor
from .events import AIXEvent, merge_detail, parse_event
from .temporal import TemporalStarter, build_action

TERMINAL_TASK_STATUSES = frozenset({"done", "cancelled", "failed", "closed"})


class EventSource(Protocol):
    def poll_events(self) -> list[dict[str, Any]]: ...


class WorkHubReader(Protocol):
    def read_task(self, task_number: int) -> dict[str, Any] | None: ...


class ProjectionReader(Protocol):
    def read_task_snapshot(self, task_number: int) -> dict[str, Any] | None: ...
    def snapshot_detail(self, snapshot: dict[str, Any]) -> dict[str, Any]: ...
    def is_fresh(self, snapshot: dict[str, Any]) -> bool: ...


class DictEventSource:
    """In-process source: drains the given raw event dicts once."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = [dict(item) for item in events]

    def poll_events(self) -> list[dict[str, Any]]:
        batch, self._events = self._events, []
        return batch


class MapWorkHubReader:
    """Isolated-test WorkHub reader backed by a plain mapping."""

    def __init__(self, tasks: dict[int, dict[str, Any]]) -> None:
        self._tasks = {int(key): dict(value) for key, value in tasks.items()}

    def read_task(self, task_number: int) -> dict[str, Any] | None:
        found = self._tasks.get(int(task_number))
        return dict(found) if found is not None else None


class JsonProjectionReader:
    """File-backed projection reader (the agent-interface files).

    Layout ``{root}/tasks/by-number/{n}.json`` with broker-compatible record
    keys (``status``/``data``/``source_record_version``/``fetched_at_epoch``).
    Missing files read as None (no record to gate on); present-but-stale
    records gate the event to deferred so nothing acts on a STALE read.
    """

    def __init__(
        self, *, root: str | Path, max_age_seconds: float = 60.0, clock: Any = None
    ) -> None:
        self._root = Path(root)
        self._max_age = float(max_age_seconds)
        self._clock = clock or time.time

    def _path_for(self, task_number: int) -> Path:
        return self._root / "tasks" / "by-number" / f"{int(task_number)}.json"

    def read_task_snapshot(self, task_number: int) -> dict[str, Any] | None:
        path = self._path_for(task_number)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def snapshot_detail(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        data = snapshot.get("data")
        return dict(data) if isinstance(data, dict) else {}

    def is_fresh(self, snapshot: dict[str, Any]) -> bool:
        if snapshot.get("status") != "OK":
            return False
        if not isinstance(snapshot.get("data"), dict):
            return False
        try:
            age = float(self._clock()) - float(snapshot["fetched_at_epoch"])
        except (KeyError, TypeError, ValueError):
            return False
        return age <= self._max_age


class _Deferred(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class ConsumerOutcome:
    seen: int = 0
    started: int = 0
    signalled: int = 0
    duplicates: int = 0
    rejected: int = 0
    malformed: int = 0
    deferred: int = 0
    blocked: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RecoveryReport:
    requeued: list[str] = field(default_factory=list)
    resolved_via_workhub: list[str] = field(default_factory=list)
    still_pending: list[str] = field(default_factory=list)


class AixEventConsumer:
    def __init__(
        self,
        *,
        event_source: EventSource,
        cursor: DurableCursor,
        temporal: TemporalStarter,
        admission: AdmissionPolicy | None = None,
        workhub_reader: WorkHubReader | None = None,
        projection_reader: ProjectionReader | None = None,
        detail_fn: Callable[[AIXEvent], Mapping[str, Any] | None] | None = None,
        after_temporal_action: Callable[[AIXEvent], None] | None = None,
    ) -> None:
        self.event_source = event_source
        self.cursor = cursor
        self.temporal = temporal
        self.admission = admission or DefaultAdmissionPolicy()
        self.workhub_reader = workhub_reader
        self.projection_reader = projection_reader
        self.detail_fn = detail_fn
        self.after_temporal_action = after_temporal_action

    def run_once(self, limit: int | None = None) -> ConsumerOutcome:
        outcome = ConsumerOutcome()
        batch = self.event_source.poll_events()
        if limit is not None:
            batch = batch[: max(0, int(limit))]
        for raw in batch:
            outcome.seen += 1
            try:
                event = parse_event(raw)
            except ValueError as exc:
                outcome.malformed += 1
                outcome.results.append(
                    {
                        "event_id": raw.get("event_id") if isinstance(raw, dict) else None,
                        "outcome": "malformed",
                        "reason": str(exc),
                    }
                )
                continue
            decision = self.admission.decide(event)
            if not decision.admitted:
                outcome.rejected += 1
                outcome.results.append(
                    {
                        "event_id": event.event_id,
                        "outcome": "rejected",
                        "reason": decision.reason_code,
                    }
                )
                continue
            if self.cursor.is_processed(event.event_id):
                outcome.duplicates += 1
                outcome.results.append(
                    {"event_id": event.event_id, "outcome": "duplicate"}
                )
                continue
            try:
                event = self._resolve(event)
            except _Deferred as exc:
                outcome.deferred += 1
                outcome.results.append(
                    {"event_id": event.event_id, "outcome": "deferred", "reason": exc.code}
                )
                continue
            action = build_action(event)
            if action.workflow_input is None and not self.temporal.has_workflow(
                action.workflow_id
            ):
                outcome.blocked += 1
                outcome.results.append(
                    {
                        "event_id": event.event_id,
                        "outcome": "blocked",
                        "reason": "INPUT_INCOMPLETE",
                    }
                )
                continue
            mode = "signal" if self.temporal.has_workflow(action.workflow_id) else "start"
            result = self.temporal.start_or_signal(dataclasses.replace(action, mode=mode))
            if not result.acted:
                outcome.duplicates += 1
                outcome.results.append(
                    {"event_id": event.event_id, "outcome": "duplicate"}
                )
                continue
            if self.after_temporal_action is not None:
                self.after_temporal_action(event)
            self.cursor.record(event.event_id, workflow_id=action.workflow_id, event=raw)
            if result.mode == "signal":
                outcome.signalled += 1
            else:
                outcome.started += 1
            outcome.results.append(
                {
                    "event_id": event.event_id,
                    "outcome": result.mode,
                    "workflow_id": action.workflow_id,
                }
            )
        return outcome

    def _resolve(self, event: AIXEvent) -> AIXEvent:
        if self.projection_reader is not None and event.task_number is not None:
            snapshot = self.projection_reader.read_task_snapshot(event.task_number)
            if snapshot is not None:
                if not self.projection_reader.is_fresh(snapshot):
                    raise _Deferred("PROJECTION_STALE")
                event = merge_detail(
                    event, self.projection_reader.snapshot_detail(snapshot)
                )
        detail: Mapping[str, Any] | None = None
        if self.detail_fn is not None:
            detail = self.detail_fn(event)
        elif self.workhub_reader is not None and event.task_number is not None:
            detail = self.workhub_reader.read_task(event.task_number)
        event = merge_detail(event, detail)
        if not event.objective_id or not event.cycle_id:
            raise _Deferred("DETAIL_UNAVAILABLE")
        return event

    def recover(self) -> RecoveryReport:
        """Reconcile the durable cursor against Temporal handles + WorkHub."""
        report = RecoveryReport()
        for event_id in self.cursor.processed_ids():
            entry = self.cursor.entry(event_id) or {}
            workflow_id = str(entry.get("workflow_id", ""))
            if workflow_id and self.temporal.has_workflow(workflow_id):
                continue
            raw = entry.get("event")
            raw = dict(raw) if isinstance(raw, dict) else {}
            task_number = raw.get("task_number")
            snapshot: dict[str, Any] | None = None
            if self.workhub_reader is not None and isinstance(task_number, int):
                snapshot = self.workhub_reader.read_task(task_number)
            status = str((snapshot or {}).get("status", ""))
            if status in TERMINAL_TASK_STATUSES:
                report.resolved_via_workhub.append(event_id)
                continue
            try:
                event = self._resolve(parse_event(raw))
            except (ValueError, _Deferred):
                report.still_pending.append(event_id)
                continue
            action = dataclasses.replace(build_action(event), mode="start")
            try:
                self.temporal.start_or_signal(action)
            except Exception:
                report.still_pending.append(event_id)
                continue
            report.requeued.append(event_id)
        return report
