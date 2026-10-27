"""Tests for P7 (incremental replan wiring), P9 (evidence chain), P10 (failure taxonomy in executor)."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, RiskTier, StepContractV1

_BUDGET = BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=3, steps=10, concurrency=1)


def _step(sid: str, depends_on: tuple[str, ...] = (), status: str = "PENDING", idx: int = 0) -> StepContractV1:
    return StepContractV1(
        id=sid, cycle_id="cyc-1", tenant_id="t1", step_index=idx,
        purpose=f"Step {sid}", depends_on=depends_on,
        target_scope=("src/app.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("done",), validation_commands=("pytest",),
        assigned_role="executor", risk_tier=RiskTier.LOW,
        step_budget=_BUDGET, required_evidence=("file://diff",),
        status=status,
    )


# ── P7: DAG-aware replan ──────────────────────────────────────────────────────

class TestIncrementalReplanDAG:
    def test_transitive_dependents_linear_chain(self):
        from backend.aix_core.runtime.planner.incremental_replan import find_transitive_dependents

        steps = [_step("s1", idx=0), _step("s2", ("s1",), idx=1), _step("s3", ("s2",), idx=2)]
        deps = find_transitive_dependents("s1", steps)
        assert deps == {"s2", "s3"}

    def test_transitive_dependents_independent_branch(self):
        from backend.aix_core.runtime.planner.incremental_replan import find_transitive_dependents

        steps = [_step("s1", idx=0), _step("s2", ("s1",), idx=1), _step("s3", idx=2)]
        deps = find_transitive_dependents("s1", steps)
        assert deps == {"s2"}, "s3 should not be affected since it has no dependency on s1"

    def test_perform_incremental_replan_preserves_verified(self):
        from backend.aix_core.runtime.planner.incremental_replan import perform_incremental_replan

        steps = (
            _step("s1", status="VERIFIED", idx=0),
            _step("s2", ("s1",), status="FAILED", idx=1),
            _step("s3", ("s2",), status="PENDING", idx=2),
        )
        result = perform_incremental_replan(
            failed_step_id="s2", existing_steps=steps, replacement_steps=(),
        )
        assert "s1" in result.preserved_verified_step_ids
        assert "s2" in result.superseded_step_ids
        assert "s3" in result.superseded_step_ids


# ── P9: Evidence chain from audit ────────────────────────────────────────────

class TestEvidenceChainIntegration:
    def test_audit_events_build_evidence_chain(self):
        from backend.aix_core.runtime.foundation.audit import (
            clear_audit_log,
            audit_capability_requested,
            audit_authorization_decision,
            audit_execution_started,
            audit_execution_completed,
            get_evidence_chain,
        )
        from backend.aix_core.runtime.foundation.execution_context import (
            ExecutionContextV1,
            PrincipalV1,
        )
        from backend.aix_core.runtime.objective.contracts import (
            ActionRequestV1,
            ActionObservationV1,
        )
        from backend.aix_core.runtime.foundation.policy_gateway import PolicyDecision

        clear_audit_log()
        ctx = ExecutionContextV1(
            principal=PrincipalV1(type="agent", id="coder-1", role="coder"),
            tenant_id="t1",
            project_id="p1",
            repository_id="repo1",
            cycle_id="cycle-42",
            step_id="step-1",
            attempt_id="att-1",
        )
        req = ActionRequestV1(capability="filesystem", operation="mkdir", arguments={"path": "src/new"})
        decision = PolicyDecision(
            allowed=True, capability="filesystem", operation="mkdir",
            code="ALLOW", detail="allowed",
        )
        obs = ActionObservationV1(
            action_id=req.action_id, status="SUCCESS", changed=True,
            observation={"created": True}, evidence=(),
        )

        audit_capability_requested(req, ctx)
        audit_authorization_decision(req, decision, ctx)
        audit_execution_started(req, ctx)
        audit_execution_completed(req, obs, ctx)

        chain = get_evidence_chain("cycle-42")
        digest = chain.compute_chain_digest()
        assert digest, "chain digest must be non-empty"
        chain.validate_chain_integrity(digest)

    def test_evidence_chain_tamper_detection(self):
        from backend.aix_core.runtime.foundation.audit import (
            clear_audit_log,
            audit_capability_requested,
            get_evidence_chain,
        )
        from backend.aix_core.runtime.foundation.execution_context import (
            ExecutionContextV1,
            PrincipalV1,
        )
        from backend.aix_core.runtime.objective.contracts import ActionRequestV1
        from backend.aix_core.runtime.verification.evidence_graph import EvidenceGraphError

        clear_audit_log()
        ctx = ExecutionContextV1(
            principal=PrincipalV1(type="agent", id="coder-1", role="coder"),
            tenant_id="t1", project_id="p1", repository_id="repo1",
            cycle_id="cycle-99", step_id="s1", attempt_id="a1",
        )
        req = ActionRequestV1(capability="filesystem", operation="read", arguments={"path": "x"})
        audit_capability_requested(req, ctx)

        chain = get_evidence_chain("cycle-99")
        with pytest.raises(EvidenceGraphError):
            chain.validate_chain_integrity("tampered-digest")


# ── P10: Failure taxonomy in capability executor ─────────────────────────────

class TestFailureTaxonomyIntegration:
    def test_classify_success_returns_none(self):
        from backend.aix_core.runtime.foundation.capability_executor import classify_action_failure
        from backend.aix_core.runtime.objective.contracts import ActionObservationV1

        obs = ActionObservationV1(
            action_id="a1", status="SUCCESS", changed=True,
            observation={}, evidence=(),
        )
        assert classify_action_failure(obs) is None

    def test_classify_policy_denied_failure(self):
        from backend.aix_core.runtime.foundation.capability_executor import classify_action_failure
        from backend.aix_core.runtime.objective.contracts import ActionObservationV1

        obs = ActionObservationV1(
            action_id="a1", status="FAILED", changed=False,
            observation={}, evidence=(),
            error_code="POLICY_DENIED",
        )
        result = classify_action_failure(obs, attempt_number=1, max_attempts=3)
        assert result is not None
        assert result.failure_class in ("REPLAN_REQUIRED", "BLOCKED", "REPAIRABLE")

    def test_classify_timeout_is_retryable(self):
        from backend.aix_core.runtime.foundation.capability_executor import classify_action_failure
        from backend.aix_core.runtime.objective.contracts import ActionObservationV1

        obs = ActionObservationV1(
            action_id="a1", status="FAILED", changed=False,
            observation={}, evidence=(),
            error_code="TIMEOUT",
        )
        result = classify_action_failure(obs, attempt_number=1, max_attempts=3)
        assert result is not None
        assert result.failure_class == "RETRYABLE"

    def test_classify_budget_exhausted_becomes_terminal(self):
        from backend.aix_core.runtime.foundation.capability_executor import classify_action_failure
        from backend.aix_core.runtime.objective.contracts import ActionObservationV1

        obs = ActionObservationV1(
            action_id="a1", status="FAILED", changed=False,
            observation={}, evidence=(),
            error_code="SOME_ERROR",
        )
        result = classify_action_failure(
            obs, attempt_number=3, max_attempts=3, replans_used=2, max_replans=2,
        )
        assert result is not None
        assert result.failure_class == "TERMINAL"
