"""AIX-REAL-EXECUTION-005: verification-check contracts and outcome aggregation."""

from __future__ import annotations

from backend.aix_core.runtime.verification.verification_engine import (
    aggregate_outcome,
    build_checks_from_completion_gates,
    build_checks_from_read_verification,
)


def test_all_passed_read_verification_aggregates_to_complete() -> None:
    checks = build_checks_from_read_verification(
        {
            "resource_resolved": True,
            "file_read_non_empty": True,
            "target_resource_accessed": True,
            "secret_values_redacted": True,
        }
    )
    outcome = aggregate_outcome(checks)
    assert outcome.all_mandatory_passed is True
    assert outcome.failed_mandatory == ()
    assert len(checks) == 4
    assert all(c.mandatory for c in checks)


def test_empty_result_fails_mandatory_verification() -> None:
    checks = build_checks_from_read_verification(
        {
            "resource_resolved": True,
            "file_read_non_empty": False,
            "target_resource_accessed": True,
            "secret_values_redacted": True,
        }
    )
    outcome = aggregate_outcome(checks)
    assert outcome.all_mandatory_passed is False
    assert "file_read_non_empty" in outcome.failed_mandatory


def test_no_verification_data_yields_no_checks_and_no_mandatory_pass() -> None:
    outcome = aggregate_outcome(build_checks_from_read_verification(None))
    assert outcome.checks == ()
    # No checks means no *failed* mandatory check, but also no proof of anything --
    # callers must treat an empty outcome as unverified, never as a free pass.
    assert outcome.all_mandatory_passed is True


def test_completion_gates_failure_surfaces_as_failed_check() -> None:
    checks = build_checks_from_completion_gates(
        {
            "gates": {"source_read": True, "source_changed": False},
            "verification_verdict": "FAILED",
        }
    )
    outcome = aggregate_outcome(checks)
    assert outcome.all_mandatory_passed is False
    assert "source_changed" in outcome.failed_mandatory
    assert "diff_matches_expected" in outcome.failed_mandatory


def test_completion_gates_all_passed_verdict_passed() -> None:
    checks = build_checks_from_completion_gates(
        {
            "gates": {"source_read": True, "source_changed": True, "verification_passed": True},
            "verification_verdict": "PASSED",
        }
    )
    outcome = aggregate_outcome(checks)
    assert outcome.all_mandatory_passed is True


def test_check_carries_evidence_ref_and_verifier_version() -> None:
    checks = build_checks_from_read_verification({"resource_resolved": True}, evidence_ref="cyc_123")
    assert checks[0].evidence_ref == "cyc_123"
    assert checks[0].verifier_version
    assert checks[0].started_at and checks[0].completed_at
