"""AIX-E2E-CYCLE-001H-W01: independent verification contract.

The single most important invariant this module enforces: a step's own executor claiming
success is never sufficient for a `PASSED` verdict. `VerificationInputV1.independent_evidence_refs`
must be non-empty and `claimed_outcome` is recorded for audit but never read by
`IndependentVerifier.verify` to decide the verdict — only `criteria_results` (independently
computed) and the quality gate report do. This is the direct countermeasure to the
"false success" hostile scenario named throughout this programme's non-happy-path
acceptance criteria.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.aix_core.runtime.verification.quality_gates import QualityGateReportV1

VerificationVerdict = Literal["PASSED", "FAILED", "INCONCLUSIVE", "BLOCKED"]


class VerificationError(ValueError):
    pass


@dataclass(frozen=True)
class VerificationInputV1:
    step_id: str
    attempt_id: str
    tenant_id: str
    acceptance_criteria: tuple[str, ...]
    criteria_results: dict[str, bool]
    """Independently computed pass/fail per acceptance-criterion string — computed by the
    verifier's own tooling, not read from whatever the executor claimed."""
    independent_evidence_refs: tuple[str, ...]
    quality_gates: QualityGateReportV1
    claimed_outcome: str
    """Recorded for audit trail only. Never consulted by `verify()`."""

    def __post_init__(self) -> None:
        if not self.acceptance_criteria:
            raise VerificationError("acceptance_criteria must not be empty")
        missing = set(self.acceptance_criteria) - set(self.criteria_results)
        if missing:
            raise VerificationError(f"criteria_results missing entries for: {sorted(missing)}")


@dataclass(frozen=True)
class VerificationResultV1:
    verdict: VerificationVerdict
    reason_codes: tuple[str, ...]
    unmet_criteria: tuple[str, ...] = ()


class IndependentVerifier:
    def verify(self, verification_input: VerificationInputV1) -> VerificationResultV1:
        if not verification_input.independent_evidence_refs:
            return VerificationResultV1(
                verdict="BLOCKED",
                reason_codes=("NO_INDEPENDENT_EVIDENCE_PROVIDED",),
            )

        unmet = tuple(
            criterion
            for criterion in verification_input.acceptance_criteria
            if not verification_input.criteria_results.get(criterion, False)
        )
        if unmet:
            return VerificationResultV1(
                verdict="FAILED",
                reason_codes=("ACCEPTANCE_CRITERIA_UNMET",),
                unmet_criteria=unmet,
            )

        if not verification_input.quality_gates.all_passed:
            return VerificationResultV1(
                verdict="FAILED",
                reason_codes=tuple(f"QUALITY_GATE_FAILED:{gate}" for gate in verification_input.quality_gates.failed_gates),
            )

        return VerificationResultV1(verdict="PASSED", reason_codes=("ALL_CRITERIA_AND_GATES_PASSED",))


__all__ = [
    "VerificationVerdict",
    "VerificationError",
    "VerificationInputV1",
    "VerificationResultV1",
    "IndependentVerifier",
]
