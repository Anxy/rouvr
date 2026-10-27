"""DW1-G0-T02: contract + adapter tests for the bounded reviewer capability."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

from backend.aix_core.reviewer_capability import (
    AttributionLog,
    CapabilityRegistry,
    ReviewerAdmissionRejected,
    bind_capability,
    compile_review_context,
    deliver_over_stdin,
    scan_for_leak,
    supervised_launch_argv,
)

TOKEN = "cap-test-token-9f8e7d6c5b4a39482716"
ASSIGNMENT = {
    "task_id": "lane-ops-95",
    "assignment_id": "assign-001",
    "assignment_revision": "r3",
    "attempt_id": "attempt-001",
}
CANDIDATE = {"candidate_id": "cand-7", "candidate_digest": "digest-abc123"}


def _context(**over):
    assignment = dict(ASSIGNMENT)
    candidate = dict(CANDIDATE)
    assignment.update(over.pop("assignment", {}))
    candidate.update(over.pop("candidate", {}))
    return compile_review_context(
        assignment=assignment,
        candidate=candidate,
        evidence_digests=over.pop("evidence_digests", ["ev-1", "ev-2"]),
        tenant_id=over.pop("tenant_id", "example-tenant"),
    )


def test_context_digest_is_deterministic():
    assert _context().context_digest == _context().context_digest


def test_context_digest_changes_with_candidate():
    assert _context().context_digest != _context(
        candidate={"candidate_digest": "digest-other"}
    ).context_digest


def test_compile_rejects_partial_records():
    with pytest.raises(ReviewerAdmissionRejected):
        compile_review_context(assignment={}, candidate=CANDIDATE)
    with pytest.raises(ReviewerAdmissionRejected):
        compile_review_context(assignment=ASSIGNMENT, candidate={"candidate_id": "x"})
    with pytest.raises(ReviewerAdmissionRejected):
        compile_review_context(
            assignment=dict(ASSIGNMENT, attempt_id="  "), candidate=CANDIDATE
        )


def test_bind_rejects_blank_token_scope_ttl():
    ctx = _context()
    with pytest.raises(ReviewerAdmissionRejected):
        bind_capability(context=ctx, token="  ", scope=["review:read"])
    with pytest.raises(ReviewerAdmissionRejected):
        bind_capability(context=ctx, token=TOKEN, scope=[])
    with pytest.raises(ReviewerAdmissionRejected):
        bind_capability(context=ctx, token=TOKEN, scope=["review:read"], ttl_seconds=0)


def test_register_admit_happy_path():
    reg = CapabilityRegistry()
    ctx = _context()
    cap = bind_capability(context=ctx, token=TOKEN, scope=["review:read"])
    reg.register(cap)
    assert reg.admit(context=ctx, capability=cap) == "attempt-001"


def test_admit_rejects_unregistered():
    reg = CapabilityRegistry()
    ctx = _context()
    cap = bind_capability(context=ctx, token=TOKEN, scope=["review:read"])
    with pytest.raises(ReviewerAdmissionRejected):
        reg.admit(context=ctx, capability=cap)


def test_admit_rejects_stale_context():
    reg = CapabilityRegistry()
    ctx = _context()
    reg.register(bind_capability(context=ctx, token=TOKEN, scope=["review:read"]))
    stale = _context(candidate={"candidate_digest": "digest-stale"})
    cap = bind_capability(context=stale, token=TOKEN, scope=["review:read"])
    with pytest.raises(ReviewerAdmissionRejected):
        reg.admit(context=stale, capability=cap)


def test_admit_rejects_expired_revoked_sealed():
    reg = CapabilityRegistry()
    ctx = _context()
    reg.register(bind_capability(context=ctx, token=TOKEN, scope=["review:read"]))
    expired = bind_capability(
        context=ctx, token=TOKEN, scope=["review:read"], ttl_seconds=600, now=1000.0
    )
    with pytest.raises(ReviewerAdmissionRejected):
        reg.admit(context=ctx, capability=expired, now=2000.0)
    reg.revoke("attempt-001")
    fresh = bind_capability(context=ctx, token=TOKEN, scope=["review:read"])
    with pytest.raises(ReviewerAdmissionRejected):
        reg.admit(context=ctx, capability=fresh)
    reg.revoked.clear()
    fresh.seal()
    with pytest.raises(ReviewerAdmissionRejected):
        reg.admit(context=ctx, capability=fresh)


def test_seal_drops_token_bytes():
    ctx = _context()
    cap = bind_capability(context=ctx, token=TOKEN, scope=["review:read"])
    cap.seal()
    assert cap.sealed is True
    assert TOKEN not in repr(cap)


def test_launch_argv_never_carries_token():
    argv = supervised_launch_argv(
        reviewer_module="tests.stub_reviewer_entry", attempt_id="attempt-001"
    )
    assert TOKEN not in " ".join(argv)
    assert "--attempt" in argv and "attempt-001" in argv


def test_supervised_launch_delivers_over_stdin_without_leak(caplog):
    stub = str(Path(__file__).with_name("stub_reviewer_entry.py"))
    argv = [sys.executable, stub, "--attempt", "attempt-001"]
    env = {"PATH": "/usr/bin:/bin", "ATTEMPT": "attempt-001"}
    proc = deliver_over_stdin(argv, token=TOKEN, env=env)
    assert proc.returncode == 0
    assert f"received_chars={len(TOKEN)}" in proc.stdout
    leaked = scan_for_leak(
        token=TOKEN,
        surfaces={
            "argv": " ".join(argv),
            "env": " ".join(f"{k}={v}" for k, v in env.items()),
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        },
    )
    assert leaked == []
    logger = logging.getLogger("reviewer.launch")
    with caplog.at_level(logging.INFO, logger="reviewer.launch"):
        logger.info("launched attempt=%s", "attempt-001")
    assert scan_for_leak(token=TOKEN, surfaces={"log": caplog.text}) == []


def test_deliver_refuses_token_in_argv_or_env():
    with pytest.raises(ReviewerAdmissionRejected):
        deliver_over_stdin(
            [sys.executable, "-m", "x", TOKEN], token=TOKEN, env={"PATH": "p"}
        )
    with pytest.raises(ReviewerAdmissionRejected):
        deliver_over_stdin(
            [sys.executable, "-m", "x"],
            token=TOKEN,
            env={"PATH": "p", "LEAK": f"prefix-{TOKEN}"},
        )


def test_attribution_rows_are_attempt_bound_and_token_free():
    log = AttributionLog()
    log.record(attempt_id="attempt-001", event="start", detail=TOKEN)
    log.record(attempt_id="attempt-001", event="exit", detail="rc=0")
    log.record(attempt_id="attempt-002", event="start")
    rows = log.for_attempt("attempt-001")
    assert [r.event for r in rows] == ["start", "exit"]
    assert TOKEN not in repr(rows)
