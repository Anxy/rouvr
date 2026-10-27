"""AIX-E2E-CYCLE-001H-W06 bounded replan tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.verification.failure_taxonomy import FailureClassificationV1
from backend.aix_core.runtime.recovery.replan import (
    BoundedReplanError,
    BoundedReplanPolicy,
)


class TestAuthorize:
    def test_replan_required_within_budget_authorized(self) -> None:
        policy = BoundedReplanPolicy()
        classification = FailureClassificationV1("REPLAN_REQUIRED", ("ATTEMPT_BUDGET_EXHAUSTED",))
        authorization = policy.authorize(classification, replans_used=0, max_replans=1)
        assert authorization.disposition == "AUTHORIZED"

    def test_replan_budget_exhausted_denied(self) -> None:
        policy = BoundedReplanPolicy()
        classification = FailureClassificationV1("REPLAN_REQUIRED", ("ATTEMPT_BUDGET_EXHAUSTED",))
        authorization = policy.authorize(classification, replans_used=1, max_replans=1)
        assert authorization.disposition == "DENIED"

    @pytest.mark.parametrize("failure_class", ["RETRYABLE", "REPAIRABLE", "BLOCKED", "TERMINAL"])
    def test_non_replan_class_denied(self, failure_class: str) -> None:
        policy = BoundedReplanPolicy()
        classification = FailureClassificationV1(failure_class, ("X",))  # type: ignore[arg-type]
        authorization = policy.authorize(classification, replans_used=0, max_replans=5)
        assert authorization.disposition == "DENIED"


class TestPropose:
    def _authorized(self) -> object:
        policy = BoundedReplanPolicy()
        classification = FailureClassificationV1("REPLAN_REQUIRED", ("ATTEMPT_BUDGET_EXHAUSTED",))
        return policy.authorize(classification, replans_used=0, max_replans=1)

    def test_propose_without_authorization_raises(self) -> None:
        policy = BoundedReplanPolicy()
        classification = FailureClassificationV1("REPLAN_REQUIRED", ("X",))
        denied = policy.authorize(classification, replans_used=1, max_replans=1)
        with pytest.raises(BoundedReplanError):
            policy.propose(
                cycle_id="cycle-1",
                tenant_id="tenant-1",
                triggering_evidence_ref="evidence://x",
                reason="r",
                retained_step_ids=(),
                scope_comparison="none",
                policy_decision_ref="policy-1",
                decided_by="aix",
                authorization=denied,
            )

    def test_propose_with_authorization_builds_valid_decision(self) -> None:
        policy = BoundedReplanPolicy()
        decision = policy.propose(
            cycle_id="cycle-1",
            tenant_id="tenant-1",
            triggering_evidence_ref="evidence://x",
            reason="step failed repeatedly",
            retained_step_ids=("step-1",),
            added_step_ids=("step-2",),
            scope_comparison="added one recovery step",
            policy_decision_ref="policy-1",
            decided_by="aix-replan-agent",
            authorization=self._authorized(),  # type: ignore[arg-type]
        )
        assert decision.requires_approval is True
        assert decision.objective_unchanged is True
        assert "step-2" in decision.added_step_ids

    def test_propose_always_requires_approval_even_without_scope_widening(self) -> None:
        policy = BoundedReplanPolicy()
        decision = policy.propose(
            cycle_id="cycle-1",
            tenant_id="tenant-1",
            triggering_evidence_ref="evidence://x",
            reason="reorder only",
            retained_step_ids=("step-1", "step-2"),
            scope_comparison="no new steps, same scope",
            policy_decision_ref="policy-1",
            decided_by="aix-replan-agent",
            authorization=self._authorized(),  # type: ignore[arg-type]
        )
        assert decision.requires_approval is True
