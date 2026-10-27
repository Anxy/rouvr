"""AIX-E2E-CYCLE-001H-W02 quality gates tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.verification.quality_gates import (
    REQUIRED_GATES,
    QualityGateError,
    build_report,
)

ALL_PASS = {gate: True for gate in REQUIRED_GATES}


class TestBuildReport:
    def test_all_pass(self) -> None:
        report = build_report(ALL_PASS)
        assert report.all_passed is True
        assert report.failed_gates == ()

    def test_one_fail(self) -> None:
        results = dict(ALL_PASS)
        results["lint"] = False
        report = build_report(results)
        assert report.all_passed is False
        assert "lint" in report.failed_gates

    def test_detail_tuple_form(self) -> None:
        results = dict(ALL_PASS)
        results["test"] = (False, "2 tests failed")
        report = build_report(results)
        failing = [r for r in report.results if r.gate == "test"][0]
        assert failing.passed is False
        assert failing.detail == "2 tests failed"

    def test_missing_required_gate_fails_closed(self) -> None:
        incomplete = dict(ALL_PASS)
        del incomplete["security"]
        with pytest.raises(QualityGateError):
            build_report(incomplete)

    def test_unknown_gate_rejected(self) -> None:
        extra = dict(ALL_PASS)
        extra["made_up_gate"] = True
        with pytest.raises(QualityGateError):
            build_report(extra)

    def test_gate_names_matches_required(self) -> None:
        report = build_report(ALL_PASS)
        assert report.gate_names == REQUIRED_GATES
