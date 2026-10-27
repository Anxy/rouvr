"""AIX-W07-E01-T001: Baseline and Regression Attribution.

Compares baseline test suite results against current test run results to classify failures:
- PRE_EXISTING: Failed in baseline and failed in current run.
- NEW_REGRESSION: Passed in baseline but failed in current run.
- FIXED_EXISTING: Failed in baseline but passed in current run.
- UNCHANGED_PASS: Passed in baseline and passed in current run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AttributionCategory = Literal["PRE_EXISTING", "NEW_REGRESSION", "FIXED_EXISTING", "UNCHANGED_PASS"]


class AttributionError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class AttributionItemResult:
    test_id: str
    passed: bool
    error_message: str | None = None


@dataclass(frozen=True)
class AttributionRecord:
    test_id: str
    category: AttributionCategory
    baseline_passed: bool
    current_passed: bool
    details: str | None = None


@dataclass
class RegressionAttributionReport:
    items: list[AttributionRecord] = field(default_factory=list)

    @property
    def has_new_regressions(self) -> bool:
        return any(item.category == "NEW_REGRESSION" for item in self.items)

    @property
    def new_regressions_count(self) -> int:
        return sum(1 for item in self.items if item.category == "NEW_REGRESSION")

    @property
    def pre_existing_count(self) -> int:
        return sum(1 for item in self.items if item.category == "PRE_EXISTING")


def attribute_test_results(
    baseline: list[AttributionItemResult],
    current: list[AttributionItemResult],
) -> RegressionAttributionReport:
    """Attributes current test suite run against baseline suite run."""
    baseline_map = {item.test_id: item for item in baseline}
    current_map = {item.test_id: item for item in current}

    all_ids = sorted(set(baseline_map.keys()) | set(current_map.keys()))
    attributions: list[AttributionRecord] = []

    for tid in all_ids:
        b_item = baseline_map.get(tid)
        c_item = current_map.get(tid)

        if c_item is None:
            # Test deleted or missing in current run
            continue

        b_pass = b_item.passed if b_item else True  # Assume passed if newly introduced or missing from baseline
        c_pass = c_item.passed

        if b_pass and c_pass:
            cat: AttributionCategory = "UNCHANGED_PASS"
        elif not b_pass and c_pass:
            cat = "FIXED_EXISTING"
        elif b_pass and not c_pass:
            cat = "NEW_REGRESSION"
        else:
            cat = "PRE_EXISTING"

        attributions.append(
            AttributionRecord(
                test_id=tid,
                category=cat,
                baseline_passed=b_pass,
                current_passed=c_pass,
                details=c_item.error_message if not c_pass else None,
            )
        )

    return RegressionAttributionReport(items=attributions)


__all__ = [
    "AttributionCategory",
    "AttributionError",
    "AttributionItemResult",
    "AttributionRecord",
    "RegressionAttributionReport",
    "attribute_test_results",
]
