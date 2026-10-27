"""AIX-E2E-CYCLE-001H-W01 verification contract tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.verification.quality_gates import REQUIRED_GATES, build_report
from backend.aix_core.runtime.verification.verification import (
    IndependentVerifier,
    VerificationError,
    VerificationInputV1,
)

ALL_GATES_PASS = build_report({gate: True for gate in REQUIRED_GATES})


def _input(**overrides: object) -> VerificationInputV1:
    base = dict(
        step_id="step-1",
        attempt_id="attempt-1",
        tenant_id="tenant-1",
        acceptance_criteria=("docs updated",),
        criteria_results={"docs updated": True},
        independent_evidence_refs=("evidence://attempt-1/diff",),
        quality_gates=ALL_GATES_PASS,
        claimed_outcome="SUCCEEDED",
    )
    base.update(overrides)
    return VerificationInputV1(**base)


class TestVerificationInputValidation:
    def test_empty_acceptance_criteria_rejected(self) -> None:
        with pytest.raises(VerificationError):
            _input(acceptance_criteria=())

    def test_missing_criteria_result_rejected(self) -> None:
        with pytest.raises(VerificationError):
            _input(acceptance_criteria=("a", "b"), criteria_results={"a": True})


class TestIndependentVerifier:
    def test_all_criteria_and_gates_pass_yields_passed(self) -> None:
        result = IndependentVerifier().verify(_input())
        assert result.verdict == "PASSED"

    def test_false_success_without_independent_evidence_is_blocked_not_passed(self) -> None:
        # The executor claims SUCCEEDED and criteria_results even says True, but there
        # is no independent evidence at all — this must never become PASSED.
        result = IndependentVerifier().verify(
            _input(independent_evidence_refs=(), criteria_results={"docs updated": True})
        )
        assert result.verdict == "BLOCKED"
        assert "NO_INDEPENDENT_EVIDENCE_PROVIDED" in result.reason_codes

    def test_claimed_outcome_success_does_not_override_failed_criteria(self) -> None:
        # claimed_outcome says SUCCEEDED but the independently computed criteria_results
        # says the criterion was not actually met — verdict must be FAILED, not PASSED.
        result = IndependentVerifier().verify(
            _input(claimed_outcome="SUCCEEDED", criteria_results={"docs updated": False})
        )
        assert result.verdict == "FAILED"
        assert "docs updated" in result.unmet_criteria

    def test_unmet_criteria_lists_every_failing_one(self) -> None:
        result = IndependentVerifier().verify(
            _input(
                acceptance_criteria=("a", "b", "c"),
                criteria_results={"a": True, "b": False, "c": False},
            )
        )
        assert result.verdict == "FAILED"
        assert set(result.unmet_criteria) == {"b", "c"}

    def test_quality_gate_failure_fails_verification_even_with_criteria_met(self) -> None:
        failing_gates = build_report({gate: (gate != "security") for gate in REQUIRED_GATES})
        result = IndependentVerifier().verify(_input(quality_gates=failing_gates))
        assert result.verdict == "FAILED"
        assert any("security" in code for code in result.reason_codes)
