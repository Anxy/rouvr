"""AIX Feature 12 (Packet 07) — Unit tests for Regression Verification."""

import pytest
from backend.aix_core.runtime.verification.regression_checker import (
    RegressionChecker,
)
from backend.aix_core.runtime.verification.verification_result import (
    OVItemResultV1,
)


def test_regression_checker_detects_broken_prior_pass():
    checker = RegressionChecker(baseline_passed_ov_ids={"OV-003", "OV-004"})

    # Post-repair result: OV-002 is fixed, but OV-003 regressed to FAIL!
    current_results = [
        OVItemResultV1(
            ov_id="OV-002",
            status="PASS",
            expected="SQLite persistence",
            observed="SQLite configured",
        ),
        OVItemResultV1(
            ov_id="OV-003",
            status="FAIL",
            expected="FastAPI uvicorn startup",
            observed="Syntax error in main.py introduced during DB repair",
        ),
        OVItemResultV1(
            ov_id="OV-004",
            status="PASS",
            expected="Registration endpoint",
            observed="Registration endpoint works",
        ),
    ]

    regression_gaps = checker.detect_regressions(
        objective_id="obj-reg-1",
        verification_id="v-repair-1",
        current_results=current_results,
    )

    assert len(regression_gaps) == 1
    reg_gap = regression_gaps[0]
    assert reg_gap.ov_id == "OV-003"
    assert reg_gap.category == "REGRESSION"
    assert reg_gap.severity == "BLOCKER"
    assert reg_gap.status == "OPEN"
    assert "Regression detected" in reg_gap.observed


def test_regression_checker_no_regression_when_all_prior_pass():
    checker = RegressionChecker(baseline_passed_ov_ids={"OV-003"})

    current_results = [
        OVItemResultV1(
            ov_id="OV-002",
            status="PASS",
            expected="SQLite persistence",
            observed="SQLite configured",
        ),
        OVItemResultV1(
            ov_id="OV-003",
            status="PASS",
            expected="FastAPI uvicorn startup",
            observed="Uvicorn server starts",
        ),
    ]

    regression_gaps = checker.detect_regressions(
        objective_id="obj-reg-2",
        verification_id="v-repair-2",
        current_results=current_results,
    )

    assert len(regression_gaps) == 0
