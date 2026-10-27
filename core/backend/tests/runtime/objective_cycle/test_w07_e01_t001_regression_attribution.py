"""AIX-W07-E01-T001: Baseline and Regression Attribution Tests.

Verifies:
1. Exact 4-way classification: PRE_EXISTING, NEW_REGRESSION, FIXED_EXISTING, UNCHANGED_PASS.
2. New regression detection flags newly introduced failures.
3. Baseline red tests are preserved as PRE_EXISTING and not mislabeled.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.verification.regression_attribution import (
    AttributionItemResult,
    RegressionAttributionReport,
    attribute_test_results,
)


def test_four_way_regression_attribution() -> None:
    """Verifies correct classification across all 4 categories."""
    baseline = [
        AttributionItemResult("test_unchanged", passed=True),
        AttributionItemResult("test_fixed", passed=False, error_message="AssertionError"),
        AttributionItemResult("test_pre_existing", passed=False, error_message="TimeoutError"),
        AttributionItemResult("test_regressed", passed=True),
    ]

    current = [
        AttributionItemResult("test_unchanged", passed=True),
        AttributionItemResult("test_fixed", passed=True),
        AttributionItemResult("test_pre_existing", passed=False, error_message="TimeoutError"),
        AttributionItemResult("test_regressed", passed=False, error_message="KeyError"),
    ]

    report = attribute_test_results(baseline, current)

    by_id = {item.test_id: item.category for item in report.items}
    assert by_id["test_unchanged"] == "UNCHANGED_PASS"
    assert by_id["test_fixed"] == "FIXED_EXISTING"
    assert by_id["test_pre_existing"] == "PRE_EXISTING"
    assert by_id["test_regressed"] == "NEW_REGRESSION"

    assert report.has_new_regressions is True
    assert report.new_regressions_count == 1
    assert report.pre_existing_count == 1


def test_no_regressions_when_all_pass_or_improve() -> None:
    """Report has_new_regressions is False when all baseline tests pass or fix existing errors."""
    baseline = [
        AttributionItemResult("test_a", passed=True),
        AttributionItemResult("test_b", passed=False),
    ]
    current = [
        AttributionItemResult("test_a", passed=True),
        AttributionItemResult("test_b", passed=True),  # fixed!
    ]

    report = attribute_test_results(baseline, current)

    assert report.has_new_regressions is False
    assert report.new_regressions_count == 0
