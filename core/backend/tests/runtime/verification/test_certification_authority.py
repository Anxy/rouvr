"""AIX Feature 12 (Packet 08) — Unit tests for Objective Certification Authority."""

import pytest
from backend.aix_core.runtime.governance.certification_authority import (
    CertificationAuthority,
    CertificationDecisionV1,
)
from backend.aix_core.runtime.governance.completion_authority import (
    decision_from_cycle_outcome,
)
from backend.aix_core.runtime.verification.terminalization_guard import (
    GuardsEvaluated,
    TerminalizationDecisionV1,
)


def _make_tdec(decision: str = "ALLOW_CERTIFIED", reasoning: str = "ok") -> TerminalizationDecisionV1:
    return TerminalizationDecisionV1(
        decision_id="tdec-test-01",
        verification_decision_id="vdec-test-01",
        objective_id="obj-cert-1",
        decision=decision,  # type: ignore[arg-type]
        guards_evaluated=GuardsEvaluated(
            all_required_steps_verified=True,
            objective_runtime_verification="PASS" if decision == "ALLOW_CERTIFIED" else "FAIL",
            blocking_gaps=0 if decision == "ALLOW_CERTIFIED" else 1,
            required_evidence_complete=True,
            ovc_coverage_complete=True,
        ),
        reasoning=reasoning,
    )


def test_certification_authority_certifies_when_allow_certified():
    auth = CertificationAuthority(certifier_model="claude-3-5-sonnet")
    tdec = _make_tdec("ALLOW_CERTIFIED", "All conjuncts pass")

    cert = auth.evaluate_certification(
        objective_id="obj-cert-1",
        cycle_id="cyc-cert-1",
        terminalization_decision=tdec,
        executor_model="gpt-4o",  # Independent from certifier
    )

    assert cert.verdict == "CERTIFIED"
    assert cert.certifier["model"] == "claude-3-5-sonnet"
    assert "All 5 terminalization conjunction criteria satisfied" in cert.reasons[0]


def test_certification_authority_demands_repair_on_deny_needs_repair():
    auth = CertificationAuthority()
    tdec = _make_tdec("DENY_NEEDS_REPAIR", "SQLite probe failed")

    cert = auth.evaluate_certification(
        objective_id="obj-cert-2",
        cycle_id="cyc-cert-2",
        terminalization_decision=tdec,
    )

    assert cert.verdict == "REPAIR_REQUIRED"
    assert "Terminalization denied: open gaps or runtime proof failure" in cert.reasons[0]


def test_certification_authority_gaps_found_on_deny_terminalization():
    auth = CertificationAuthority()
    tdec = _make_tdec("DENY_TERMINALIZATION", "Contract missing")

    cert = auth.evaluate_certification(
        objective_id="obj-cert-3",
        cycle_id="cyc-cert-3",
        terminalization_decision=tdec,
    )

    assert cert.verdict == "GAPS_FOUND"


def test_completion_authority_maps_certified_to_pass_and_gaps_found_to_fail():
    pass_dec = decision_from_cycle_outcome(
        tenant_id="tenant-1",
        objective_id="obj-cert-4",
        cycle_id="cyc-cert-4",
        terminal_status="CERTIFIED",
        evidence_refs=["evidence://test"],
    )
    assert pass_dec.verdict == "PASS"

    gaps_dec = decision_from_cycle_outcome(
        tenant_id="tenant-1",
        objective_id="obj-cert-5",
        cycle_id="cyc-cert-5",
        terminal_status="GAPS_FOUND",
        evidence_refs=["evidence://test"],
    )
    assert gaps_dec.verdict == "FAIL"
