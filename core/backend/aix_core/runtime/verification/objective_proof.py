"""AIX-W07-E02-T004: Objective Proof Algorithm.

Determines objective proof status (PROVEN_COMPLETE vs COMPLETED_FOR_REVIEW / REPAIRABLE / BLOCKED).
Guarantees PROVEN_COMPLETE is impossible with failed/unknown mandatory criteria, stale snapshots,
invalid evidence, or unresolved regressions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ObjectiveProofStatus = Literal["PROVEN_COMPLETE", "COMPLETED_FOR_REVIEW", "REPAIRABLE_FAILURE", "BLOCKED_FAILURE"]


class ObjectiveProofError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ObjectiveProofEvaluation:
    status: ObjectiveProofStatus
    proven_complete: bool
    blocking_reasons: tuple[str, ...]


def evaluate_objective_proof(
    *,
    all_mandatory_criteria_passed: bool,
    has_unresolved_regressions: bool,
    is_snapshot_stale: bool,
    has_invalid_evidence: bool,
    is_human_review_only: bool = False,
) -> ObjectiveProofEvaluation:
    """Evaluates whether an objective cycle is PROVEN_COMPLETE. Fails closed to COMPLETED_FOR_REVIEW or failure."""
    reasons: list[str] = []

    if not all_mandatory_criteria_passed:
        reasons.append("MANDATORY_CRITERIA_UNMET")
    if has_unresolved_regressions:
        reasons.append("UNRESOLVED_REGRESSIONS_PRESENT")
    if is_snapshot_stale:
        reasons.append("REPOSITORY_SNAPSHOT_STALE")
    if has_invalid_evidence:
        reasons.append("INVALID_OR_CORRUPTED_EVIDENCE")

    if reasons:
        return ObjectiveProofEvaluation(
            status="REPAIRABLE_FAILURE" if "MANDATORY_CRITERIA_UNMET" in reasons else "BLOCKED_FAILURE",
            proven_complete=False,
            blocking_reasons=tuple(reasons),
        )

    if is_human_review_only:
        return ObjectiveProofEvaluation(
            status="COMPLETED_FOR_REVIEW",
            proven_complete=False,
            blocking_reasons=("HUMAN_REVIEW_REQUIRED",),
        )

    return ObjectiveProofEvaluation(
        status="PROVEN_COMPLETE",
        proven_complete=True,
        blocking_reasons=(),
    )


__all__ = [
    "ObjectiveProofStatus",
    "ObjectiveProofError",
    "ObjectiveProofEvaluation",
    "evaluate_objective_proof",
]
