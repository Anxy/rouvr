"""Focused tests for the maintained AIX event consumer (#119).

Slice contract (smallest that unblocks #72 first):
- One WorkHub/OpsBus event in -> exactly one durable Temporal action out,
  on queue ``aix.execution.current``.
- Event-ID dedupe: replays and reordered redeliveries never double-act.
- Restart-safe: a new consumer on the same durable cursor skips processed ids.
- Admission seam: inadmissible events are rejected with no Temporal action.
- Recovery pass: processed-but-unknown Temporal handles are requeued by
  reconciling the durable cursor against WorkHub state vs Temporal handles.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import signal
from pathlib import Path

import pytest

from backend.aix_event_consumer.admission import DefaultAdmissionPolicy
from backend.aix_event_consumer.consumer import AixEventConsumer, DictEventSource
from backend.aix_event_consumer.cursor import DurableCursor
from backend.aix_event_consumer.events import parse_event
from backend.aix_event_consumer.temporal import (
    EXECUTION_QUEUE,
    WORKFLOW_TYPE,
    InMemoryTemporalDouble,
    build_action,
    deterministic_workflow_id,
)


class _DurableReceiverDouble(InMemoryTemporalDouble):
    """Process-restart double for Temporal's history-backed event inbox."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        if path.is_file():
            state = json.loads(path.read_text())
            self.starts = state["starts"]
            self.signals = state["signals"]
            self._received_event_ids = set(state["received_event_ids"])

    def start_or_signal(self, action):  # type: ignore[no-untyped-def]
        result = super().start_or_signal(action)
        self.path.write_text(json.dumps({
            "starts": self.starts, "signals": self.signals,
            "received_event_ids": sorted(self._received_event_ids),
        }))
        return result


def _crash_after_temporal(receiver_path: str, cursor_path: str) -> None:
    temporal = _DurableReceiverDouble(Path(receiver_path))
    consumer = AixEventConsumer(
        event_source=DictEventSource([_event("ev-crash", seq=2)]),
        admission=DefaultAdmissionPolicy(), cursor=DurableCursor(cursor_path),
        temporal=temporal,
        after_temporal_action=lambda event: os.kill(os.getpid(), signal.SIGKILL),
    )
    consumer.run_once()

PLAN = "a" * 64
TENANT = "t-cycle-1"


def _event(event_id: str, seq: int = 0, **over: object) -> dict:
    payload: dict[str, object] = {
        "event_id": event_id,
        "event_type": "workhub.task.updated",
        "tenant_id": TENANT,
        "task_number": 119,
        "objective_id": "objective-1",
        "cycle_id": "cycle-1",
        "correlation_id": event_id,
        "candidate_sha": "b" * 40,
        "plan_sha256": PLAN,
        "seq": seq,
    }
    payload.update(over)
    return payload


def _consumer(tmp_path: Path, **over: object) -> AixEventConsumer:
    args: dict[str, object] = {
        "event_source": DictEventSource([]),
        "admission": DefaultAdmissionPolicy(),
        "cursor": DurableCursor(tmp_path / "cursor.json"),
        "temporal": InMemoryTemporalDouble(),
    }
    args.update(over)
    return AixEventConsumer(**args)  # type: ignore[arg-type]


def test_first_event_starts_exactly_one_temporal_action(tmp_path: Path) -> None:
    consumer = _consumer(tmp_path, event_source=DictEventSource([_event("ev-1")]))
    outcome = consumer.run_once()

    assert outcome.started == 1
    assert outcome.signalled == 0
    assert outcome.duplicates == 0
    temporal = consumer.temporal
    assert isinstance(temporal, InMemoryTemporalDouble)
    assert len(temporal.starts) == 1
    (workflow_id, record), = temporal.starts.items()
    assert record["task_queue"] == EXECUTION_QUEUE == "aix.execution.current"
    assert record["workflow_type"] == WORKFLOW_TYPE == "AixObjectiveCycleWorkflow"
    assert workflow_id == deterministic_workflow_id(
        tenant_id=TENANT, objective_id="objective-1", cycle_id="cycle-1"
    )


