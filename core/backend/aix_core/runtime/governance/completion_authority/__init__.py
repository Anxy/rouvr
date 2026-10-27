"""C003: single Completion Authority (ADR-007).

Only this package may emit a terminal success CompletionDecision for the
canonical Objective Runtime. Alternate engines may produce adapter reports
but cannot mint canonical terminal success.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

COMPLETION_AUTHORITY_VERSION = "aix.completion_authority.v1"
CANONICAL_EMITTER = "backend.aix_core.runtime.governance.completion_authority.decide_completion"

CompletionVerdict = Literal[
    "PASS",
    "FAIL",
    "BLOCKED",
    "CANCELLED",
    "REJECTED",
    "INSUFFICIENT_EVIDENCE",
    "NON_CANONICAL",
]


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CompletionDecisionV2:
    """Pure completion decision over durable evidence references."""

    decision_id: str
    tenant_id: str
    objective_id: str
    run_id: str
    verdict: CompletionVerdict
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    approval_refs: tuple[str, ...] = ()
    budget_ok: bool = True
    emitter: str = CANONICAL_EMITTER
    authority_version: str = COMPLETION_AUTHORITY_VERSION
    decided_at: str = field(default_factory=_utc)
    replayable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_terminal_success(self) -> bool:
        return self.verdict == "PASS" and self.emitter == CANONICAL_EMITTER


class CompletionAuthorityError(ValueError):
    pass


_EMITTED: list[CompletionDecisionV2] = []


def reset_completion_audit_for_tests() -> None:
    _EMITTED.clear()


def emitted_decisions() -> tuple[CompletionDecisionV2, ...]:
    return tuple(_EMITTED)


def decide_completion(
    *,
    tenant_id: str,
    objective_id: str,
    run_id: str,
    verdict: CompletionVerdict,
    reasons: tuple[str, ...] | list[str],
    evidence_refs: tuple[str, ...] | list[str],
    approval_refs: tuple[str, ...] | list[str] | None = None,
    budget_ok: bool = True,
    decision_id: str | None = None,
    allow_success_without_evidence: bool = False,
) -> CompletionDecisionV2:
    """Sole canonical emitter of CompletionDecisionV2.

    PASS requires non-empty evidence_refs unless explicitly allowed (never in
    production paths). Alternate runtimes must call `adapter_non_canonical_report`
    instead of this function for success claims.
    """
    if not tenant_id.strip():
        raise CompletionAuthorityError("TENANT_REQUIRED")
    if not objective_id.strip() or not run_id.strip():
        raise CompletionAuthorityError("IDENTITY_REQUIRED")

    reason_t = tuple(str(r) for r in reasons)
    evidence_t = tuple(str(e) for e in evidence_refs if str(e).strip())
    approval_t = tuple(str(a) for a in (approval_refs or ()) if str(a).strip())

    if verdict == "PASS" and not evidence_t and not allow_success_without_evidence:
        raise CompletionAuthorityError("HALT:PASS_REQUIRES_EVIDENCE")
    if verdict == "PASS" and not budget_ok:
        raise CompletionAuthorityError("HALT:BUDGET_EXHAUSTED")
    decision = CompletionDecisionV2(
        decision_id=decision_id or f"cd_{new_ulid()[:16]}",
        tenant_id=tenant_id,
        objective_id=objective_id,
        run_id=run_id,
        verdict=verdict,
        reasons=reason_t,
        evidence_refs=evidence_t,
        approval_refs=approval_t,
        budget_ok=budget_ok,
        emitter=CANONICAL_EMITTER,
    )
    _EMITTED.append(decision)
    return decision


def adapter_non_canonical_report(
    *,
    source: str,
    tenant_id: str,
    objective_id: str,
    run_id: str,
    claimed_status: str,
    detail: str = "",
) -> CompletionDecisionV2:
    """Record that an alternate engine claimed completion without canonical authority.

    Never returns verdict=PASS. Used until C004 retires alternate write paths.
    """
    decision = CompletionDecisionV2(
        decision_id=f"ncd_{new_ulid()[:16]}",
        tenant_id=tenant_id or "unknown",
        objective_id=objective_id or "unknown",
        run_id=run_id or "unknown",
        verdict="NON_CANONICAL",
        reasons=(
            f"NON_CANONICAL_SOURCE:{source}",
            f"CLAIMED_STATUS:{claimed_status}",
            detail or "Alternate engine may not mint canonical terminal success.",
        ),
        evidence_refs=(),
        emitter=f"adapter:{source}",
        replayable=True,
    )
    _EMITTED.append(decision)
    return decision


def decision_from_cycle_outcome(
    *,
    tenant_id: str,
    objective_id: str,
    cycle_id: str,
    terminal_status: str,
    evidence_refs: tuple[str, ...] | list[str],
    reasons: tuple[str, ...] | list[str] | None = None,
    budget_ok: bool = True,
    terminalization_decision: Any = None,
) -> CompletionDecisionV2:
    """Map objective_cycle CycleOutcome terminal_status onto CompletionDecisionV2."""
    status = str(terminal_status or "").strip().upper()
    
    # Correction 6: COMPLETED_FOR_REVIEW != PASS.
    # Block mapping CFR -> PASS unless terminalization_decision is ALLOW_CERTIFIED.
    if status == "COMPLETED_FOR_REVIEW":
        is_allow_certified = (
            getattr(terminalization_decision, "decision", None) == "ALLOW_CERTIFIED"
            if terminalization_decision is not None
            else False
        )
        if is_allow_certified:
            verdict: CompletionVerdict = "PASS"
        else:
            verdict = "INSUFFICIENT_EVIDENCE"
    else:
        mapping: dict[str, CompletionVerdict] = {
            "CERTIFIED": "PASS",
            "GAPS_FOUND": "FAIL",
            "FAILED": "FAIL",
            "BLOCKED": "BLOCKED",
            "CANCELLED": "CANCELLED",
            "REJECTED": "REJECTED",
        }
        verdict = mapping.get(status, "INSUFFICIENT_EVIDENCE")
    return decide_completion(
        tenant_id=tenant_id,
        objective_id=objective_id,
        run_id=cycle_id,
        verdict=verdict,
        reasons=reasons or (f"cycle_outcome:{status}",),
        evidence_refs=evidence_refs,
        budget_ok=budget_ok,
        # COMPLETED_FOR_REVIEW may legitimately wait for human review with artifact refs;
        # still require at least one evidence ref for PASS.
        allow_success_without_evidence=False,
    )
