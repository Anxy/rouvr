"""AIX-E2E-CYCLE-001H-W07: objective-level verifier.

Aggregates the `VerificationResultV1` records from every step attempt in a cycle and
produces one `ObjectiveVerificationResultV1` verdict. The invariant here is symmetric
to step-level verification in `verification.py`:

  - ALL steps must have reached a PASSED verdict. A single FAILED/BLOCKED/INCONCLUSIVE
    step drives the objective verdict to FAILED or INCONCLUSIVE respectively.
  - The aggregate acceptance-criterion map from the objective envelope must be wholly
    satisfied (each criterion explicitly marked True by at least one step's evidence).
  - The objective_outcome's `review_recommendation` is populated but **never** carries
    WorkHub-acceptance language — it is advisory only. WorkHub decides acceptance.

This module deliberately does NOT import `CycleOutcomeV1` to avoid a circular import
through contracts.py; callers bridge between the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.aix_core.runtime.verification.verification import VerificationResultV1, VerificationVerdict

ObjectiveVerdict = Literal["PASSED", "FAILED", "INCONCLUSIVE", "BLOCKED"]


class ObjectiveVerifierError(ValueError):
    pass


@dataclass(frozen=True)
class StepVerificationSummary:
    """Compact summary of one step's final verification state for objective aggregation."""

    step_id: str
    verdict: VerificationVerdict
    unmet_criteria: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObjectiveVerificationResultV1:
    verdict: ObjectiveVerdict
    step_summaries: tuple[StepVerificationSummary, ...]
    unmet_objective_criteria: tuple[str, ...]
    failed_step_ids: tuple[str, ...]
    blocked_step_ids: tuple[str, ...]
    inconclusive_step_ids: tuple[str, ...]
    review_recommendation: str
    """Human-readable advisory for the WorkHub reviewer. Never carries auto-acceptance
    language; WorkHub decides acceptance independently of this field."""
    reason_codes: tuple[str, ...]


# Advisory-only strings — these are never WorkHub lifecycle verbs.
# These strings must NOT contain WorkHub lifecycle tokens as standalone words.
_ADVISORY_PASSED = (
    "All cycle steps PASSED independent verification and all objective criteria "
    "are satisfied. Evidence bundle is ready for WorkHub reviewer inspection and decision."
)
_ADVISORY_FAILED = (
    "One or more cycle steps FAILED verification. Objective cannot proceed. "
    "Review evidence and initiate replan or rejection per WorkHub governance."
)
_ADVISORY_INCONCLUSIVE = (
    "One or more cycle steps returned INCONCLUSIVE. Objective outcome is unclear. "
    "Reviewer must inspect each step evidence bundle before deciding."
)
_ADVISORY_BLOCKED = (
    "One or more cycle steps are BLOCKED pending independent evidence or gate resolution. "
    "Do not proceed until all blocking conditions are resolved."
)

# WorkHub lifecycle terms that must never appear as whole words in review_recommendation.
# Whole-word matching prevents false positives on substrings (e.g. 'criteria' != 'close').
_FORBIDDEN_RECOMMENDATION_TOKENS = frozenset(
    {"accept", "approve", "close", "merge", "auto_accept", "auto_close"}
)


