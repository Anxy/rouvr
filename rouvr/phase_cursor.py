"""rouvr/phase_cursor.py — durable delivery phase cursor + failure classes.

Cursor phases (successful checkpoints only):
  build_done → test_passed → verify_approved → closed

resume_at maps the latest cursor to the next unfinished phase.
Product rework clears the cursor so Retry rebuilds; infra blocks leave it so
Retry re-enters at the failed phase.
"""
from __future__ import annotations

import json
from typing import Literal

from rouvr.contracts import AssignResult, DeliveryCursor, TestResult, VerifyResult
from rouvr.errors import Code

FailureClass = Literal["infra", "product", "policy"]
ResumeAt = Literal["build", "test", "verify", "close"]

CURSOR_KIND = "delivery_cursor"
CURSOR_CLEARED = ""

# Error type / block reason → class. Unknown reasons default to product so we
# never silently treat a mystery block as free infra retries.
_INFRA_REASONS = {
    Code.EVIDENCE.value,
    Code.DOWNSTREAM.value,
    Code.TRANSPORT.value,
    Code.THROTTLE.value,
    "E_EVIDENCE",
    "E_DOWNSTREAM",
    "E_TRANSPORT",
    "E_THROTTLE",
}
_POLICY_REASONS = {
    Code.AUTHZ.value,
    Code.CONTRACT.value,
    Code.CONTRACT_MOVED.value,
    Code.CANCELLED.value,
    Code.SCOPE.value,
    Code.INELIGIBLE.value,
    Code.SELF_REVIEW.value,
    Code.BAD_REQUEST.value,
    Code.MOVED.value,
    "E_CANCELLED",
    "E_AUTHZ",
    "E_CONTRACT",
    "E_CONTRACT_MOVED",
    "E_SCOPE",
    "E_INELIGIBLE",
    "E_SELF_REVIEW",
    "E_BAD_REQUEST",
    "E_MOVED",
    "rejected_by_reviewer",
}
_PRODUCT_REASONS = {
    "rework_exhausted",
}


def failure_class(reason: str) -> FailureClass:
    """Classify a block/escalate reason for rework accounting + UI."""
    r = (reason or "").strip()
    if r in _INFRA_REASONS or r.startswith("E_EVIDENCE"):
        return "infra"
    if r in _POLICY_REASONS or r.startswith("E_AUTHZ") or r.startswith("E_CONTRACT"):
        return "policy"
    if r in _PRODUCT_REASONS or r.startswith("Tests failed"):
        return "product"
    # ApplicationError types often arrive bare (E_*); treat unknown E_ as infra
    # only for evidence/downstream family already listed; else product.
    if r.startswith("E_") and r in _INFRA_REASONS:
        return "infra"
    return "product"


def resume_at(cursor: DeliveryCursor | None) -> ResumeAt:
    """Next phase a Retry should enter given the durable cursor."""
    if cursor is None or not cursor.phase:
        return "build"
    if cursor.phase == "verify_approved":
        return "close"
    if cursor.phase == "test_passed":
        return "verify"
    if cursor.phase == "build_done":
        return "test"
    if cursor.phase == "closed":
        return "build"  # done cursor: only Restart/force should start again
    return "build"


def cursor_from_evidence(rows: list[dict]) -> DeliveryCursor:
    """Latest delivery_cursor evidence body, or empty cursor if none/cleared."""
    latest: DeliveryCursor | None = None
    for e in rows or []:
        if e.get("kind") != CURSOR_KIND and e.get("evidence_type") != CURSOR_KIND:
            continue
        body = e.get("body")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                continue
        if not isinstance(body, dict):
            continue
        latest = DeliveryCursor(
            phase=str(body.get("phase") or ""),
            attempt_no=int(body.get("attempt_no") or 0),
            attempt_id=str(body.get("attempt_id") or ""),
            lease_epoch=int(body.get("lease_epoch") or 0),
            muse_session_id=str(body.get("muse_session_id") or ""),
            branch=str(body.get("branch") or ""),
            pr_url=str(body.get("pr_url") or ""),
            head_sha=str(body.get("head_sha") or ""),
            evidence_ids=list(body.get("evidence_ids") or []),
            test_run_id=str(body.get("test_run_id") or ""),
            test_summary=str(body.get("test_summary") or ""),
            test_evidence_id=str(body.get("test_evidence_id") or ""),
            test_failed_output=str(body.get("test_failed_output") or ""),
            verify_verdict=str(body.get("verify_verdict") or ""),
            verify_notes=str(body.get("verify_notes") or ""),
            verify_reviewer=str(body.get("verify_reviewer") or ""),
            verify_evidence_id=str(body.get("verify_evidence_id") or ""),
        )
    if latest is None or not latest.phase:
        return DeliveryCursor()
    return latest


