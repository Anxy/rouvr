"""AIX-E2E-CYCLE-001H-W04: deterministic failure taxonomy.

Classifies a `VerificationResultV1` (from `verification.py`) into exactly one of five
disjoint classes. Classification is a pure function of the verdict, reason codes, and
attempt/budget counters — never of what the executor claimed, and never randomized or
"best guess": the same inputs always classify the same way, which is what "deterministic"
means for the `001B-W04`/`001H-W05` retry-budget accounting downstream of this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.aix_core.runtime.verification.verification import VerificationResultV1

FailureClass = Literal["RETRYABLE", "REPAIRABLE", "REPLAN_REQUIRED", "BLOCKED", "TERMINAL"]

# Reason codes this packet recognizes as "the same kind of thing happened again" —
# a transient condition worth a bare retry with no code/plan change.
_RETRYABLE_REASON_PREFIXES = ("TIMEOUT", "TRANSIENT", "NETWORK_FLAKE")

# Reason codes indicating the acceptance criteria were not met by the produced change,
# but the *plan* (which steps, what scope) is not itself suspect — a repair attempt
# (same step, adjusted approach) is worth trying before concluding the plan is wrong.
_REPAIRABLE_REASON_PREFIXES = ("ACCEPTANCE_CRITERIA_UNMET", "QUALITY_GATE_FAILED")


@dataclass(frozen=True)
class FailureClassificationV1:
    failure_class: FailureClass
    reason_codes: tuple[str, ...]


class FailureTaxonomyError(ValueError):
    pass


def classify(
    result: VerificationResultV1,
    *,
    attempt_number: int,
    max_attempts: int,
    replans_used: int,
    max_replans: int,
) -> FailureClassificationV1:
    if attempt_number < 1:
        raise FailureTaxonomyError("attempt_number must be >= 1")
    if max_attempts < 1:
        raise FailureTaxonomyError("max_attempts must be >= 1")

    if result.verdict == "PASSED":
        raise FailureTaxonomyError("classify() must not be called on a PASSED verification result")

    if result.verdict == "BLOCKED":
        # e.g. no independent evidence provided — this is not a retryable transient
        # failure, it is a structural problem with the attempt itself.
        return FailureClassificationV1("BLOCKED", result.reason_codes)

    if result.verdict == "INCONCLUSIVE":
        return FailureClassificationV1("BLOCKED", result.reason_codes)

    # verdict == "FAILED" from here on.
    if attempt_number >= max_attempts:
        if replans_used >= max_replans:
            return FailureClassificationV1("TERMINAL", result.reason_codes + ("ATTEMPT_AND_REPLAN_BUDGET_EXHAUSTED",))
        return FailureClassificationV1("REPLAN_REQUIRED", result.reason_codes + ("ATTEMPT_BUDGET_EXHAUSTED",))

    if any(code.startswith(_RETRYABLE_REASON_PREFIXES) for code in result.reason_codes):
        return FailureClassificationV1("RETRYABLE", result.reason_codes)

    if any(code.startswith(_REPAIRABLE_REASON_PREFIXES) for code in result.reason_codes):
        return FailureClassificationV1("REPAIRABLE", result.reason_codes)

    # An unrecognized reason code is not silently treated as retryable — fail closed
    # toward the more conservative REPLAN_REQUIRED rather than guessing it's transient.
    return FailureClassificationV1("REPLAN_REQUIRED", result.reason_codes + ("UNCLASSIFIED_REASON_CODE",))


__all__ = [
    "FailureClass",
    "FailureClassificationV1",
    "FailureTaxonomyError",
    "classify",
]