def test_duplicate_event_is_deduped(tmp_path: Path) -> None:
    consumer = _consumer(
        tmp_path, event_source=DictEventSource([_event("ev-1"), _event("ev-1")])
    )
    outcome = consumer.run_once()

    assert outcome.started == 1
    assert outcome.duplicates == 1
    assert len(consumer.temporal.starts) == 1  # type: ignore[attr-defined]


def test_kill_after_signal_before_cursor_commit_replay_is_receiver_idempotent(
    tmp_path: Path,
) -> None:
    receiver_path = tmp_path / "temporal-history.json"
    cursor_path = tmp_path / "cursor.json"
    temporal = _DurableReceiverDouble(receiver_path)
    first = AixEventConsumer(
        event_source=DictEventSource([_event("ev-start", seq=1)]),
        admission=DefaultAdmissionPolicy(), cursor=DurableCursor(cursor_path),
        temporal=temporal,
    )
    assert first.run_once().started == 1

    process = multiprocessing.get_context("fork").Process(
        target=_crash_after_temporal, args=(str(receiver_path), str(cursor_path))
    )
    process.start()
    process.join(10)
    assert process.exitcode == -signal.SIGKILL
    assert DurableCursor(cursor_path).is_processed("ev-crash") is False

    restarted_temporal = _DurableReceiverDouble(receiver_path)
    replay = AixEventConsumer(
        event_source=DictEventSource([_event("ev-crash", seq=2)]),
        admission=DefaultAdmissionPolicy(), cursor=DurableCursor(cursor_path),
        temporal=restarted_temporal,
    ).run_once()
    assert replay.duplicates == 1
    assert [item["event_id"] for item in restarted_temporal.signals] == ["ev-crash"]


def test_reordered_redelivery_is_deduped(tmp_path: Path) -> None:
    consumer = _consumer(
        tmp_path,
        event_source=DictEventSource(
            [_event("ev-2", seq=2), _event("ev-1", seq=1), _event("ev-2", seq=2)]
        ),
    )
    outcome = consumer.run_once()

    assert outcome.started == 1
    assert outcome.signalled == 1  # same workflow, second distinct event -> signal
    assert outcome.duplicates == 1


def test_restart_recovers_cursor_and_skips_processed(tmp_path: Path) -> None:
    cursor_path = tmp_path / "cursor.json"
    first = AixEventConsumer(
        event_source=DictEventSource([_event("ev-1")]),
        admission=DefaultAdmissionPolicy(),
        cursor=DurableCursor(cursor_path),
        temporal=InMemoryTemporalDouble(),
    )
    assert first.run_once().started == 1

    # Simulate a process restart: fresh consumer, same cursor file, empty Temporal.
    restarted = AixEventConsumer(
        event_source=DictEventSource([_event("ev-1"), _event("ev-2")]),
        admission=DefaultAdmissionPolicy(),
        cursor=DurableCursor(cursor_path),
        temporal=InMemoryTemporalDouble(),
    )
    outcome = restarted.run_once()

    assert outcome.duplicates == 1
    assert outcome.started == 1
    cursor_state = json.loads(cursor_path.read_text(encoding="utf-8"))
    assert set(cursor_state["processed"]) == {"ev-1", "ev-2"}


def test_inadmissible_event_rejected_without_temporal_action(tmp_path: Path) -> None:
    consumer = _consumer(
        tmp_path,
        admission=DefaultAdmissionPolicy(allowed_tenants=("other-tenant",)),
        event_source=DictEventSource([_event("ev-1")]),
    )
    outcome = consumer.run_once()

    assert outcome.rejected == 1
    assert outcome.started == 0
    assert outcome.signalled == 0
    assert len(consumer.temporal.starts) == 0  # type: ignore[attr-defined]
    assert consumer.cursor.is_processed("ev-1") is False  # type: ignore[attr-defined]


