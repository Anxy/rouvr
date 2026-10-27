"""AIX Feature 12 (Packet 02) — Unit tests for TerminalizationGuard & Gating."""

import pytest
from backend.aix_core.runtime.verification.verification_decision import (
    VerificationDecisionV1,
)
from backend.aix_core.runtime.verification.terminalization_guard import (
    GuardsEvaluated,
    TerminalizationDecisionV1,
    evaluate_terminalization,
)
from backend.workflows.temporal.objective_cycle_workflow import (
    AixObjectiveCycleWorkflowEngine,
    ObjectiveCycleWorkflowInput,
)
from backend.aix_core.runtime.governance.completion_authority import (
    decision_from_cycle_outcome,
)


def _make_engine(tenant_id: str = "tenant-1", objective_id: str = "obj-1", cycle_id: str = "cyc-1") -> AixObjectiveCycleWorkflowEngine:
    workflow_input = ObjectiveCycleWorkflowInput(
        workflow_version=1,
        tenant_id=tenant_id,
        objective_id=objective_id,
        cycle_id=cycle_id,
        candidate_sha="test-sha",
        plan_sha256="0" * 64,
        start_request_id="req-1",
        correlation_id="corr-1",
    )
    return AixObjectiveCycleWorkflowEngine(workflow_input)


def test_terminalization_guard_denies_without_runtime_pass():
    vdec = VerificationDecisionV1(
        decision_id="vdec-1",
        verification_id="v-1",
        objective_id="obj-1",
        decision="REPAIR",
        reasoning="Runtime test failure",
        blocking_gaps=["gap-1"],
    )
    guards = GuardsEvaluated(
        all_required_steps_verified=True,
        objective_runtime_verification="FAIL",
        blocking_gaps=1,
        required_evidence_complete=True,
        ovc_coverage_complete=True,
    )
    tdec = evaluate_terminalization(
        verification_decision=vdec,
        guards=guards,
        current_phase="RUNNING_STEP",
    )
    assert tdec.decision == "DENY_NEEDS_REPAIR"
    assert "Terminalization denied" in tdec.reasoning


def test_terminalization_guard_allows_certified_on_full_conjunction():
    vdec = VerificationDecisionV1(
        decision_id="vdec-2",
        verification_id="v-2",
        objective_id="obj-2",
        decision="PASS",
        reasoning="All checks passed",
        blocking_gaps=[],
    )
    guards = GuardsEvaluated(
        all_required_steps_verified=True,
        objective_runtime_verification="PASS",
        blocking_gaps=0,
        required_evidence_complete=True,
        ovc_coverage_complete=True,
    )
    tdec = evaluate_terminalization(
        verification_decision=vdec,
        guards=guards,
        current_phase="RUNNING_STEP",
    )
    assert tdec.decision == "ALLOW_CERTIFIED"


def test_continue_after_approval_denies_on_guard_failure():
    engine = _make_engine(objective_id="obj-1", cycle_id="cyc-1")
    engine.run_until_wait_or_terminal()
    engine.signal_approval(approval_ref="app-1", valid=True)
    assert engine.state.phase == "RUNNING_STEP"

    vdec = VerificationDecisionV1(
        decision_id="vdec-3",
        verification_id="v-3",
        objective_id="obj-1",
        decision="REPAIR",
        reasoning="SQLite missing",
        blocking_gaps=["gap-sqlite"],
    )
    guards = GuardsEvaluated(
        all_required_steps_verified=True,
        objective_runtime_verification="FAIL",
        blocking_gaps=1,
        required_evidence_complete=False,
        ovc_coverage_complete=False,
    )
    tdec = evaluate_terminalization(
        verification_decision=vdec,
        guards=guards,
        current_phase="RUNNING_STEP",
    )
    assert tdec.decision == "DENY_NEEDS_REPAIR"

    # continue_after_approval with denied decision must land GAPS_FOUND
    engine.continue_after_approval(decision=tdec, verification_decision=vdec)
    assert engine.state.phase == "GAPS_FOUND"
    assert any(h.get("event") == "terminalization_consumed" for h in engine.state.history)


def test_continue_after_approval_allows_on_guard_pass():
    engine = _make_engine(objective_id="obj-2", cycle_id="cyc-2")
    engine.run_until_wait_or_terminal()
    engine.signal_approval(approval_ref="app-2", valid=True)
    assert engine.state.phase == "RUNNING_STEP"

    vdec = VerificationDecisionV1(
        decision_id="vdec-4",
        verification_id="v-4",
        objective_id="obj-2",
        decision="PASS",
        reasoning="Everything passed",
    )
    guards = GuardsEvaluated(
        all_required_steps_verified=True,
        objective_runtime_verification="PASS",
        blocking_gaps=0,
        required_evidence_complete=True,
        ovc_coverage_complete=True,
    )
    tdec = evaluate_terminalization(
        verification_decision=vdec,
        guards=guards,
        current_phase="RUNNING_STEP",
    )
    assert tdec.decision == "ALLOW_CERTIFIED"

    engine.continue_after_approval(decision=tdec, verification_decision=vdec)
    assert engine.state.phase == "CERTIFIED"


def test_completion_authority_blocks_cfr_without_allow_certified():
    # Without ALLOW_CERTIFIED decision
    decision_no_cert = decision_from_cycle_outcome(
        tenant_id="tenant-1",
        objective_id="obj-1",
        cycle_id="cyc-1",
        terminal_status="COMPLETED_FOR_REVIEW",
        evidence_refs=["evidence://test"],
    )
    assert decision_no_cert.verdict == "INSUFFICIENT_EVIDENCE"

    # With ALLOW_CERTIFIED decision
    tdec_allow = TerminalizationDecisionV1(
        decision_id="tdec-1",
        verification_decision_id="vdec-1",
        objective_id="obj-1",
        decision="ALLOW_CERTIFIED",
        guards_evaluated=GuardsEvaluated(
            all_required_steps_verified=True,
            objective_runtime_verification="PASS",
            blocking_gaps=0,
            required_evidence_complete=True,
            ovc_coverage_complete=True,
        ),
        reasoning="Fully certified",
    )
    decision_cert = decision_from_cycle_outcome(
        tenant_id="tenant-1",
        objective_id="obj-1",
        cycle_id="cyc-1",
        terminal_status="COMPLETED_FOR_REVIEW",
        evidence_refs=["evidence://test"],
        terminalization_decision=tdec_allow,
    )
    assert decision_cert.verdict == "PASS"
