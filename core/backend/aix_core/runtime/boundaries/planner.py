"""Planner boundary: plan generation/acceptance only. Never executes steps."""

from __future__ import annotations

from backend.aix_core.runtime.boundaries.log import planner_log
from backend.aix_core.runtime.objective.contracts import PlanRevisionV1


def log_planning_started(
    *,
    objective_id: str,
    cycle_id: str | None = None,
    reason: str = "propose_step_contract",
) -> None:
    planner_log.emit(
        "planning_started",
        objective_id=objective_id,
        status="STARTED",
        reason=reason,
        cycle_id=cycle_id,
    )


def log_plan_accepted(plan: PlanRevisionV1, *, reason: str = "plan_quality_ok") -> None:
    planner_log.emit(
        "plan_accepted",
        objective_id=plan.objective_id,
        plan_id=plan.plan_id,
        status="ACCEPTED",
        reason=reason,
        step_count=len(plan.steps),
        revision=plan.revision,
    )
