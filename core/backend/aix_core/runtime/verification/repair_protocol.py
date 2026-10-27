"""AIX-W08-E01-T002: Repair Hypothesis Protocol.

Enforces structured repair directives linking root-cause evidence, hypothesis, blast radius,
budget, and changed strategy. Rejects identical retries that lack a new hypothesis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class IdenticalRetryError(ValueError):
    def __init__(self, code: str = "IDENTICAL_RETRY_WITHOUT_NEW_HYPOTHESIS_REJECTED", detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class RepairHypothesisV1:
    failure_signature: str
    root_cause_evidence_refs: tuple[str, ...]
    hypothesis: str
    blast_radius_files: tuple[str, ...]
    repair_budget_allocated: dict[str, Any]
    changed_strategy: str


def validate_repair_hypothesis(
    current: RepairHypothesisV1,
    prior: RepairHypothesisV1 | None = None,
) -> None:
    """Validates repair hypothesis invariants and rejects identical retries without a new hypothesis."""
    if not current.hypothesis.strip():
        raise IdenticalRetryError("EMPTY_REPAIR_HYPOTHESIS", "Repair hypothesis cannot be empty.")
    if not current.changed_strategy.strip():
        raise IdenticalRetryError("EMPTY_CHANGED_STRATEGY", "Repair must specify a changed strategy.")
    if not current.root_cause_evidence_refs:
        raise IdenticalRetryError("MISSING_ROOT_CAUSE_EVIDENCE", "Repair must link root-cause evidence references.")

    if prior is not None:
        if (
            current.failure_signature == prior.failure_signature
            and current.hypothesis.strip().casefold() == prior.hypothesis.strip().casefold()
            and current.changed_strategy.strip().casefold() == prior.changed_strategy.strip().casefold()
        ):
            raise IdenticalRetryError(
                "IDENTICAL_RETRY_WITHOUT_NEW_HYPOTHESIS_REJECTED",
                "Identical retry attempt detected without an updated hypothesis or strategy.",
            )


__all__ = [
    "IdenticalRetryError",
    "RepairHypothesisV1",
    "validate_repair_hypothesis",
]
