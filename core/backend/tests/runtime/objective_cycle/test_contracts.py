"""AIX-E2E-CYCLE-001A-W03/W04/W05 contract tests: deterministic serialization, legal
values, and hostile/fail-closed invariants."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backend.aix_core.runtime.objective.contracts import (
    FORBIDDEN_EFFECTS,
    BudgetV1,
    CycleOutcomeV1,
    ExecutionCycleV1,
    ObjectiveEnvelopeV1,
    ReplanDecisionV1,
    StepAttemptV1,
    StepContractV1,
    UsageV1,
)
from backend.aix_core.runtime.governance.policy import _FORBIDDEN_EFFECTS as _POLICY_FORBIDDEN_EFFECTS


def _budget(**overrides: object) -> BudgetV1:
    base = {"time_seconds": 3600, "tokens": 100_000, "cost_usd": 5.0, "retries": 2, "steps": 10, "concurrency": 1}
    base.update(overrides)
    return BudgetV1(**base)


def _objective(**overrides: object) -> ObjectiveEnvelopeV1:
    base = dict(
        tenant_id="tenant-1",
        actor_ref="user:alice",
        project_id="project-1",
        workhub_task_ref="wh-task-1",
        task_class="docs_only_change",
        definition_of_done="Docs updated and reviewed.",
        acceptance_criteria=("docs updated",),
        repository_scope=("AIX",),
        budget_ceiling=_budget(),
        idempotency_key="idem-1",
    )
    base.update(overrides)
    return ObjectiveEnvelopeV1(**base)


class TestForbiddenEffectsLockstep:
    def test_contracts_forbidden_effects_matches_policy(self) -> None:
        assert FORBIDDEN_EFFECTS == _POLICY_FORBIDDEN_EFFECTS, (
            "contracts.FORBIDDEN_EFFECTS has drifted from policy._FORBIDDEN_EFFECTS; "
            "these must be kept in lockstep by hand since they are independent fail-closed checks"
        )


class TestObjectiveEnvelopeV1:
    def test_deterministic_serialization_round_trip(self) -> None:
        objective = _objective()
        dumped = objective.model_dump(mode="json")
        restored = ObjectiveEnvelopeV1.model_validate(dumped)
        assert restored == objective
        # Deterministic: dumping twice produces byte-identical JSON.
        assert json.dumps(dumped, sort_keys=True) == json.dumps(
            restored.model_dump(mode="json"), sort_keys=True
        )

    def test_unsupported_task_class_fails_closed(self) -> None:
        with pytest.raises(ValidationError):
            _objective(task_class="delete_production_database")

    def test_missing_acceptance_criteria_fails_closed(self) -> None:
        with pytest.raises(ValidationError):
            _objective(acceptance_criteria=())

    def test_allowed_effects_cannot_include_forbidden_effect(self) -> None:
        with pytest.raises(ValidationError):
            _objective(allowed_effects=("auto_accept",))

    def test_production_environment_scope_rejected_in_v1(self) -> None:
        with pytest.raises(ValidationError):
            _objective(environment_scope="production")

    def test_extra_field_fails_closed(self) -> None:
        payload = _objective().model_dump(mode="json")
        payload["unexpected_field"] = "should not be silently accepted"
        with pytest.raises(ValidationError):
            ObjectiveEnvelopeV1.model_validate(payload)

    def test_is_frozen(self) -> None:
        objective = _objective()
        with pytest.raises(ValidationError):
            objective.tenant_id = "tenant-2"  # type: ignore[misc]


class TestExecutionCycleV1:
    def test_valid_cycle(self) -> None:
        cycle = ExecutionCycleV1(
            objective_id="obj-1",
            tenant_id="tenant-1",
            status="PLANNING",
            step_ids=("step-1", "step-2"),
            current_step_cursor="step-1",
            consumed_budget=_budget(time_seconds=0, tokens=0, cost_usd=0.0, retries=0, steps=0),
        )
        assert cycle.status == "PLANNING"

    def test_cursor_must_reference_known_step(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionCycleV1(
                objective_id="obj-1",
                tenant_id="tenant-1",
                status="PLANNING",
                step_ids=("step-1",),
                current_step_cursor="step-does-not-exist",
                consumed_budget=_budget(time_seconds=0, tokens=0, cost_usd=0.0, retries=0, steps=0),
            )

    def test_stop_the_line_requires_reason(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionCycleV1(
                objective_id="obj-1",
                tenant_id="tenant-1",
                status="BLOCKED",
                consumed_budget=_budget(time_seconds=0, tokens=0, cost_usd=0.0, retries=0, steps=0),
                stop_the_line_active=True,
            )


class TestStepContractV1:
    def _step(self, **overrides: object) -> StepContractV1:
        base = dict(
            cycle_id="cycle-1",
            tenant_id="tenant-1",
            step_index=0,
            purpose="Update the README.",
            target_scope=("README.md",),
            expected_output_schema_ref="schema://step-output/v1",
            acceptance_criteria=("README mentions the new flag",),
            validation_commands=("pytest -q",),
            assigned_role="executor",
            step_budget=_budget(time_seconds=600, tokens=10_000, cost_usd=1.0, retries=1, steps=1),
            status="PENDING",
        )
        base.update(overrides)
        return StepContractV1(**base)

    def test_valid_step(self) -> None:
        step = self._step()
        assert step.status == "PENDING"

    def test_self_dependency_fails_closed(self) -> None:
        step_id = "step-fixed-id"
        with pytest.raises(ValidationError):
            self._step(id=step_id, depends_on=(step_id,))


class TestStepAttemptV1:
    def _attempt(self, **overrides: object) -> StepAttemptV1:
        base = dict(
            step_id="step-1",
            tenant_id="tenant-1",
            attempt_number=1,
            move_token="move-token-1",
            executor_ref="executor:aider",
            outcome="IN_PROGRESS",
        )
        base.update(overrides)
        return StepAttemptV1(**base)

    def test_in_progress_does_not_require_completed_at(self) -> None:
        attempt = self._attempt()
        assert attempt.completed_at is None

    def test_terminal_outcome_requires_completed_at(self) -> None:
        with pytest.raises(ValidationError):
            self._attempt(outcome="SUCCEEDED", completed_at=None)

    def test_terminal_outcome_with_completed_at_is_valid(self) -> None:
        attempt = self._attempt(
            outcome="SUCCEEDED",
            completed_at="2026-07-29T00:00:00Z",
            usage=UsageV1(tokens_in=100, tokens_out=50, cost_usd=0.01, tool_calls=2, time_seconds=12.5),
        )
        assert attempt.usage.tokens_in == 100


class TestReplanDecisionV1:
    def _replan(self, **overrides: object) -> ReplanDecisionV1:
        base = dict(
            cycle_id="cycle-1",
            tenant_id="tenant-1",
            triggering_evidence_ref="evidence://attempt-1",
            reason="Step 2 failed verification twice.",
            retained_step_ids=("step-1",),
            added_step_ids=(),
            scope_comparison="No new files touched; same repository scope.",
            policy_decision_ref="policy-decision-1",
            decided_by="aix-replan-agent",
        )
        base.update(overrides)
        return ReplanDecisionV1(**base)

    def test_valid_replan_without_scope_widening(self) -> None:
        replan = self._replan(requires_approval=False)
        assert replan.requires_approval is False

    def test_scope_widening_requires_approval(self) -> None:
        with pytest.raises(ValidationError):
            self._replan(added_step_ids=("step-new-1",), requires_approval=False)

    def test_scope_widening_with_approval_is_valid(self) -> None:
        replan = self._replan(added_step_ids=("step-new-1",), requires_approval=True)
        assert "step-new-1" in replan.added_step_ids


class TestCycleOutcomeV1:
    def test_valid_terminal_outcome(self) -> None:
        outcome = CycleOutcomeV1(
            cycle_id="cycle-1",
            tenant_id="tenant-1",
            terminal_status="COMPLETED_FOR_REVIEW",
            completed_step_ids=("step-1", "step-2"),
            objective_acceptance_map={"docs updated": True},
        )
        assert outcome.terminal_status == "COMPLETED_FOR_REVIEW"

    @pytest.mark.parametrize("terminal_status", ["BLOCKED", "FAILED", "CANCELLED", "REJECTED"])
    def test_all_non_review_terminal_states_are_valid(self, terminal_status: str) -> None:
        outcome = CycleOutcomeV1(cycle_id="cycle-1", tenant_id="tenant-1", terminal_status=terminal_status)
        assert outcome.terminal_status == terminal_status

    @pytest.mark.parametrize(
        "non_terminal_status",
        ["RECEIVED", "ADMITTED", "GROUNDING", "PLANNING", "READY", "RUNNING_STEP"],
    )
    def test_non_terminal_status_rejected(self, non_terminal_status: str) -> None:
        with pytest.raises(ValidationError):
            CycleOutcomeV1(cycle_id="cycle-1", tenant_id="tenant-1", terminal_status=non_terminal_status)

    def test_workhub_acceptance_terminal_status_is_impossible(self) -> None:
        # CycleStatus itself has no ACCEPTED/APPROVED/CLOSED/MERGED value, so this is
        # rejected by the Literal type before the semantic validator ever runs — this
        # test documents that guarantee rather than exercising the validator directly.
        with pytest.raises(ValidationError):
            CycleOutcomeV1(cycle_id="cycle-1", tenant_id="tenant-1", terminal_status="ACCEPTED")
