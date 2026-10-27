"""AIX-MPE-003 Packets MPE-19 / MPE-23: deterministic cycle controller.

Does not reason about code. Maps persisted state + last verdict onto the next action.
Planner / executor / verifier return here, not to the user.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.aix_core.runtime.objective.contracts import (
    ControllerAction,
    PlanRevisionV1,
    PlannerIterationDecision,
    StepRuntimeStateV1,
    VerificationVerdict,
)
from backend.aix_core.runtime.objective.step_scheduler import all_required_steps_passed, select_next_step


@dataclass(frozen=True)
class ControllerDecision:
    action: ControllerAction
    planner_decision: PlannerIterationDecision | None = None
    detail: str = ""


def route_verifier_feedback(verdict: VerificationVerdict) -> ControllerDecision:
    mapping: dict[VerificationVerdict, ControllerDecision] = {
        "PASS": ControllerDecision(action="CONTINUE", planner_decision="CONTINUE_PLAN"),
        "REPAIR": ControllerDecision(action="REPAIR", planner_decision="REPAIR_STEP"),
        "REUNDERSTAND": ControllerDecision(action="REUNDERSTAND", planner_decision="REQUEST_UNDERSTANDING"),
        "REPLAN": ControllerDecision(action="REPLAN", planner_decision="REVISE_PLAN"),
        "HALT": ControllerDecision(action="HALT", detail="verifier halt"),
    }
    return mapping[verdict]


def next_controller_action(
    *,
    plan: PlanRevisionV1 | None,
    runtime: dict[str, StepRuntimeStateV1],
    last_verdict: VerificationVerdict | None,
    remaining_required_obligation_ids: tuple[str, ...],
    budget_exhausted: str | None = None,
    understanding_complete: bool = True,
) -> ControllerDecision:
    if budget_exhausted:
        return ControllerDecision(action="HALT", detail=budget_exhausted)
    if not understanding_complete:
        return ControllerDecision(action="UNDERSTAND")
    if plan is None:
        return ControllerDecision(action="PLAN")
    if last_verdict is not None and last_verdict != "PASS":
        return route_verifier_feedback(last_verdict)
    if all_required_steps_passed(plan, runtime):
        if remaining_required_obligation_ids:
            return ControllerDecision(
                action="REPLAN",
                planner_decision="REVISE_PLAN",
                detail="steps passed but required obligations remain",
            )
        return ControllerDecision(action="COMPLETE")
    nxt = select_next_step(plan, runtime)
    if nxt is None:
        return ControllerDecision(action="HALT", detail="no READY step and work remains")
    return ControllerDecision(action="EXECUTE", detail=nxt.id)


__all__ = ["ControllerDecision", "next_controller_action", "route_verifier_feedback"]
