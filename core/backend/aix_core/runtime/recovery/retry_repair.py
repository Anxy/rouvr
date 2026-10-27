"""AIX-E2E-CYCLE-001H-W05: bounded retry/repair.

Consumes a `RETRYABLE`/`REPAIRABLE` classification from `failure_taxonomy.py` and decides
whether a new attempt is authorized. Never mutates a prior `StepAttemptV1`/evidence
bundle — this module only ever returns instructions for a *new* attempt; nothing here
holds a reference to, or writes to, any previous attempt's record.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.aix_core.runtime.objective.contracts import BudgetV1
from backend.aix_core.runtime.verification.failure_taxonomy import FailureClassificationV1

RetryDisposition = Literal["AUTHORIZED", "DENIED"]


class RetryRepairError(ValueError):
    pass


@dataclass(frozen=True)
class RetryDecisionV1:
    disposition: RetryDisposition
    next_attempt_number: int | None
    remaining_budget: BudgetV1 | None
    reason_codes: tuple[str, ...]


def _decrement_retry_budget(budget: BudgetV1) -> BudgetV1:
    if budget.retries <= 0:
        raise RetryRepairError("cannot decrement a retry budget that is already at 0")
    return BudgetV1(
        time_seconds=budget.time_seconds,
        tokens=budget.tokens,
        cost_usd=budget.cost_usd,
        retries=budget.retries - 1,
        steps=budget.steps,
        concurrency=budget.concurrency,
    )


def decide_retry(
    classification: FailureClassificationV1,
    *,
    current_attempt_number: int,
    remaining_budget: BudgetV1,
) -> RetryDecisionV1:
    if classification.failure_class not in ("RETRYABLE", "REPAIRABLE"):
        return RetryDecisionV1(
            disposition="DENIED",
            next_attempt_number=None,
            remaining_budget=remaining_budget,
            reason_codes=(f"NOT_A_RETRY_ELIGIBLE_CLASS:{classification.failure_class}",),
        )
    if remaining_budget.retries <= 0:
        return RetryDecisionV1(
            disposition="DENIED",
            next_attempt_number=None,
            remaining_budget=remaining_budget,
            reason_codes=("RETRY_BUDGET_EXHAUSTED",),
        )

    new_budget = _decrement_retry_budget(remaining_budget)
    return RetryDecisionV1(
        disposition="AUTHORIZED",
        next_attempt_number=current_attempt_number + 1,
        remaining_budget=new_budget,
        reason_codes=(f"RETRY_AUTHORIZED:{classification.failure_class}",),
    )


__all__ = [
    "RetryDisposition",
    "RetryRepairError",
    "RetryDecisionV1",
    "decide_retry",
]
