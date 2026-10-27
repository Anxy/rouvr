"""AIX-MPE-003 Packet MPE-04: StepRuntimeStateV1 helpers.

Never mutates a frozen record in place -- returns a replacement.
"""
from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import (
    PlanRevisionV1,
    StepRuntimeStateV1,
    StepRuntimeStatus,
    VerificationVerdict,
    utc_now,
)


def init_step_runtime(plan: PlanRevisionV1) -> dict[str, StepRuntimeStateV1]:
    return {
        step.id: StepRuntimeStateV1(
            step_id=step.id,
            plan_id=plan.plan_id,
            plan_revision=plan.revision,
            status="PENDING",
        )
        for step in plan.steps
    }


def replace_runtime_status(
    state: StepRuntimeStateV1,
    *,
    status: StepRuntimeStatus,
    attempt_count: int | None = None,
    latest_attempt_id: str | None = None,
    verification_verdict: VerificationVerdict | None = None,
    blocked_reason: str | None = None,
    failure_refs: tuple[str, ...] | None = None,
) -> StepRuntimeStateV1:
    data = state.model_dump()
    data["status"] = status
    if attempt_count is not None:
        data["attempt_count"] = attempt_count
    if latest_attempt_id is not None:
        data["latest_attempt_id"] = latest_attempt_id
    if verification_verdict is not None:
        data["verification_verdict"] = verification_verdict
    if blocked_reason is not None:
        data["blocked_reason"] = blocked_reason
    if failure_refs is not None:
        data["failure_refs"] = failure_refs
    if status == "RUNNING" and not data.get("started_at"):
        data["started_at"] = utc_now()
    if status in ("PASS", "FAILED", "BLOCKED", "SUPERSEDED"):
        data["completed_at"] = utc_now()
    return StepRuntimeStateV1.model_validate(data)


__all__ = ["init_step_runtime", "replace_runtime_status"]
