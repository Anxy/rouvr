"""Hermetic Stage-7 tests: operational snapshots."""

from backend.session_factory.ops import (
    attempt_summary,
    queue_snapshot,
    worker_snapshot,
)


def test_worker_snapshot_idle_and_active() -> None:
    idle = worker_snapshot(unit_id="u", lane_id="l", locks_owned=[], active_attempt=None)
    assert idle["idle"] is True
    active = worker_snapshot(
        unit_id="u", lane_id="l", locks_owned=["b.py", "a.py"],
        active_attempt={"attempt_id": "a1"},
    )
    assert active["idle"] is False
    assert active["locks_owned"] == ["a.py", "b.py"]


def test_queue_delays() -> None:
    snap = queue_snapshot(waiting=[("u1", 3.0), ("u2", 9.5)])
    assert snap["depth"] == 2
    assert snap["longest_waiter"] == "u2"
    assert snap["longest_wait_seconds"] == 9.5
    empty = queue_snapshot(waiting=[])
    assert empty["depth"] == 0 and empty["longest_waiter"] is None


def test_attempt_summary() -> None:
    summary = attempt_summary([
        {"status": "VERIFIED", "revision": 1},
        {"status": "BLOCKED", "revision": 2},
        {"status": "VERIFIED"},
    ])
    assert summary["total"] == 3
    assert summary["by_status"] == {"VERIFIED": 2, "BLOCKED": 1}
    assert summary["max_revision"] == 2
