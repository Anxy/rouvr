"""Tests for F5-P3 EvidenceBundle & Mapping Helpers."""

from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from backend.aix_core.framework.verification.contract import (
    GateResult,
    VerificationCheck,
    VerificationOutcome,
)
from backend.aix_core.framework.verification.evidence import (
    EvidenceBundle,
    EvidenceBundleBuilder,
    aggregate_outcome,
    build_checks_from_completion_gates,
    build_checks_from_read_verification,
)


def test_evidence_bundle_builder_sealing():
    builder = EvidenceBundleBuilder(
        tenant_id="tenant-1",
        objective_id="obj-100",
        cycle_id="cycle-200",
        step_id="step-300",
        attempt_id="att-400",
    )
    builder.add_artifact_ref("art://1")
    builder.add_artifact_ref("art://2")
    builder.set_diff_ref("diff://sha256:abc")
    builder.add_test_ref("test://report1")
    builder.add_observation_ref("obs://1")
    builder.add_gate_result(GateResult(gate_id="continuity_law_c", passed=True))

    bundle = builder.seal()

    assert bundle.tenant_id == "tenant-1"
    assert bundle.objective_id == "obj-100"
    assert bundle.cycle_id == "cycle-200"
    assert bundle.step_id == "step-300"
    assert bundle.attempt_id == "att-400"
    assert bundle.artifact_refs == ("art://1", "art://2")
    assert bundle.diff_ref == "diff://sha256:abc"
    assert bundle.test_refs == ("test://report1",)
    assert bundle.observation_refs == ("obs://1",)
    assert len(bundle.gate_results) == 1

    # Cannot seal twice
    with pytest.raises(RuntimeError, match="already been sealed"):
        builder.seal()


def test_evidence_bundle_canonical_hash_determinism():
    # Bundle A
    b1 = (
        EvidenceBundleBuilder(
            tenant_id="tenant-1",
            objective_id="obj-1",
            cycle_id="cyc-1",
            step_id="step-1",
            attempt_id="att-1",
        )
        .add_artifact_ref("art://b")
        .add_artifact_ref("art://a")
        .set_diff_ref("diff://123")
        .seal()
    )

    # Bundle B (inserted in different order)
    b2 = (
        EvidenceBundleBuilder(
            tenant_id="tenant-1",
            objective_id="obj-1",
            cycle_id="cyc-1",
            step_id="step-1",
            attempt_id="att-1",
        )
        .add_artifact_ref("art://a")
        .add_artifact_ref("art://b")
        .set_diff_ref("diff://123")
        .seal()
    )

    assert b1.canonical_hash() == b2.canonical_hash()
    assert len(b1.canonical_hash()) == 64


def test_evidence_bundle_immutability():
    bundle = (
        EvidenceBundleBuilder(
            tenant_id="tenant-1",
            objective_id="obj-1",
            cycle_id="cyc-1",
            step_id="step-1",
            attempt_id="att-1",
        ).seal()
    )

    with pytest.raises(FrozenInstanceError):
        bundle.tenant_id = "mutated"  # type: ignore[misc]


def test_build_checks_from_read_verification():
    v_data = {
        "resource_resolved": True,
        "file_read_non_empty": True,
        "target_resource_accessed": True,
        "secret_values_redacted": False,
    }

    checks = build_checks_from_read_verification(v_data, evidence_ref="ev://read")
    assert len(checks) == 4
    assert checks[0].check_type == "resource_resolved"
    assert checks[0].status == "PASSED"
    assert checks[3].check_type == "secret_values_redacted"
    assert checks[3].status == "FAILED"
    assert checks[3].failure_reason == "secret_values_redacted was not satisfied"

    outcome = aggregate_outcome(checks)
    assert outcome.all_mandatory_passed is False
    assert outcome.failed_mandatory == ("secret_values_redacted",)


def test_build_checks_from_completion_gates():
    gates = {
        "gates": {
            "source_read": True,
            "source_changed": True,
            "secret_leak": False,
        },
        "verification_verdict": "PASSED",
    }

    checks = build_checks_from_completion_gates(gates, evidence_ref="ev://gate")
    assert len(checks) == 4
    assert any(c.check_type == "diff_matches_expected" and c.status == "PASSED" for c in checks)
    assert any(c.check_type == "secret_leak" and c.status == "FAILED" for c in checks)
