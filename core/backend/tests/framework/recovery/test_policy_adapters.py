"""Tests for F6-P1 Recovery Policy Adapters."""

from __future__ import annotations

import pytest

from backend.aix_core.framework.recovery.policies import (
    RepairPolicyAdapter,
    ReplanPolicyAdapter,
    RetryPolicyAdapter,
)
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    StepContractV1,
)
from backend.aix_core.runtime.verification.failure_taxonomy import FailureClassificationV1


def _sample_step() -> StepContractV1:
    return StepContractV1(
        id="step-1",
        cycle_id="cycle-1",
        tenant_id="tenant-1",
        step_index=0,
        purpose="Test step",
        target_scope=("file.py",),
        expected_output_schema_ref="schema://test",
        acceptance_criteria=("criterion 1",),
        validation_commands=("pytest",),
        assigned_role="executor",
        step_budget=BudgetV1(time_seconds=60, tokens=10000, cost_usd=1.0, retries=2, steps=1, concurrency=1),
        status="PENDING",
    )


def test_retry_policy_adapter_authorized():
    classification = FailureClassificationV1(
        failure_class="RETRYABLE",
        reason_codes=("NETWORK_TIMEOUT",),
    )
    budget = BudgetV1(time_seconds=60, tokens=10000, cost_usd=1.0, retries=2, steps=1, concurrency=1)

    decision, action = RetryPolicyAdapter.authorize_retry(
        classification,
        budget,
        current_attempt_number=1,
        step_id="step-1",
        failure_ref="ev://fail",
    )

    assert decision.disposition == "AUTHORIZED"
    assert decision.next_attempt_number == 2
    assert action is not None
    assert action.step_id == "step-1"
    assert action.next_attempt == 2
    assert action.budget_after == 1  # 2 - 1 = 1 retry remaining


def test_retry_policy_adapter_denied_exhausted_budget():
    classification = FailureClassificationV1(
        failure_class="RETRYABLE",
        reason_codes=("NETWORK_TIMEOUT",),
    )
    budget = BudgetV1(time_seconds=60, tokens=10000, cost_usd=1.0, retries=0, steps=1, concurrency=1)

    decision, action = RetryPolicyAdapter.authorize_retry(
        classification,
        budget,
        current_attempt_number=1,
        step_id="step-1",
        failure_ref="ev://fail",
    )

    assert decision.disposition == "DENIED"
    assert action is None


def test_repair_policy_adapter():
    step = _sample_step()
    classification = FailureClassificationV1(
        failure_class="REPAIRABLE",
        reason_codes=("SYNTAX_ERROR",),
    )
    budget = BudgetV1(time_seconds=60, tokens=10000, cost_usd=1.0, retries=2, steps=1, concurrency=1)
    retry_dec, _ = RetryPolicyAdapter.authorize_retry(
        classification, budget, current_attempt_number=1, step_id=step.id
    )

    directive, action = RepairPolicyAdapter.build_repair(
        step=step,
        reasons=("SyntaxError at line 10",),
        retry_decision=retry_dec,
    )

    assert directive.step_id == step.id
    assert action is not None
    assert action.retry.next_attempt == 2
    assert action.directive == directive


def test_replan_policy_adapter_authorized_and_exhausted():
    classification = FailureClassificationV1(
        failure_class="REPLAN_REQUIRED",
        reason_codes=("INVALID_SCOPE",),
    )

    # Attempt 1: 0 replans used -> authorized
    auth1, action1 = ReplanPolicyAdapter.authorize_replan(classification, replans_used=0, max_replans=2)
    assert auth1.disposition == "AUTHORIZED"
    assert action1 is not None
    assert action1.budget_after == 1  # 2 - 0 - 1 = 1 remaining

    # Attempt 2: 2 replans used -> denied
    auth2, action2 = ReplanPolicyAdapter.authorize_replan(classification, replans_used=2, max_replans=2)
    assert auth2.disposition == "DENIED"
    assert action2 is None
