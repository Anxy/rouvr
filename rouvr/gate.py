"""rouvr/gate.py — deterministic submit gate (R7, NOT in the scaffold).

The only way to finish: the coder submits {pr_url, claims}; the gate checks
system-observable facts only and returns PASS or exactly one line of
machine-readable feedback. Three rejects on one attempt => blocked.

Transport: the roadmap names an MCP tool `submit_result(pr_url, claims)`, but R2
found no exec-time MCP surface in `muse exec`, so the submission travels as
`submit.json` in the workspace root (schema below) and the launcher calls
submit_result() on its content. Same function, same semantics; registering it
as a real MCP tool later changes no gate logic.

submit.json schema:
    {"pr_url": "https://.../pull/N",
     "claims": [{"criterion_id": "c1"}, ...]}
The coder claims criteria; the R8 evidence refs are system-known (the
coder writes submit.json before tests run, so it cannot cite them).

Round counting is evidence-backed: each judged submission is recorded as a
`submit_attempt` evidence row by the caller; count_prior_submits() reads them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from rouvr.errors import Code, RouvrError

MAX_SUBMIT_ROUNDS = 3
SUBMIT_FILE = "submit.json"
SUBMIT_ATTEMPT_TYPE = "submit_attempt"


@dataclass(frozen=True)
class GateEvidence:
    """System-observed facts. The caller collects these; any read error must be
    mapped to reject by the caller (fail closed) before calling evaluate()."""
    expected_branch: str
    pr_url: str = ""
    pr_head_branch: str = ""
    ci_status: str = ""            # "success" passes
    criteria: tuple[str, ...] = ()  # acceptance criterion ids, in order
    evidence_refs: frozenset[str] = field(default_factory=frozenset)  # R8/R9 refs
    prior_submits: int = 0


@dataclass(frozen=True)
class GateVerdict:
    verdict: str                   # "approved" | "reject" | "blocked" | "failed"
    feedback: str | None = None    # exactly one line unless approved/blocked


def submit_result(pr_url: str, claims: list[dict], *, prior_rounds: int) -> dict:
    """The submit tool body: validate one submission (raises CONTRACT when malformed)."""
    if not isinstance(pr_url, str) or not pr_url.startswith("https://"):
        raise RouvrError(Code.CONTRACT, "submit_result needs an https pr_url")
    if not isinstance(claims, list) or not claims:
        raise RouvrError(Code.CONTRACT, "submit_result needs a non-empty claims list")
    for c in claims:
        if not isinstance(c, dict) or not c.get("criterion_id"):
            raise RouvrError(Code.CONTRACT, "each claim needs a criterion_id")
    return {"round": prior_rounds + 1,
            "submission": {"pr_url": pr_url, "claims": claims}}


def read_submission_file(workspace: Path) -> dict | None:
    """None when the coder never submitted (exit-without-submit => failed)."""
    path = workspace / SUBMIT_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return {"__malformed__": True}
    return data if isinstance(data, dict) else {"__malformed__": True}


def count_prior_submits(evidence_rows: list[dict]) -> int:
    return sum(1 for r in evidence_rows if r.get("type") == SUBMIT_ATTEMPT_TYPE)


def evaluate(submission: dict | None, facts: GateEvidence) -> GateVerdict:
    """Total function: never raises on bad input, returns reject/blocked instead."""
    try:
        return _judge(submission, facts)
    except RouvrError as e:
        return GateVerdict("reject", f"GATE_READ_ERROR fix=check-gate-inputs detail={e.code}")
    except Exception as e:  # fail closed on anything unexpected
        return GateVerdict("reject", f"GATE_READ_ERROR fix=check-gate-inputs detail={type(e).__name__}")


def _judge(submission: dict | None, facts: GateEvidence) -> GateVerdict:
    if facts.prior_submits >= MAX_SUBMIT_ROUNDS:
        return GateVerdict("blocked", None)
    if submission is None:
        return GateVerdict("failed", "NO_SUBMIT fix=write-submit.json criterion_id=*")
    if submission.get("__malformed__"):
        return GateVerdict("reject", "BAD_SUBMIT fix=write-valid-submit.json criterion_id=*")
    try:
        checked = submit_result(submission.get("pr_url", ""), submission.get("claims", []),
                                prior_rounds=facts.prior_submits)
    except RouvrError:
        return GateVerdict("reject", "BAD_SUBMIT fix=write-valid-submit.json criterion_id=*")
    if checked["submission"]["pr_url"] != facts.pr_url or facts.pr_head_branch != facts.expected_branch:
        return GateVerdict("reject", f"PR_NOT_ON_BRANCH fix=push-pr-to-{facts.expected_branch} "
                                     f"criterion_id=*")
    if facts.ci_status != "success":
        return GateVerdict("reject", "CI_NOT_GREEN fix=make-ci-pass criterion_id=*")
    if not facts.evidence_refs:
        return GateVerdict("reject", "NO_R8_EVIDENCE fix=wait-for-test-run criterion_id=*")
    claimed = {c["criterion_id"] for c in checked["submission"]["claims"]}
    for cid in facts.criteria:
        if cid not in claimed:
            return GateVerdict("reject", f"CRITERION_UNCLAIMED fix=claim-each-criterion criterion_id={cid}")
    return GateVerdict("approved", None)