def test_malformed_event_fails_closed_without_cursor_advance(tmp_path: Path) -> None:
    consumer = _consumer(tmp_path, event_source=DictEventSource([{"no": "id"}]))
    outcome = consumer.run_once()

    assert outcome.malformed == 1
    assert outcome.started == 0
    with pytest.raises(ValueError, match="EVENT_ID_REQUIRED"):
        parse_event({})


def test_recovery_pass_requeues_processed_event_missing_in_temporal(
    tmp_path: Path,
) -> None:
    cursor_path = tmp_path / "cursor.json"
    temporal: InMemoryTemporalDouble = InMemoryTemporalDouble()
    consumer = AixEventConsumer(
        event_source=DictEventSource([_event("ev-1")]),
        admission=DefaultAdmissionPolicy(),
        cursor=DurableCursor(cursor_path),
        temporal=temporal,
    )
    assert consumer.run_once().started == 1

    # Crash window: cursor durable, Temporal handle lost (fresh double).
    lost = InMemoryTemporalDouble()
    consumer.temporal = lost  # type: ignore[assignment]
    report = consumer.recover()

    assert report.requeued == ["ev-1"]
    assert len(lost.starts) == 1
    # Second recovery is a no-op: handle now exists.
    assert consumer.recover().requeued == []


def test_terminal_workhub_state_resolves_without_temporal_action(
    tmp_path: Path,
) -> None:
    from backend.aix_event_consumer.consumer import MapWorkHubReader

    cursor_path = tmp_path / "cursor.json"
    consumer = AixEventConsumer(
        event_source=DictEventSource([_event("ev-1")]),
        admission=DefaultAdmissionPolicy(),
        cursor=DurableCursor(cursor_path),
        temporal=InMemoryTemporalDouble(),
        workhub_reader=MapWorkHubReader({119: {"status": "done", "record_version": 9}}),
    )
    assert consumer.run_once().started == 1

    consumer.temporal = InMemoryTemporalDouble()  # type: ignore[assignment]
    report = consumer.recover()

    assert report.requeued == []
    assert report.resolved_via_workhub == ["ev-1"]


def test_stale_projection_defers_without_advancing_cursor(tmp_path: Path) -> None:
    from backend.aix_event_consumer.consumer import JsonProjectionReader

    root = tmp_path / "projections"
    (root / "tasks" / "by-number").mkdir(parents=True)
    (root / "tasks" / "by-number" / "119.json").write_text(
        json.dumps({"status": "STALE", "data": None}), encoding="utf-8"
    )
    consumer = _consumer(
        tmp_path,
        event_source=DictEventSource([_event("ev-1")]),
        projection_reader=JsonProjectionReader(root=root, max_age_seconds=60.0),
    )
    outcome = consumer.run_once()

    assert outcome.deferred == 1
    assert outcome.started == 0
    assert consumer.cursor.is_processed("ev-1") is False  # type: ignore[attr-defined]


def test_action_ids_are_deterministic_per_event(tmp_path: Path) -> None:
    del tmp_path
    left = build_action(parse_event(_event("ev-9")))
    right = build_action(parse_event(_event("ev-9")))
    assert left.workflow_id == right.workflow_id
    assert left.idempotency_key == "ev-9"
    assert left.task_queue == "aix.execution.current"


def test_fresh_projection_resolves_missing_detail(tmp_path: Path) -> None:
    import time

    from backend.aix_event_consumer.consumer import JsonProjectionReader

    root = tmp_path / "projections"
    (root / "tasks" / "by-number").mkdir(parents=True)
    (root / "tasks" / "by-number" / "119.json").write_text(
        json.dumps(
            {
                "status": "OK",
                "data": {"objective_id": "objective-1", "cycle_id": "cycle-1"},
                "source_record_version": 9,
                "fetched_at_epoch": time.time(),
            }
        ),
        encoding="utf-8",
    )
    bare = _event("ev-1")
    del bare["objective_id"], bare["cycle_id"]
    consumer = _consumer(
        tmp_path,
        event_source=DictEventSource([bare]),
        projection_reader=JsonProjectionReader(root=root, max_age_seconds=60.0),
    )
    outcome = consumer.run_once()

    assert outcome.started == 1
    assert outcome.deferred == 0
