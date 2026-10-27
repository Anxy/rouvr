"""Hermetic Stage-3 tests: admission bound + wait/block gate."""

from backend.session_factory.schedule import admit, gate_status


def test_admit_capacity_and_quota() -> None:
    assert admit(active_count=0, capacity=2, quota_left=5) is True
    assert admit(active_count=2, capacity=2, quota_left=5) is False
    assert admit(active_count=0, capacity=2, quota_left=0) is False


def test_gate_ready_waiting_blocked() -> None:
    deps = [("child", "p1"), ("child", "p2")]
    assert gate_status({"child": "PENDING", "p1": "SUCCEEDED", "p2": "RUNNING"}, deps, "child") == "waiting"
    assert gate_status({"child": "PENDING", "p1": "SUCCEEDED", "p2": "SUCCEEDED"}, deps, "child") == "ready"
    assert gate_status({"child": "PENDING", "p1": "SUCCEEDED", "p2": "FAILED"}, deps, "child") == "blocked"
    assert gate_status({"child": "PENDING", "p1": "SUCCEEDED", "p2": "CANCELLED"}, deps, "child") == "blocked"


def test_gate_unknown_unit_and_missing_parent_wait() -> None:
    assert gate_status({}, [], "ghost") == "unknown-unit"
    assert gate_status({"c": "PENDING"}, [("c", "missing")], "c") == "waiting"
