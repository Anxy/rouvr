"""DW1-G0-T02: bounded reviewer capability for one supervised verifier.

Compiles review context from the canonical assignment record and candidate
records, binds one deterministic reviewer attempt identity to exact
task/assignment/attempt/candidate/evidence digests, and delivers an
Auth-issued capability token to exactly one supervised verifier process
without persisting it or leaking it into argv, environment, or logs.

Out of scope (see T03/T04 and MEP4): issuing Auth grants, creating lifecycle
truth outside WorkHub, passing Lead credentials, automatic task completion.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field


class ReviewerAdmissionRejected(ValueError):
    """Raised when a reviewer launch context fails closed."""


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReviewContext:
    """Compiled review context, bound to exact digests."""

    task_id: str
    assignment_id: str
    assignment_revision: str
    attempt_id: str
    candidate_id: str
    candidate_digest: str
    evidence_digests: tuple[str, ...] = ()
    tenant_id: str = ""
    context_digest: str = ""

    def __post_init__(self) -> None:
        digest = _digest(
            _canonical(
                {
                    "task_id": self.task_id,
                    "assignment_id": self.assignment_id,
                    "assignment_revision": self.assignment_revision,
                    "attempt_id": self.attempt_id,
                    "candidate_id": self.candidate_id,
                    "candidate_digest": self.candidate_digest,
                    "evidence_digests": list(self.evidence_digests),
                    "tenant_id": self.tenant_id,
                }
            )
        )
        object.__setattr__(self, "context_digest", digest)


def compile_review_context(
    *,
    assignment: dict,
    candidate: dict,
    evidence_digests: list[str] | tuple[str, ...] = (),
    tenant_id: str = "",
) -> ReviewContext:
    """Compile context from canonical assignment + candidate records.

    Raises ReviewerAdmissionRejected when required identity fields are
    missing or blank: fail closed, never launch on partial context.
    """
    for key in ("task_id", "assignment_id", "assignment_revision", "attempt_id"):
        if not str(assignment.get(key) or "").strip():
            raise ReviewerAdmissionRejected(
                f"assignment record missing {key}: refusing context"
            )
    if not str(candidate.get("candidate_id") or "").strip():
        raise ReviewerAdmissionRejected("candidate record missing candidate_id")
    if not str(candidate.get("candidate_digest") or "").strip():
        raise ReviewerAdmissionRejected("candidate record missing candidate_digest")
    return ReviewContext(
        task_id=str(assignment["task_id"]),
        assignment_id=str(assignment["assignment_id"]),
        assignment_revision=str(assignment["assignment_revision"]),
        attempt_id=str(assignment["attempt_id"]),
        candidate_id=str(candidate["candidate_id"]),
        candidate_digest=str(candidate["candidate_digest"]),
        evidence_digests=tuple(str(d) for d in evidence_digests),
        tenant_id=str(tenant_id or ""),
    )


@dataclass
class BoundedCapability:
    """In-memory capability: token bytes live here and nowhere else.

    Never serialized, never placed in argv/env/logs. `seal()` drops the
    token after delivery so a handle cannot be re-read.
    """

    context: ReviewContext
    scope: tuple[str, ...]
    expires_at: float
    _token: str = field(repr=False)
    _sealed: bool = field(default=False, repr=False)

    def attempt_id(self) -> str:
        return self.context.attempt_id

    def expired(self, *, now: float | None = None) -> bool:
        return (time.time() if now is None else now) >= self.expires_at

    def seal(self) -> None:
        object.__setattr__(self, "_token", "")
        object.__setattr__(self, "_sealed", True)

    @property
    def sealed(self) -> bool:
        return self._sealed


def bind_capability(
    *,
    context: ReviewContext,
    token: str,
    scope: list[str] | tuple[str, ...],
    ttl_seconds: int = 600,
    now: float | None = None,
) -> BoundedCapability:
    """Bind an Auth-issued token to one attempt. Does NOT issue grants."""
    if not str(token or "").strip():
        raise ReviewerAdmissionRejected("refusing to bind blank capability token")
    if not scope:
        raise ReviewerAdmissionRejected("refusing to bind empty capability scope")
    if ttl_seconds <= 0:
        raise ReviewerAdmissionRejected("refusing non-positive capability TTL")
    base = time.time() if now is None else now
    return BoundedCapability(
        context=context,
        scope=tuple(str(s) for s in scope),
        expires_at=base + ttl_seconds,
        _token=str(token),
    )


@dataclass
class CapabilityRegistry:
    """Tracks issued attempt bindings + revocations for admission."""

    issued: dict[str, str] = field(default_factory=dict)
    revoked: set[str] = field(default_factory=set)

    def register(self, capability: BoundedCapability) -> None:
        self.issued[capability.attempt_id()] = capability.context.context_digest

    def revoke(self, attempt_id: str) -> None:
        self.revoked.add(attempt_id)

    def admit(
        self,
        *,
        context: ReviewContext,
        capability: BoundedCapability,
        now: float | None = None,
    ) -> str:
        """Admit one launch or raise. Every mismatch fails closed."""
        if capability.sealed:
            raise ReviewerAdmissionRejected("capability already sealed: refusing")
        if capability.expired(now=now):
            raise ReviewerAdmissionRejected("capability expired: refusing")
        if context.attempt_id in self.revoked:
            raise ReviewerAdmissionRejected("attempt revoked: refusing")
        expected = self.issued.get(context.attempt_id)
        if expected is None:
            raise ReviewerAdmissionRejected("attempt never registered: refusing")
        if expected != context.context_digest:
            raise ReviewerAdmissionRejected(
                "context digest mismatch (wrong/stale assignment or candidate): refusing"
            )
        if capability.context.context_digest != context.context_digest:
            raise ReviewerAdmissionRejected("capability bound to another context")
        return context.attempt_id


@dataclass
class AttributionRow:
    attempt_id: str
    event: str
    at: float
    detail_digest: str


class AttributionLog:
    """Start/exit/effect attribution bound to one attempt. Token-free."""

    def __init__(self) -> None:
        self.rows: list[AttributionRow] = []

    def record(self, *, attempt_id: str, event: str, detail: str = "") -> AttributionRow:
        row = AttributionRow(
            attempt_id=attempt_id,
            event=event,
            at=time.time(),
            detail_digest=_digest(detail),
        )
        self.rows.append(row)
        return row

    def for_attempt(self, attempt_id: str) -> list[AttributionRow]:
        return [r for r in self.rows if r.attempt_id == attempt_id]


def supervised_launch_argv(*, reviewer_module: str, attempt_id: str) -> list[str]:
    """Build the verifier subprocess argv. The token is NEVER in argv:
    delivery travels over stdin only (see deliver_over_stdin)."""
    return [
        sys.executable,
        "-m",
        reviewer_module,
        "--attempt",
        attempt_id,
    ]


def deliver_over_stdin(
    argv: list[str],
    *,
    token: str,
    env: dict[str, str] | None = None,
    timeout_seconds: int = 60,
) -> subprocess.CompletedProcess[str]:
    """Run the supervised verifier with the token on stdin, sealed env.

    The child inherits an env scrubbed of the token; stdout/stderr are
    captured for leak inspection by the caller.
    """
    child_env = dict(env if env is not None else os.environ)
    for key, value in child_env.items():
        if token and token in value:
            raise ReviewerAdmissionRejected(
                f"refusing launch: token present in env {key}"
            )
    if any(token and token in part for part in argv):
        raise ReviewerAdmissionRejected("refusing launch: token present in argv")
    return subprocess.run(
        argv,
        input=token,
        capture_output=True,
        text=True,
        env=child_env,
        timeout=timeout_seconds,
    )


def scan_for_leak(*, token: str, surfaces: dict[str, str]) -> list[str]:
    """Return surface names containing the token. Empty means clean."""
    if not token:
        return []
    return [name for name, text in surfaces.items() if token in (text or "")]


__all__ = [
    "AttributionLog",
    "AttributionRow",
    "BoundedCapability",
    "CapabilityRegistry",
    "ReviewContext",
    "ReviewerAdmissionRejected",
    "bind_capability",
    "compile_review_context",
    "deliver_over_stdin",
    "scan_for_leak",
    "supervised_launch_argv",
]