def cursor_payload(cursor: DeliveryCursor) -> dict:
    return {
        "phase": cursor.phase,
        "attempt_no": cursor.attempt_no,
        "attempt_id": cursor.attempt_id,
        "lease_epoch": cursor.lease_epoch,
        "muse_session_id": cursor.muse_session_id,
        "branch": cursor.branch,
        "pr_url": cursor.pr_url,
        "head_sha": cursor.head_sha,
        "evidence_ids": list(cursor.evidence_ids or []),
        "test_run_id": cursor.test_run_id,
        "test_summary": cursor.test_summary,
        "test_evidence_id": cursor.test_evidence_id,
        "test_failed_output": cursor.test_failed_output,
        "verify_verdict": cursor.verify_verdict,
        "verify_notes": cursor.verify_notes,
        "verify_reviewer": cursor.verify_reviewer,
        "verify_evidence_id": cursor.verify_evidence_id,
    }


def assign_from_cursor(cursor: DeliveryCursor) -> AssignResult | None:
    if not cursor.attempt_id or not cursor.head_sha:
        return None
    return AssignResult(
        attempt_id=cursor.attempt_id,
        lease_epoch=cursor.lease_epoch,
        muse_session_id=cursor.muse_session_id,
        branch=cursor.branch,
        pr_url=cursor.pr_url,
        head_sha=cursor.head_sha,
        evidence_ids=list(cursor.evidence_ids or []),
    )


def hydrate_tests(cursor: DeliveryCursor) -> TestResult | None:
    if not cursor.test_run_id or not cursor.test_evidence_id:
        return None
    return TestResult(
        run_id=cursor.test_run_id,
        passed=True,
        summary=cursor.test_summary,
        evidence_id=cursor.test_evidence_id,
        failed_output=cursor.test_failed_output,
    )


def verify_from_cursor(cursor: DeliveryCursor) -> VerifyResult | None:
    if cursor.verify_verdict != "approve" or not cursor.verify_evidence_id:
        return None
    return VerifyResult(
        verdict="approve",  # type: ignore[arg-type]
        notes=cursor.verify_notes,
        reviewer_principal=cursor.verify_reviewer,
        evidence_id=cursor.verify_evidence_id,
    )


def cursor_after_build(attempt_no: int, built: AssignResult) -> DeliveryCursor:
    return DeliveryCursor(
        phase="build_done",
        attempt_no=attempt_no,
        attempt_id=built.attempt_id,
        lease_epoch=built.lease_epoch,
        muse_session_id=built.muse_session_id,
        branch=built.branch,
        pr_url=built.pr_url,
        head_sha=built.head_sha,
        evidence_ids=list(built.evidence_ids or []),
    )


def cursor_after_test(attempt_no: int, built: AssignResult, tests: TestResult) -> DeliveryCursor:
    c = cursor_after_build(attempt_no, built)
    c.phase = "test_passed"
    c.test_run_id = tests.run_id
    c.test_summary = tests.summary
    c.test_evidence_id = tests.evidence_id
    c.test_failed_output = tests.failed_output
    return c


def cursor_after_verify(attempt_no: int, built: AssignResult, tests: TestResult,
                        v: VerifyResult) -> DeliveryCursor:
    c = cursor_after_test(attempt_no, built, tests)
    c.phase = "verify_approved"
    c.verify_verdict = v.verdict
    c.verify_notes = v.notes
    c.verify_reviewer = v.reviewer_principal
    c.verify_evidence_id = v.evidence_id
    return c


def last_failure_class(rows: list[dict]) -> str:
    """Latest escalation evidence failure_class, if any."""
    found = ""
    for e in rows or []:
        if e.get("kind") != "escalation" and e.get("evidence_type") != "escalation":
            continue
        body = e.get("body")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                continue
        if isinstance(body, dict) and body.get("failure_class"):
            found = str(body["failure_class"])
    return found
