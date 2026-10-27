"""Every emitted event carries eventId/cycleId/sequence/timestamp, and a repeated
zero-change file event is never emitted twice."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


@pytest.mark.asyncio
async def test_events_carry_real_id_cycle_id_sequence_and_timestamp() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=False)
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": "Prove event schema",
            "task_class": "bounded_repair",
            "definition_of_done": ("tests pass",),
            "repository_id": "aix",
            "allowed_files": (),
        },
        idempotency_key="admit-1",
        expected_version=None,
    )
    objective_id = admit["objective_id"]
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=objective_id,
        body={"candidate_sha": "abc1234", "budgets": {}},
        idempotency_key="start-1",
        expected_version=None,
    )
    cycle_id = started["cycle_id"]

    events_res = await backend.query(
        operation="get_events", tenant_id="tenant-1", resource_id=cycle_id, child_id=None, filters={}
    )
    events = events_res["events"]
    assert len(events) >= 1
    seen_ids = set()
    for idx, event in enumerate(events, start=1):
        assert event["event_id"], "every event must carry a real eventId"
        assert event["event_id"] not in seen_ids, "eventId must be unique"
        seen_ids.add(event["event_id"])
        assert event["cycle_id"] == cycle_id
        assert event["sequence"] == idx, "sequence must be monotonic starting at 1"
        assert event["sequence_number"] == idx
        assert event["occurred_at"], "every event must carry a real timestamp"
        assert event["trace_id"].startswith("trc_")
        assert event["objective_id"] == objective_id
        assert event["event_version"] == "1.0"
        assert event["component"] == "objective-cycle"
        assert event["status"]

    assert {event["event_type"] for event in events}.issuperset(
        {"request.received", "intent.classified", "objective.created", "cycle.started"}
    )


def _backend_with_cycle() -> tuple[DurableObjectiveCycleApiBackend, object]:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=False)
    from backend.aix_core.runtime.objective.durable_api_backend import _CycleRecord

    cycle = _CycleRecord(
        cycle_id="cyc_dedup1",
        objective_id="obj_dedup1",
        tenant_id="tenant-1",
        candidate_sha="",
        budgets={},
    )
    backend._cycles[cycle.cycle_id] = cycle  # noqa: SLF001 - direct unit test of _emit_event
    return backend, cycle


def test_repeated_zero_change_file_event_is_deduped() -> None:
    backend, cycle = _backend_with_cycle()
    backend._emit_event(  # noqa: SLF001
        cycle=cycle, event_type="tool.git_diff", payload_refs={"path": "config.py", "additions": 0, "deletions": 0}
    )
    backend._emit_event(  # noqa: SLF001
        cycle=cycle, event_type="tool.git_diff", payload_refs={"path": "config.py", "additions": 0, "deletions": 0}
    )
    assert len(cycle.events) == 1, "an identical zero-change file event must not be emitted twice"


def test_repeated_meaningful_change_event_is_not_deduped() -> None:
    backend, cycle = _backend_with_cycle()
    backend._emit_event(  # noqa: SLF001
        cycle=cycle, event_type="tool.git_diff", payload_refs={"path": "config.py", "additions": 1, "deletions": 0}
    )
    backend._emit_event(  # noqa: SLF001
        cycle=cycle, event_type="tool.git_diff", payload_refs={"path": "config.py", "additions": 1, "deletions": 0}
    )
    assert len(cycle.events) == 2, "a repeated event with a real (non-zero) diff must still be recorded"


def test_repeated_event_with_different_metadata_is_not_deduped() -> None:
    backend, cycle = _backend_with_cycle()
    backend._emit_event(  # noqa: SLF001
        cycle=cycle, event_type="tool.file_read", payload_refs={"path": "config.py", "size_bytes": 100}
    )
    backend._emit_event(  # noqa: SLF001
        cycle=cycle, event_type="tool.file_read", payload_refs={"path": "config.py", "size_bytes": 105}
    )
    assert len(cycle.events) == 2, "a meaningfully different repeat (e.g. size changed) must still be recorded"


def test_unrelated_events_are_never_deduped_against_each_other() -> None:
    backend, cycle = _backend_with_cycle()
    backend._emit_event(cycle=cycle, event_type="discovery.started", payload_refs={})  # noqa: SLF001
    backend._emit_event(cycle=cycle, event_type="discovery.completed", payload_refs={})  # noqa: SLF001
    assert len(cycle.events) == 2
    assert [e["sequence"] for e in cycle.events] == [1, 2]
