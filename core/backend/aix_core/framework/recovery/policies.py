"""AIX Framework Recovery Policy Adapters.

Sprint: F6 Recovery Framework (F6-P1)
Adapters wrapping verified runtime policies: retry_repair, repair_directive, replan, loop_budget.
"""

from __future__ import annotations

from typing import Any

from backend.aix_core.framework.recovery.taxonomy import (
    RecoveryMechanism,
    Repair,
    Replan,
    Retry,
)
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    RepairDirectiveV1,
    ReplanDecisionV1,
    StepContractV1,
)
from backend.aix_core.runtime.recovery.repair_directive import (
    _failure_fingerprint,
    build_repair_directive,
)
from backend.aix_core.runtime.recovery.replan import (
    BoundedReplanPolicy,
    ReplanAuthorizationV1,
)
from backend.aix_core.runtime.recovery.retry_repair import (
    RetryDecisionV1,
    decide_retry,
)
from backend.aix_core.runtime.verification.failure_taxonomy import FailureClassificationV1


class RetryPolicyAdapter:
    """Canonical adapter over decide_retry."""

    @staticmethod
    def authorize_retry(
        classification: FailureClassificationV1,
        budget: BudgetV1,
        *,
        current_attempt_number: int = 1,
        step_id: str = "",
        failure_ref: str = "",
    ) -> tuple[RetryDecisionV1, Retry | None]:
        decision = decide_retry(
            classification,
            current_attempt_number=current_attempt_number,
            remaining_budget=budget,
        )
        action: Retry | None = None
        if decision.disposition == "AUTHORIZED" and decision.next_attempt_number is not None:
            retries_remaining = decision.remaining_budget.retries if decision.remaining_budget else 0
            action = Retry(
                failure_ref=failure_ref or (classification.reason_codes[0] if classification.reason_codes else "failure"),
                step_id=step_id,
                next_attempt=decision.next_attempt_number,
                budget_after=retries_remaining,
            )
        return decision, action


class RepairPolicyAdapter:
    """Canonical adapter over build_repair_directive + Retry."""

    @staticmethod
    def build_repair(
        *,
        step: StepContractV1,
        reasons: tuple[str, ...],
        retry_decision: RetryDecisionV1,
        last_repair_diff: str = "",
        failure_ref: str = "",
        last_strategy_id: str | None = None,
        prior_failure_fingerprints: tuple[str, ...] = (),
    ) -> tuple[RepairDirectiveV1, Repair | None]:
        directive = build_repair_directive(
            step=step,
            reasons=reasons,
            last_strategy_id=last_strategy_id,
            prior_failure_fingerprints=prior_failure_fingerprints,
            last_repair_diff=last_repair_diff,
        )
        action: Repair | None = None
        if retry_decision.disposition == "AUTHORIZED" and retry_decision.next_attempt_number is not None:
            retries_remaining = retry_decision.remaining_budget.retries if retry_decision.remaining_budget else 0
            fp = _failure_fingerprint(reasons, last_repair_diff)
            retry_action = Retry(
                failure_ref=failure_ref or fp,
                step_id=step.id,
                next_attempt=retry_decision.next_attempt_number,
                budget_after=retries_remaining,
            )
            action = Repair(
                failure_ref=failure_ref or fp,
                directive=directive,
                retry=retry_action,
            )
        return directive, action


class ReplanPolicyAdapter:
    """Canonical adapter over BoundedReplanPolicy."""

    @staticmethod
    def authorize_replan(
        classification: FailureClassificationV1,
        replans_used: int,
        max_replans: int = 3,
    ) -> tuple[ReplanAuthorizationV1, Replan | None]:
        policy = BoundedReplanPolicy()
        auth = policy.authorize(
            classification,
            replans_used=replans_used,
            max_replans=max_replans,
        )
        action: Replan | None = None
        if auth.disposition == "AUTHORIZED":
            remaining = max(0, max_replans - replans_used - 1)
            action = Replan(
                decision=auth.reason_codes[0] if auth.reason_codes else "REPLAN_AUTHORIZED",
                budget_after=remaining,
            )
        return auth, action
