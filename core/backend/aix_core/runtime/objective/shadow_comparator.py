"""AIX Objective Shadow Comparator.

Performs read-only comparison between baseline and refactored decision paths.
Guarantees NO side-effects, double execution, or duplicate mutations.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DecisionComparison:
    decision_type: str  # plan, step, verification, repair, terminalization, phase
    baseline_result: Any
    refactored_result: Any
    matched: bool
    difference_reason: str | None = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )


@dataclass
class ShadowComparisonReport:
    scenario: str
    comparisons: list[DecisionComparison] = field(default_factory=list)
    all_matched: bool = True

    def add_comparison(
        self,
        decision_type: str,
        baseline: Any,
        refactored: Any,
        difference_reason: str | None = None,
    ) -> DecisionComparison:
        matched = baseline == refactored
        if not matched and difference_reason is None:
            difference_reason = f"Baseline={baseline!r} != Refactored={refactored!r}"
        if not matched:
            self.all_matched = False
        comp = DecisionComparison(
            decision_type=decision_type,
            baseline_result=baseline,
            refactored_result=refactored,
            matched=matched,
            difference_reason=difference_reason,
        )
        self.comparisons.append(comp)
        return comp


class ShadowComparator:
    """Read-only comparison engine for parallel decision verification."""

    def __init__(self) -> None:
        self.reports: list[ShadowComparisonReport] = []

    def compare_scenario(
        self,
        *,
        scenario: str,
        baseline_decisions: dict[str, Any],
        refactored_decisions: dict[str, Any],
    ) -> ShadowComparisonReport:
        """Compares baseline vs refactored decisions across key decision points."""
        report = ShadowComparisonReport(scenario=scenario)
        all_keys = set(baseline_decisions.keys()) | set(refactored_decisions.keys())

        for key in sorted(all_keys):
            base_val = baseline_decisions.get(key)
            ref_val = refactored_decisions.get(key)
            diff_reason = None
            if base_val != ref_val:
                diff_reason = f"Mismatch in {key}: baseline={base_val!r}, refactored={ref_val!r}"
            report.add_comparison(
                decision_type=key,
                baseline=base_val,
                refactored=ref_val,
                difference_reason=diff_reason,
            )

        self.reports.append(report)
        return report
