"""Autonomous Closed-Loop Lane, Packet AL-11: `CycleOutcomeV1` is actually constructed
once a cycle reaches a genuinely terminal state, not just defined in contracts.py."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, StepAttemptV1, StepContractV1, UnderstandingEvidenceV1, UsageV1
from backend.aix_core.runtime.objective.cycle_outcome import build_cycle_outcome, is_genuinely_terminal_phase
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.infrastructure.model_gateway.hyeve_integration_client import ChatCompletionResultV1
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult


def _attempt(outcome: str, step_id: str = "s0") -> StepAttemptV1:
    return StepAttemptV1(
        step_id=step_id, tenant_id="tenant-1", attempt_number=1, move_token="mv_placeholder",
        executor_ref="aix.real_execute.v1", outcome=outcome,
        usage=UsageV1(tokens_in=10, tokens_out=5, cost_usd=0.01, tool_calls=1, time_seconds=1.0),
        completed_at="2026-08-13T00:00:00Z",
    )


class TestIsGenuinelyTerminalPhase:
    def test_completed_for_review_is_terminal(self) -> None:
        assert is_genuinely_terminal_phase(phase="COMPLETED_FOR_REVIEW", terminal_reason=None) is True

    def test_failed_is_terminal(self) -> None:
        assert is_genuinely_terminal_phase(phase="FAILED", terminal_reason=None) is True

    def test_cancelled_is_terminal(self) -> None:
        assert is_genuinely_terminal_phase(phase="CANCELLED", terminal_reason=None) is True

    def test_blocked_with_terminal_only_reason_is_terminal(self) -> None:
        assert is_genuinely_terminal_phase(phase="BLOCKED", terminal_reason="WORKSPACE_NOT_BOUND") is True

    def test_blocked_with_retryable_reason_is_not_terminal(self) -> None:
        # EXECUTOR_MODEL_MISMATCH is retry-eligible -- WorkflowPhase.BLOCKED here is not
        # the "no further edges" terminal state CycleOutcomeV1 is meant to describe.
        assert is_genuinely_terminal_phase(phase="BLOCKED", terminal_reason="EXECUTOR_MODEL_MISMATCH") is False

    def test_running_step_is_not_terminal(self) -> None:
        assert is_genuinely_terminal_phase(phase="RUNNING_STEP", terminal_reason=None) is False

    def test_waiting_approval_is_not_terminal(self) -> None:
        assert is_genuinely_terminal_phase(phase="WAITING_APPROVAL", terminal_reason=None) is False


class TestBuildCycleOutcome:
    def test_completed_outcome_aggregates_usage_and_step_ids(self) -> None:
        outcome = build_cycle_outcome(
            cycle_id="cyc-1", tenant_id="tenant-1", phase="COMPLETED_FOR_REVIEW",
            terminal_reason=None, step_attempts=(_attempt("SUCCEEDED", "s0"), _attempt("SUCCEEDED", "s1")),
        )
        assert outcome.terminal_status == "COMPLETED_FOR_REVIEW"
        assert outcome.completed_step_ids == ("s0", "s1")
        assert outcome.failed_step_ids == ()
        assert outcome.total_usage.tokens_in == 20
        assert outcome.total_usage.cost_usd == pytest.approx(0.02)
        assert outcome.unresolved_risks == ()

    def test_failed_outcome_records_failed_steps_and_risk(self) -> None:
        outcome = build_cycle_outcome(
            cycle_id="cyc-1", tenant_id="tenant-1", phase="FAILED",
            terminal_reason="TESTS_FAILED", step_attempts=(_attempt("FAILED"),),
        )
        assert outcome.terminal_status == "FAILED"
        assert outcome.failed_step_ids == ("s0",)
        assert outcome.unresolved_risks == ("TESTS_FAILED",)
        assert "TESTS_FAILED" in (outcome.review_recommendation or "")

    def test_blocked_outcome_with_no_attempts_records_reason_as_blocked_step(self) -> None:
        outcome = build_cycle_outcome(
            cycle_id="cyc-1", tenant_id="tenant-1", phase="BLOCKED",
            terminal_reason="WORKSPACE_NOT_BOUND", step_attempts=(),
        )
        assert outcome.terminal_status == "BLOCKED"
        assert outcome.blocked_step_ids == ("cycle:WORKSPACE_NOT_BOUND",)

    def test_non_terminal_phase_raises(self) -> None:
        with pytest.raises(ValueError, match="non-terminal phase"):
            build_cycle_outcome(cycle_id="cyc-1", tenant_id="tenant-1", phase="RUNNING_STEP", terminal_reason=None)

    def test_forbidden_acceptance_language_never_appears_in_terminal_status(self) -> None:
        # CycleOutcomeV1's own validator rejects ACCEPT/APPROV/CLOS/MERG in
        # terminal_status -- confirms this module never accidentally constructs one.
        for phase in ("COMPLETED_FOR_REVIEW", "FAILED", "CANCELLED"):
            outcome = build_cycle_outcome(cycle_id="c", tenant_id="t", phase=phase, terminal_reason=None)
            upper = outcome.terminal_status.upper()
            assert not any(bad in upper for bad in ("ACCEPT", "APPROV", "CLOS", "MERG"))


async def _run_real_execute_ok(**kwargs: Any) -> RealExecuteResult:
    return RealExecuteResult(
        ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=True,
        artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
        objective_status="COMPLETED", real_operations=1, target_files_modified=1,
        completion_gates={"gates": {"diff_exists": True, "verification_passed": True}, "verification_verdict": "PASSED"},
        diff_text="+ some real diff\n",
    )


@pytest.mark.asyncio
async def test_completed_for_review_cycle_gets_a_recorded_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={
            "workhub_task_id": "task-101", "objective": "docs only change",
            "task_class": "docs_only_change", "definition_of_done": ("done",),
            "repository_id": "aix-frontend", "allowed_files": ("README.md",), "project_id": "proj-1",
        },
        idempotency_key="admit-1", expected_version=None,
    )
    objective_id = admit["objective_id"]
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=objective_id,
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-1",
        expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-1", expected_version=started["version"],
    )

    assert approved["phase"] == "COMPLETED_FOR_REVIEW"
    outcome = backend._cycle_outcomes.get(started["cycle_id"])  # noqa: SLF001
    assert outcome is not None
    assert outcome.terminal_status == "COMPLETED_FOR_REVIEW"
    assert outcome.cycle_id == started["cycle_id"]

    # Recorded exactly once -- a later idempotent-replay style call must not duplicate it.
    outcome_events = [e for e in backend._cycles[started["cycle_id"]].events if e.get("event_type") == "cycle.outcome_recorded"]  # noqa: SLF001
    assert len(outcome_events) == 1


@pytest.mark.asyncio
async def test_verification_halt_cycle_gets_a_recorded_blocked_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", True
    )

    async def _fake_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        # The verifier model's own schema only ever returns PASS/REPAIR/REPLAN
        # (semantic_verifier.py's _ask_verifier_model rejects any other literal) --
        # verify_step() itself escalates REPAIR -> HALT once repair_budget_remaining
        # is exhausted, set below via retry_budget_remaining = 0.
        return ChatCompletionResultV1(
            output_text=json.dumps({"verdict": "REPAIR", "reasons": ["unrecoverable"], "criteria_results": []}),
            provider="test", resolved_provider="test", model="test-model", usage={},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.verification.semantic_verifier.chat_completion", _fake_chat_completion
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={
            "workhub_task_id": "task-101", "objective": "refactor and optimise FeeDue.php",
            "task_class": "single_file_helper", "definition_of_done": ("done",),
            "repository_id": "aix-frontend", "allowed_files": ("FeeDue.php",), "project_id": "proj-1",
        },
        idempotency_key="admit-1", expected_version=None,
    )
    objective_id = admit["objective_id"]
    backend._understanding_evidence[objective_id] = UnderstandingEvidenceV1(  # noqa: SLF001
        objective_id=objective_id, cycle_id=None, files_read=("FeeDue.php",),
        observed_problem="test evidence", understanding_complete=True,
    )
    backend._step_contracts[objective_id] = StepContractV1(  # noqa: SLF001
        cycle_id="placeholder", tenant_id="tenant-1", step_index=0, purpose="fix it",
        target_scope=("FeeDue.php",), expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("done",), validation_commands=("php -l FeeDue.php",),
        assigned_role="executor",
        step_budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=0, steps=10, concurrency=1),
        # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P3: cover every real obligation this
        # objective admitted with, or the coverage gate refuses with PLAN_INCOMPLETE
        # before this test's own HALT/outcome scenario ever runs.
        obligation_refs=tuple(o.id for o in backend._obligations.get(objective_id, ())),  # noqa: SLF001
        status="PENDING",
    )
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=objective_id,
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-1",
        expected_version=None,
    )
    backend._cycles[started["cycle_id"]].retry_budget_remaining = 0  # noqa: SLF001

    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-1", expected_version=started["version"],
    )

    assert approved["ok"] is False
    assert approved["error"]["code"] == "VERIFICATION_HALT"

    outcome = backend._cycle_outcomes.get(started["cycle_id"])  # noqa: SLF001
    assert outcome is not None
    assert outcome.terminal_status == "BLOCKED"
    assert "VERIFICATION_HALT" in outcome.unresolved_risks
