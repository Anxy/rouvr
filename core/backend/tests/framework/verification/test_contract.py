"""Tests for F5-P1 VerificationContract & Core Types."""

from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from backend.aix_core.framework.verification.contract import (
    GateContext,
    GateResult,
    VERIFICATION_TYPES,
    VERIFIER_VERSION,
    VerificationCheck,
    VerificationContract,
    VerificationOutcome,
)
from backend.aix_core.runtime.objective.contracts import (
    AgentRole,
    BudgetV1,
    StepContractV1,
    StepStatus,
)


def _build_step(
    *,
    step_id: str = "step-1",
    cycle_id: str = "cycle-100",
    acceptance_criteria: tuple[str, ...] = ("tests must pass", "no secrets leaked"),
    expected_checks: tuple[str, ...] = ("test_suite_passed",),
    required_evidence: tuple[str, ...] = ("diff", "test_report"),
    facet_refs: tuple[str, ...] = (),
    operation_refs: tuple[str, ...] = (),
    entity_refs: tuple[str, ...] = (),
    mapping_refs: tuple[str, ...] = (),
    obligation_refs: tuple[str, ...] = (),
    outcome_contract_id: str | None = None,
    outcome_contract_hash: str | None = None,
) -> StepContractV1:
    return StepContractV1(
        id=step_id,
        cycle_id=cycle_id,
        tenant_id="tenant-1",
        step_index=0,
        purpose="Implement auth middleware",
        target_scope=("backend/auth.py",),
        expected_output_schema_ref="schema://test",
        acceptance_criteria=acceptance_criteria,
        validation_commands=("pytest tests/test_auth.py",),
        assigned_role="executor",
        step_budget=BudgetV1(time_seconds=60, tokens=10000, cost_usd=1.0, retries=1, steps=1, concurrency=1),
        status="PENDING",
        expected_checks=expected_checks,
        required_evidence=required_evidence,
        facet_refs=facet_refs,
        operation_refs=operation_refs,
        entity_refs=entity_refs,
        mapping_refs=mapping_refs,
        obligation_refs=obligation_refs,
        outcome_contract_id=outcome_contract_id,
        outcome_contract_hash=outcome_contract_hash,
    )


def test_verification_contract_for_step_exact_mapping():
    step = _build_step(
        step_id="step-42",
        cycle_id="cycle-999",
        acceptance_criteria=("criterion 1", "criterion 2"),
        expected_checks=("check_a", "check_b"),
        required_evidence=("ev_1",),
        facet_refs=("facet.auth",),
        operation_refs=("op.login",),
        entity_refs=("entity.user",),
        mapping_refs=("map.user_token",),
        obligation_refs=("ob.1",),
        outcome_contract_id="outcome-123",
        outcome_contract_hash="hash-abc",
    )

    contract = VerificationContract.for_step(step)

    assert contract.step_id == "step-42"
    assert contract.cycle_id == "cycle-999"
    assert contract.objective_id == "cycle-999"
    assert contract.criteria == ("criterion 1", "criterion 2")
    assert contract.expected_checks == ("check_a", "check_b")
    assert contract.required_evidence == ("ev_1",)
    assert contract.facet_refs == ("facet.auth",)
    assert contract.operation_refs == ("op.login",)
    assert contract.entity_refs == ("entity.user",)
    assert contract.mapping_refs == ("map.user_token",)
    assert contract.obligation_refs == ("ob.1",)
    assert contract.outcome_contract_id == "outcome-123"
    assert contract.outcome_contract_hash == "hash-abc"
    assert contract.continuity_required is True
    assert len(contract.contract_id) > 0


def test_verification_contract_empty_continuity_refs():
    step = _build_step()
    contract = VerificationContract.for_step(step)

    assert contract.continuity_required is False
    assert contract.facet_refs == ()
    assert contract.operation_refs == ()
    assert contract.entity_refs == ()
    assert contract.mapping_refs == ()
    assert contract.obligation_refs == ()


def test_verification_contract_frozen_immutability():
    step = _build_step()
    contract = VerificationContract.for_step(step)

    with pytest.raises(FrozenInstanceError):
        contract.step_id = "mutated"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        contract.criteria = ("new",)  # type: ignore[misc]


def test_verification_check_serialization():
    check = VerificationCheck(
        check_type="diff_matches_expected",
        mandatory=True,
        status="PASSED",
        evidence_ref="evidence://ref1",
    )

    data = check.to_dict()
    assert data["check_type"] == "diff_matches_expected"
    assert data["mandatory"] is True
    assert data["status"] == "PASSED"
    assert data["evidence_ref"] == "evidence://ref1"
    assert data["failure_reason"] is None
    assert data["verifier_version"] == VERIFIER_VERSION
    assert "started_at" in data
    assert "completed_at" in data


def test_verification_outcome_serialization():
    c1 = VerificationCheck(check_type="diff_matches_expected", mandatory=True, status="PASSED")
    c2 = VerificationCheck(check_type="test_suite_passed", mandatory=True, status="FAILED", failure_reason="Exit 1")

    outcome = VerificationOutcome(
        checks=(c1, c2),
        all_mandatory_passed=False,
        failed_mandatory=("test_suite_passed",),
    )

    data = outcome.to_dict()
    assert data["all_mandatory_passed"] is False
    assert data["failed_mandatory"] == ["test_suite_passed"]
    assert len(data["checks"]) == 2


def test_gate_result_and_context_immutability():
    step = _build_step()
    contract = VerificationContract.for_step(step)

    context = GateContext(contract=contract, diff_text="--- +++")
    assert context.contract == contract
    assert context.diff_text == "--- +++"

    with pytest.raises(FrozenInstanceError):
        context.diff_text = "new diff"  # type: ignore[misc]

    result = GateResult(gate_id="continuity_law_c", passed=True, mandatory=True, immutable=True)
    assert result.gate_id == "continuity_law_c"
    assert result.passed is True
    assert result.immutable is True

    with pytest.raises(FrozenInstanceError):
        result.passed = False  # type: ignore[misc]
