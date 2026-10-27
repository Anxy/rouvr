"""Deterministic fallback policy for the live step-contract planner.

Policy: never synthesize a multi-step plan when the live model fails.
Fail closed, allow bounded retries already owned by durable_api_backend,
and require an explicit human/replan decision after exhaustion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FALLBACK_POLICY_ID = "aix.planner_fallback.v1"

Disposition = Literal["ACCEPT_PLAN", "RETRY_BOUNDED", "FAIL_CLOSED", "REQUIRE_REPLAN"]


@dataclass(frozen=True)
class FallbackDecision:
    policy_id: str
    disposition: Disposition
    reason_code: str
    allow_synthetic_plan: bool = False


# Refusal / error prefixes returned by propose_step_contract
_RETRYABLE = (
    "NEEDS_MORE_EVIDENCE",
    "TARGET_SCOPE_NOT_GROUNDED_IN_EVIDENCE",
    "PLAN_QUALITY_REJECTED",
)
_HARD_FAIL = (
    "PLANNER_MODEL_ERROR",
    "GOVERNED_ROUTE_DENIED",
    "INVALID_STEP_CONTRACT",
)


def apply_fallback_policy(
    *,
    reason: str | None,
    plan_ok: bool,
    retry_count: int,
    max_retries: int = 1,
) -> FallbackDecision:
    if plan_ok and not reason:
        return FallbackDecision(
            policy_id=FALLBACK_POLICY_ID,
            disposition="ACCEPT_PLAN",
            reason_code="PLAN_VALID",
            allow_synthetic_plan=False,
        )
    code = (reason or "UNKNOWN").split(":")[0]
    if code in _RETRYABLE and retry_count < max_retries:
        return FallbackDecision(
            policy_id=FALLBACK_POLICY_ID,
            disposition="RETRY_BOUNDED",
            reason_code=code,
            allow_synthetic_plan=False,
        )
    if code in _HARD_FAIL or retry_count >= max_retries:
        return FallbackDecision(
            policy_id=FALLBACK_POLICY_ID,
            disposition="FAIL_CLOSED" if code in _HARD_FAIL else "REQUIRE_REPLAN",
            reason_code=code,
            allow_synthetic_plan=False,
        )
    return FallbackDecision(
        policy_id=FALLBACK_POLICY_ID,
        disposition="FAIL_CLOSED",
        reason_code=code,
        allow_synthetic_plan=False,
    )
