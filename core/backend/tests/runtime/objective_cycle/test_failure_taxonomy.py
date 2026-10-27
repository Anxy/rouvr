"""AIX-E2E-CYCLE-001H-W04 failure taxonomy tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.verification.failure_taxonomy import (
    FailureTaxonomyError,
    classify,
)
from backend.aix_core.runtime.verification.verification import VerificationResultV1


def _result(verdict: str, *codes: str) -> VerificationResultV1:
    return VerificationResultV1(verdict=verdict, reason_codes=codes)  # type: ignore[arg-type]


class TestClassify:
    def test_passed_verdict_rejected(self) -> None:
        with pytest.raises(FailureTaxonomyError):
            classify(_result("PASSED"), attempt_number=1, max_attempts=3, replans_used=0, max_replans=1)

    def test_blocked_stays_blocked(self) -> None:
        result = classify(
            _result("BLOCKED", "NO_INDEPENDENT_EVIDENCE_PROVIDED"),
            attempt_number=1,
            max_attempts=3,
            replans_used=0,
            max_replans=1,
        )
        assert result.failure_class == "BLOCKED"

    def test_inconclusive_becomes_blocked(self) -> None:
        result = classify(_result("INCONCLUSIVE"), attempt_number=1, max_attempts=3, replans_used=0, max_replans=1)
        assert result.failure_class == "BLOCKED"

    def test_timeout_before_budget_exhausted_is_retryable(self) -> None:
        result = classify(
            _result("FAILED", "TIMEOUT_DURING_EXECUTION"),
            attempt_number=1,
            max_attempts=3,
            replans_used=0,
            max_replans=1,
        )
        assert result.failure_class == "RETRYABLE"

    def test_acceptance_unmet_before_budget_exhausted_is_repairable(self) -> None:
        result = classify(
            _result("FAILED", "ACCEPTANCE_CRITERIA_UNMET"),
            attempt_number=1,
            max_attempts=3,
            replans_used=0,
            max_replans=1,
        )
        assert result.failure_class == "REPAIRABLE"

    def test_attempts_exhausted_with_replan_budget_left_requires_replan(self) -> None:
        result = classify(
            _result("FAILED", "ACCEPTANCE_CRITERIA_UNMET"),
            attempt_number=3,
            max_attempts=3,
            replans_used=0,
            max_replans=1,
        )
        assert result.failure_class == "REPLAN_REQUIRED"

    def test_attempts_and_replans_both_exhausted_is_terminal(self) -> None:
        result = classify(
            _result("FAILED", "ACCEPTANCE_CRITERIA_UNMET"),
            attempt_number=3,
            max_attempts=3,
            replans_used=1,
            max_replans=1,
        )
        assert result.failure_class == "TERMINAL"

    def test_unrecognized_reason_code_defaults_to_replan_required_not_retryable(self) -> None:
        # Fail-closed: an unmapped failure reason must not be assumed transient.
        result = classify(
            _result("FAILED", "SOMETHING_NEW_NOT_YET_CLASSIFIED"),
            attempt_number=1,
            max_attempts=3,
            replans_used=0,
            max_replans=1,
        )
        assert result.failure_class == "REPLAN_REQUIRED"
        assert "UNCLASSIFIED_REASON_CODE" in result.reason_codes

    def test_invalid_attempt_number_rejected(self) -> None:
        with pytest.raises(FailureTaxonomyError):
            classify(_result("FAILED", "TIMEOUT"), attempt_number=0, max_attempts=3, replans_used=0, max_replans=1)