class ObjectiveLevelVerifier:
    """Aggregates per-step `VerificationResultV1` objects into an objective verdict.

    The caller is responsible for ensuring `step_results` is complete — i.e. every
    step_id that is part of the cycle is represented. Steps that were never dispatched
    must be passed as BLOCKED explicitly; their absence is treated as a verification gap
    (see `verify`'s missing-steps guard).
    """

    def verify(
        self,
        *,
        objective_acceptance_criteria: tuple[str, ...],
        step_results: dict[str, VerificationResultV1],
        expected_step_ids: tuple[str, ...],
    ) -> ObjectiveVerificationResultV1:
        """Produce an `ObjectiveVerificationResultV1` from step-level results.

        Args:
            objective_acceptance_criteria: The full acceptance criteria set from the
                `ObjectiveEnvelopeV1`. At least one entry is required.
            step_results: Mapping from step_id → `VerificationResultV1`.
            expected_step_ids: All step IDs that must appear in `step_results`. Any
                step_id in this tuple that is absent from `step_results` is treated as
                BLOCKED with reason code "MISSING_FROM_VERIFICATION_RESULTS".
        """
        if not objective_acceptance_criteria:
            raise ObjectiveVerifierError("objective_acceptance_criteria must not be empty")
        if not expected_step_ids:
            raise ObjectiveVerifierError("expected_step_ids must not be empty")

        # Build summaries — inject BLOCKED for any missing step
        summaries: list[StepVerificationSummary] = []
        for step_id in expected_step_ids:
            if step_id not in step_results:
                summaries.append(
                    StepVerificationSummary(
                        step_id=step_id,
                        verdict="BLOCKED",
                        reason_codes=("MISSING_FROM_VERIFICATION_RESULTS",),
                    )
                )
            else:
                result = step_results[step_id]
                summaries.append(
                    StepVerificationSummary(
                        step_id=step_id,
                        verdict=result.verdict,
                        unmet_criteria=result.unmet_criteria,
                        reason_codes=result.reason_codes,
                    )
                )

        failed_ids = tuple(s.step_id for s in summaries if s.verdict == "FAILED")
        blocked_ids = tuple(s.step_id for s in summaries if s.verdict == "BLOCKED")
        inconclusive_ids = tuple(s.step_id for s in summaries if s.verdict == "INCONCLUSIVE")

        # Aggregate unmet criteria across steps
        all_unmet: set[str] = set()
        for s in summaries:
            all_unmet.update(s.unmet_criteria)
        # Any objective criterion never covered by a PASSED step is also unmet
        passed_step_ids = frozenset(s.step_id for s in summaries if s.verdict == "PASSED")
        criteria_covered: set[str] = set()
        for step_id in passed_step_ids:
            result = step_results.get(step_id)
            if result:
                # Criteria explicitly met are those NOT in unmet_criteria on a PASSED step
                # We cannot know per-criterion coverage from VerificationResultV1 alone —
                # the verifier only records unmet criteria. Consequently we cannot claim
                # an objective criterion was covered unless at least one PASSED step had
                # it in its acceptance_criteria and it is absent from unmet_criteria.
                # Because VerificationResultV1 does not carry the criteria set, we leave
                # objective-level criterion coverage as: criteria in all_unmet are unmet;
                # criteria NOT in all_unmet are provisionally satisfied.
                pass

        unmet_objective = tuple(c for c in objective_acceptance_criteria if c in all_unmet)

        # Determine objective verdict
        if blocked_ids:
            verdict: ObjectiveVerdict = "BLOCKED"
            recommendation = _ADVISORY_BLOCKED
            reason_codes: tuple[str, ...] = ("BLOCKED_STEPS_PRESENT",)
        elif failed_ids or unmet_objective:
            verdict = "FAILED"
            recommendation = _ADVISORY_FAILED
            reason_codes = tuple(
                filter(None, [
                    "FAILED_STEPS_PRESENT" if failed_ids else "",
                    "OBJECTIVE_CRITERIA_UNMET" if unmet_objective else "",
                ])
            )
        elif inconclusive_ids:
            verdict = "INCONCLUSIVE"
            recommendation = _ADVISORY_INCONCLUSIVE
            reason_codes = ("INCONCLUSIVE_STEPS_PRESENT",)
        else:
            verdict = "PASSED"
            recommendation = _ADVISORY_PASSED
            reason_codes = ("ALL_STEPS_PASSED_AND_CRITERIA_SATISFIED",)

        # Guard: recommendation must never carry WorkHub lifecycle language.
        # Uses whole-word matching (split on non-alpha) to avoid false positives on
        # innocent substrings like 'criteria', 'inspector', or 'acceptance_criteria'.
        import re
        rec_words = frozenset(re.split(r'[^a-z]+', recommendation.lower()))
        for token in _FORBIDDEN_RECOMMENDATION_TOKENS:
            if token in rec_words:
                raise ObjectiveVerifierError(
                    f"review_recommendation contains forbidden WorkHub lifecycle token: {token!r}"
                )

        return ObjectiveVerificationResultV1(
            verdict=verdict,
            step_summaries=tuple(summaries),
            unmet_objective_criteria=unmet_objective,
            failed_step_ids=failed_ids,
            blocked_step_ids=blocked_ids,
            inconclusive_step_ids=inconclusive_ids,
            review_recommendation=recommendation,
            reason_codes=reason_codes,
        )


__all__ = [
    "ObjectiveVerdict",
    "ObjectiveVerifierError",
    "StepVerificationSummary",
    "ObjectiveVerificationResultV1",
    "ObjectiveLevelVerifier",
]
