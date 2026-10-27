"""AIX-E2E-CYCLE-001M-W03: SLO/error-budget policy.

Targets named in the 001M-W03 outcome: availability, completion latency, stuck age,
recovery, alert objectives. This module evaluates observed values against configured
targets and computes remaining error budget — it does not itself collect the observed
values (that's `metrics.py`, 001M-W02) or send alerts (that's `alert_delivery.py`,
001M-W04); it is the decision layer between the two.
"""

from __future__ import annotations

from dataclasses import dataclass


class SloPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class SloTargetsV1:
    availability_target: float  # e.g. 0.995
    completion_latency_p95_seconds: float
    stuck_age_seconds: float
    recovery_time_objective_seconds: float

    def __post_init__(self) -> None:
        if not (0.0 < self.availability_target <= 1.0):
            raise SloPolicyError("availability_target must be in (0, 1]")
        for name, value in (
            ("completion_latency_p95_seconds", self.completion_latency_p95_seconds),
            ("stuck_age_seconds", self.stuck_age_seconds),
            ("recovery_time_objective_seconds", self.recovery_time_objective_seconds),
        ):
            if value <= 0:
                raise SloPolicyError(f"{name} must be positive")


@dataclass(frozen=True)
class SloEvaluationV1:
    availability_met: bool
    latency_met: bool
    error_budget_remaining_ratio: float
    """1.0 = full error budget remaining; 0.0 = fully exhausted; negative = breached
    beyond budget. Computed as (availability_target - observed_failure_ratio) /
    (1 - availability_target), clamped only at the top (1.0), not the bottom, so a
    breach is visibly negative rather than silently floored at 0."""

    @property
    def slo_met(self) -> bool:
        return self.availability_met and self.latency_met


class SloEvaluator:
    def __init__(self, targets: SloTargetsV1) -> None:
        self._targets = targets

    def evaluate(
        self,
        *,
        observed_failure_ratio: float,
        observed_latency_p95_seconds: float,
    ) -> SloEvaluationV1:
        if not (0.0 <= observed_failure_ratio <= 1.0):
            raise SloPolicyError("observed_failure_ratio must be in [0, 1]")
        if observed_latency_p95_seconds < 0:
            raise SloPolicyError("observed_latency_p95_seconds must be non-negative")

        observed_availability = 1.0 - observed_failure_ratio
        availability_met = observed_availability >= self._targets.availability_target
        latency_met = observed_latency_p95_seconds <= self._targets.completion_latency_p95_seconds

        allowed_failure_ratio = 1.0 - self._targets.availability_target
        if allowed_failure_ratio <= 0:
            budget_remaining = 1.0 if observed_failure_ratio == 0 else -float("inf")
        else:
            budget_remaining = min(1.0, (allowed_failure_ratio - observed_failure_ratio) / allowed_failure_ratio)

        return SloEvaluationV1(
            availability_met=availability_met,
            latency_met=latency_met,
            error_budget_remaining_ratio=budget_remaining,
        )

    def is_stuck(self, *, age_seconds: float) -> bool:
        return age_seconds >= self._targets.stuck_age_seconds

    def recovery_within_objective(self, *, actual_recovery_seconds: float) -> bool:
        return actual_recovery_seconds <= self._targets.recovery_time_objective_seconds


__all__ = [
    "SloPolicyError",
    "SloTargetsV1",
    "SloEvaluationV1",
    "SloEvaluator",
]
