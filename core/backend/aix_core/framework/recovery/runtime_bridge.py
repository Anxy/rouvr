"""F6 recovery bridge — adapters over existing OC recovery authorities."""

from __future__ import annotations

from typing import Any

from backend.aix_core.framework.recovery.policies import (
    RepairPolicyAdapter,
    ReplanPolicyAdapter,
    RetryPolicyAdapter,
)
from backend.aix_core.runtime.objective.contracts import BudgetV1, RepairDirectiveV1, StepContractV1
from backend.aix_core.runtime.recovery.replan import BoundedReplanPolicy, ReplanAuthorizationV1
from backend.aix_core.runtime.recovery.retry_repair import RetryDecisionV1
from backend.aix_core.runtime.verification.failure_taxonomy import FailureClassificationV1


def decide_retry_routed(
    classification: FailureClassificationV1,
    *,
    current_attempt_number: int = 1,
    remaining_budget: BudgetV1,
    step_id: str = "",
    **_kwargs: Any,
) -> RetryDecisionV1:
    """Always via F6 RetryPolicyAdapter (pure delegate to decide_retry)."""
    decision, _ = RetryPolicyAdapter.authorize_retry(
        classification,
        remaining_budget,
        current_attempt_number=current_attempt_number,
        step_id=step_id,
    )
    return decision


def build_repair_directive_routed(
    *,
    step: StepContractV1,
    reasons: tuple[str, ...],
    retry_decision: RetryDecisionV1,
    last_repair_diff: str = "",
    last_strategy_id: str | None = None,
    prior_failure_fingerprints: tuple[str, ...] = (),
    **_kwargs: Any,
) -> RepairDirectiveV1:
    """Always via F6 RepairPolicyAdapter (pure delegate)."""
    directive, _ = RepairPolicyAdapter.build_repair(
        step=step,
        reasons=reasons,
        retry_decision=retry_decision,
        last_repair_diff=last_repair_diff,
        last_strategy_id=last_strategy_id,
        prior_failure_fingerprints=prior_failure_fingerprints,
    )
    return directive


def authorize_replan_routed(**kwargs: Any) -> ReplanAuthorizationV1:
    """Always via F6 ReplanPolicyAdapter (pure delegate to BoundedReplanPolicy)."""
    auth, _ = ReplanPolicyAdapter.authorize_replan(**kwargs)
    return auth


__all__ = [
    "authorize_replan_routed",
    "build_repair_directive_routed",
    "decide_retry_routed",
]
