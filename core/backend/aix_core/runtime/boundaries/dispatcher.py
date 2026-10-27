"""Dispatcher boundary: the only planner → executor bridge.

Reads an ACCEPTED plan, selects a ready PENDING step, and hands a concrete
StepContract to the executor. Does not plan and does not mutate repositories.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.aix_core.runtime.boundaries.log import dispatch_log
from backend.aix_core.runtime.objective.contracts import PlanRevisionV1, StepContractV1, StepRuntimeStateV1
from backend.aix_core.runtime.objective.step_runtime import init_step_runtime
from backend.aix_core.runtime.objective.step_scheduler import select_next_step


@dataclass(frozen=True)
class DispatchObservation:
    """Result of independently observing an ACCEPTED plan."""

    plan: PlanRevisionV1
    step: StepContractV1
    runtime: dict[str, StepRuntimeStateV1]


def observe_accepted_plan(
    plan: PlanRevisionV1,
    runtime: dict[str, StepRuntimeStateV1] | None = None,
    *,
    preferred_step_id: str | None = None,
) -> DispatchObservation | None:
    """Independently read an ACCEPTED plan and select the next ready step.

    Returns None when the plan is not ACCEPTED or no ready PENDING step exists.

    ``preferred_step_id`` covers the durable claim path: dispatch may already have
    marked the cursor step RUNNING before this observe runs, so ``select_next_step``
    alone would incorrectly skip it.
    """
    if plan is None or str(plan.status) != "ACCEPTED":
        return None
    rt = runtime if runtime is not None else init_step_runtime(plan)
    if not rt and plan.steps:
        rt = init_step_runtime(plan)
    if preferred_step_id:
        for step in plan.steps:
            if step.id != preferred_step_id:
                continue
            st = rt.get(step.id)
            if st is not None and st.status in {"PASS", "SUPERSEDED", "FAILED", "BLOCKED"}:
                break
            return DispatchObservation(plan=plan, step=step, runtime=rt)
    step = select_next_step(plan, rt)
    if step is None:
        return None
    return DispatchObservation(plan=plan, step=step, runtime=rt)


def log_plan_observed(
    *,
    objective_id: str,
    plan_id: str,
    step_id: str,
    attempt: int | str | None = None,
    reason: str = "accepted_plan_ready",
) -> None:
    dispatch_log.emit(
        "plan_observed",
        objective_id=objective_id,
        plan_id=plan_id,
        step_id=step_id,
        attempt=attempt,
        status="OBSERVED",
        reason=reason,
    )


def log_step_ready(
    *,
    objective_id: str,
    plan_id: str | None,
    step_id: str,
    attempt: int | str | None = None,
    reason: str = "scheduler_selected",
) -> None:
    dispatch_log.emit(
        "step_ready",
        objective_id=objective_id,
        plan_id=plan_id,
        step_id=step_id,
        attempt=attempt,
        status="READY",
        reason=reason,
    )


def log_step_claimed(
    *,
    objective_id: str,
    plan_id: str | None,
    step_id: str,
    attempt: int | str | None = None,
    reason: str = "ready_step_claimed",
) -> None:
    dispatch_log.emit(
        "step_claimed",
        objective_id=objective_id,
        plan_id=plan_id,
        step_id=step_id,
        attempt=attempt,
        status="CLAIMED",
        reason=reason,
    )
