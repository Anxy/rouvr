"""Hermetic Stage-6 tests: authority limits + event discipline."""

from backend.session_factory.events import EventLog
from backend.session_factory.personas import can_act


def test_authority_limits() -> None:
    assert can_act(role="planner", action="propose_decomposition") is True
    assert can_act(role="planner", action="execute_assignment") is False
    assert can_act(role="implementer", action="execute_assignment") is True
    assert can_act(role="implementer", action="accept") is False
    assert can_act(role="verifier", action="reject") is True
    assert can_act(role="verifier", action="execute_assignment") is False
    assert can_act(role="verifier", action="close") is False
    assert can_act(role="ghost", action="accept") is False
    assert can_act(role="planner", action="accept", version="v9") is False


def test_request_report_verdict_flow() -> None:
    log = EventLog()
    req = {"message_id": "m1", "trace_id": "t", "kind": "work_request",
           "request_id": "r1"}
    assert log.apply(req)[0]["effect"] == "requested"
    rep = {"message_id": "m2", "trace_id": "t", "kind": "work_report",
           "request_id": "r1"}
    assert log.apply(rep)[0]["effect"] == "recorded"
    ver = {"message_id": "m3", "trace_id": "t", "kind": "verdict",
           "request_id": "r1"}
    assert log.apply(ver)[0]["effect"] == "recorded"


def test_dedup_reorder_and_park() -> None:
    log = EventLog()
    rep = {"message_id": "m2", "trace_id": "t", "kind": "work_report",
           "request_id": "r1"}
    assert log.apply(rep)[0]["effect"] == "parked"
    assert log.apply(rep) == []
    assert log.apply({"message_id": "", "kind": "work_report"}) == []
    assert log.apply({"message_id": "mx", "kind": "weird"}) == []
    req = {"message_id": "m1", "trace_id": "t", "kind": "work_request",
           "request_id": "r1"}
    effects = [e["effect"] for e in log.apply(req)]
    assert effects == ["requested", "unparked"]
    assert log.apply(req) == []
