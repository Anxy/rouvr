"""AIX-E2E-CYCLE-001H-W05 bounded retry/repair tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1
from backend.aix_core.runtime.verification.failure_taxonomy import FailureClassificationV1
from backend.aix_core.runtime.recovery.retry_repair import (
    RetryRepairError,
    decide_retry,
)


def _budget(retries: int) -> BudgetV1:
    return BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=retries, steps=1, concurrency=1)


class TestDecideRetry:
    def test_retryable_with_budget_is_authorized(self) -> None:
        classification = FailureClassificationV1("RETRYABLE", ("TIMEOUT",))
        decision = decide_retry(classification, current_attempt_number=1, remaining_budget=_budget(2))
        assert decision.disposition == "AUTHORIZED"
        assert decision.next_attempt_number == 2
        assert decision.remaining_budget is not None
        assert decision.remaining_budget.retries == 1

    def test_repairable_with_budget_is_authorized(self) -> None:
        classification = FailureClassificationV1("REPAIRABLE", ("ACCEPTANCE_CRITERIA_UNMET",))
        decision = decide_retry(classification, current_attempt_number=2, remaining_budget=_budget(1))
        assert decision.disposition == "AUTHORIZED"
        assert decision.next_attempt_number == 3

    def test_zero_retry_budget_denied(self) -> None:
        classification = FailureClassificationV1("RETRYABLE", ("TIMEOUT",))
        decision = decide_retry(classification, current_attempt_number=1, remaining_budget=_budget(0))
        assert decision.disposition == "DENIED"
        assert "RETRY_BUDGET_EXHAUSTED" in decision.reason_codes

    @pytest.mark.parametrize("failure_class", ["BLOCKED", "TERMINAL", "REPLAN_REQUIRED"])
    def test_non_retry_eligible_class_denied_even_with_budget(self, failure_class: str) -> None:
        classification = FailureClassificationV1(failure_class, ("X",))  # type: ignore[arg-type]
        decision = decide_retry(classification, current_attempt_number=1, remaining_budget=_budget(5))
        assert decision.disposition == "DENIED"

    def test_original_budget_object_is_not_mutated(self) -> None:
        original = _budget(3)
        classification = FailureClassificationV1("RETRYABLE", ("TIMEOUT",))
        decide_retry(classification, current_attempt_number=1, remaining_budget=original)
        assert original.retries == 3  # frozen contract; decide_retry must return a new one

    def test_decrement_below_zero_raises(self) -> None:
        from backend.aix_core.runtime.recovery.retry_repair import _decrement_retry_budget

        with pytest.raises(RetryRepairError):
            _decrement_retry_budget(_budget(0))
