"""Unit tests for durable phase cursor + failure classes."""
from rouvr.contracts import AssignResult, DeliveryCursor, TestResult, VerifyResult
from rouvr.phase_cursor import (assign_from_cursor, cursor_after_build, cursor_after_test,
                                cursor_after_verify, cursor_from_evidence, failure_class,
                                hydrate_tests, last_failure_class, resume_at)


def test_failure_class_infra_product_policy():
    assert failure_class("E_EVIDENCE") == "infra"
    assert failure_class("E_DOWNSTREAM") == "infra"
    assert failure_class("rework_exhausted") == "product"
    assert failure_class("Tests failed:\nok") == "product"
    assert failure_class("E_CANCELLED") == "policy"
    assert failure_class("rejected_by_reviewer") == "policy"
    assert failure_class("E_AUTHZ") == "policy"


def test_resume_at_phases():
    assert resume_at(None) == "build"
    assert resume_at(DeliveryCursor()) == "build"
    assert resume_at(DeliveryCursor(phase="build_done")) == "test"
    assert resume_at(DeliveryCursor(phase="test_passed")) == "verify"
    assert resume_at(DeliveryCursor(phase="verify_approved")) == "close"
    assert resume_at(DeliveryCursor(phase="closed")) == "build"


def test_cursor_from_evidence_takes_latest():
    rows = [
        {"kind": "delivery_cursor", "body": {"phase": "build_done", "attempt_id": "a1",
                                             "head_sha": "s1", "lease_epoch": 1}},
        {"kind": "delivery_cursor", "body": {"phase": "test_passed", "attempt_id": "a1",
                                             "head_sha": "s1", "lease_epoch": 1,
                                             "test_run_id": "tr", "test_evidence_id": "ev"}},
    ]
    c = cursor_from_evidence(rows)
    assert c.phase == "test_passed"
    assert resume_at(c) == "verify"
    assert assign_from_cursor(c).attempt_id == "a1"
    assert hydrate_tests(c).run_id == "tr"


def test_cursor_cleared_is_build():
    rows = [{"kind": "delivery_cursor", "body": {"phase": ""}}]
    assert resume_at(cursor_from_evidence(rows)) == "build"


def test_cursor_builders():
    built = AssignResult("att1", 3, "sess", "rouvr/1", "https://pr", "sha", ["e1"])
    tests = TestResult("tr", True, "ok", "tev")
    v = VerifyResult("approve", "lgtm", "rev", "vev")
    assert cursor_after_build(1, built).phase == "build_done"
    assert cursor_after_test(1, built, tests).phase == "test_passed"
    assert cursor_after_verify(1, built, tests, v).phase == "verify_approved"


def test_last_failure_class():
    rows = [
        {"kind": "escalation", "body": {"failure_class": "infra", "reason": "E_EVIDENCE"}},
        {"kind": "escalation", "body": {"failure_class": "product", "reason": "rework_exhausted"}},
    ]
    assert last_failure_class(rows) == "product"
