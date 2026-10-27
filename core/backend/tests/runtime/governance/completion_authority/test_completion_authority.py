"""C003 completion authority contract and adversarial false-completion tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.completion_authority import (
    CANONICAL_EMITTER,
    COMPLETION_AUTHORITY_VERSION,
    CompletionAuthorityError,
    adapter_non_canonical_report,
    decide_completion,
    decision_from_cycle_outcome,
    emitted_decisions,
    reset_completion_audit_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_audit() -> None:
    reset_completion_audit_for_tests()
    yield
    reset_completion_audit_for_tests()


def test_pass_requires_evidence() -> None:
    with pytest.raises(CompletionAuthorityError, match="PASS_REQUIRES_EVIDENCE"):
        decide_completion(
            tenant_id="t1",
            objective_id="obj_1",
            run_id="cyc_1",
            verdict="PASS",
            reasons=("claimed",),
            evidence_refs=(),
        )


def test_pass_with_evidence_is_canonical() -> None:
    decision = decide_completion(
        tenant_id="t1",
        objective_id="obj_1",
        run_id="cyc_1",
        verdict="PASS",
        reasons=("gates_passed",),
        evidence_refs=("trace://abc",),
    )
    assert decision.is_terminal_success
    assert decision.emitter == CANONICAL_EMITTER
    assert decision.authority_version == COMPLETION_AUTHORITY_VERSION
    assert decision.replayable is True
    assert len(emitted_decisions()) == 1


def test_budget_exhausted_blocks_pass() -> None:
    with pytest.raises(CompletionAuthorityError, match="BUDGET_EXHAUSTED"):
        decide_completion(
            tenant_id="t1",
            objective_id="obj_1",
            run_id="cyc_1",
            verdict="PASS",
            reasons=("gates_passed",),
            evidence_refs=("ev1",),
            budget_ok=False,
        )


def test_adapter_cannot_mint_pass() -> None:
    report = adapter_non_canonical_report(
        source="aix_orchestrator_v2",
        tenant_id="t1",
        objective_id="task-1",
        run_id="aixv2-1",
        claimed_status="writeback_complete",
    )
    assert report.verdict == "NON_CANONICAL"
    assert report.is_terminal_success is False
    assert report.emitter.startswith("adapter:")


def test_cycle_outcome_mapping_pass() -> None:
    decision = decision_from_cycle_outcome(
        tenant_id="t1",
        objective_id="obj_1",
        cycle_id="cyc_1",
        terminal_status="CERTIFIED",
        evidence_refs=("trace://x",),
    )
    assert decision.verdict == "PASS"
    assert decision.run_id == "cyc_1"


def test_cycle_outcome_mapping_fail() -> None:
    decision = decision_from_cycle_outcome(
        tenant_id="t1",
        objective_id="obj_1",
        cycle_id="cyc_1",
        terminal_status="FAILED",
        evidence_refs=(),
        reasons=("VERIFICATION_HALT",),
    )
    assert decision.verdict == "FAIL"
    assert decision.is_terminal_success is False


def test_adversarial_false_completion_via_non_canonical_source() -> None:
    """Alternate engine success claim must not equal canonical PASS."""
    fake = adapter_non_canonical_report(
        source="langgraph_orchestration",
        tenant_id="t1",
        objective_id="proj:1",
        run_id="corr-1",
        claimed_status="completed",
    )
    real = decide_completion(
        tenant_id="t1",
        objective_id="obj_1",
        run_id="cyc_1",
        verdict="PASS",
        reasons=("mechanical_pass",),
        evidence_refs=("diff://sha", "tests://exit0"),
    )
    assert fake.verdict != "PASS"
    assert real.verdict == "PASS"
    assert fake.emitter != real.emitter


def test_only_canonical_emitter_string_marks_terminal_success() -> None:
    report = adapter_non_canonical_report(
        source="attacker",
        tenant_id="t1",
        objective_id="obj",
        run_id="run",
        claimed_status="PASS",
    )
    # Even if claimed_status says PASS, verdict and is_terminal_success stay closed.
    assert "PASS" in report.reasons[1]
    assert report.is_terminal_success is False
