"""AIX-E2E-CYCLE-001M-W03 SLO/error-budget policy tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.slo_policy import (
    SloEvaluator,
    SloPolicyError,
    SloTargetsV1,
)


def _targets(**overrides: object) -> SloTargetsV1:
    base = dict(
        availability_target=0.99,
        completion_latency_p95_seconds=60.0,
        stuck_age_seconds=300.0,
        recovery_time_objective_seconds=120.0,
    )
    base.update(overrides)
    return SloTargetsV1(**base)


class TestTargetsValidation:
    @pytest.mark.parametrize("target", [0.0, -0.1, 1.1])
    def test_invalid_availability_target_rejected(self, target: float) -> None:
        with pytest.raises(SloPolicyError):
            _targets(availability_target=target)

    def test_non_positive_latency_rejected(self) -> None:
        with pytest.raises(SloPolicyError):
            _targets(completion_latency_p95_seconds=0)


class TestEvaluate:
    def test_meeting_all_targets(self) -> None:
        evaluator = SloEvaluator(_targets())
        result = evaluator.evaluate(observed_failure_ratio=0.001, observed_latency_p95_seconds=50.0)
        assert result.slo_met is True
        assert result.error_budget_remaining_ratio > 0

    def test_availability_breach(self) -> None:
        evaluator = SloEvaluator(_targets())
        result = evaluator.evaluate(observed_failure_ratio=0.05, observed_latency_p95_seconds=50.0)
        assert result.availability_met is False
        assert result.slo_met is False
        assert result.error_budget_remaining_ratio < 0

    def test_latency_breach_independent_of_availability(self) -> None:
        evaluator = SloEvaluator(_targets())
        result = evaluator.evaluate(observed_failure_ratio=0.0, observed_latency_p95_seconds=120.0)
        assert result.availability_met is True
        assert result.latency_met is False
        assert result.slo_met is False

    def test_exactly_at_error_budget_boundary(self) -> None:
        evaluator = SloEvaluator(_targets(availability_target=0.99))
        result = evaluator.evaluate(observed_failure_ratio=0.01, observed_latency_p95_seconds=1.0)
        assert result.availability_met is True
        assert result.error_budget_remaining_ratio == pytest.approx(0.0, abs=1e-9)

    def test_invalid_failure_ratio_rejected(self) -> None:
        evaluator = SloEvaluator(_targets())
        with pytest.raises(SloPolicyError):
            evaluator.evaluate(observed_failure_ratio=1.5, observed_latency_p95_seconds=1.0)


class TestStuckAndRecovery:
    def test_is_stuck_true_past_threshold(self) -> None:
        evaluator = SloEvaluator(_targets(stuck_age_seconds=300))
        assert evaluator.is_stuck(age_seconds=301) is True
        assert evaluator.is_stuck(age_seconds=299) is False

    def test_recovery_within_objective(self) -> None:
        evaluator = SloEvaluator(_targets(recovery_time_objective_seconds=120))
        assert evaluator.recovery_within_objective(actual_recovery_seconds=100) is True
        assert evaluator.recovery_within_objective(actual_recovery_seconds=150) is False
