"""W05: Discuss routing — planner unreachable from normal chat."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.infrastructure.model_gateway.intelligence_layer.chat_intent import (
    ChatIntent,
    ChatIntentResultV1,
)
from backend.infrastructure.model_gateway.intelligence_layer.model_registry import IntelligenceRole

RoutingPhase = Literal[
    "conversation",
    "clarification",
    "planning",
    "awaiting_confirm",
    "continue_objective",
    "executing",
    "reviewing",
]


@dataclass(frozen=True)
class DiscussRouteDecisionV1:
    intent: ChatIntent
    confidence: float
    objective_summary: str | None
    model_role: IntelligenceRole
    invoke_planner: bool
    routing_phase: RoutingPhase
    reason: str


def route_discuss_turn(
    intent: ChatIntentResultV1,
    *,
    escalate_to_planner: bool = False,
) -> DiscussRouteDecisionV1:
    """Map chat intent → model role. Planner only for objective (+ optional escalate)."""

    if intent.intent == "continue_objective":
        return DiscussRouteDecisionV1(
            intent=intent.intent,
            confidence=intent.confidence,
            objective_summary=intent.objective_summary,
            model_role="conversation",
            invoke_planner=False,
            routing_phase="continue_objective",
            reason=intent.reason,
        )

    if intent.intent in {"conversation", "clarification"}:
        return DiscussRouteDecisionV1(
            intent=intent.intent,
            confidence=intent.confidence,
            objective_summary=intent.objective_summary,
            model_role="conversation",
            invoke_planner=False,
            routing_phase="conversation" if intent.intent == "conversation" else "clarification",
            reason=intent.reason,
        )

    # objective
    if escalate_to_planner:
        return DiscussRouteDecisionV1(
            intent=intent.intent,
            confidence=intent.confidence,
            objective_summary=intent.objective_summary,
            model_role="planner",
            invoke_planner=True,
            routing_phase="planning",
            reason="objective_escalated_to_planner",
        )

    # Detected objective: still conversation model first; FE confirms then escalate.
    # Auto-escalate when client sets escalate_to_planner, OR when confidence high and
    # caller requests planner_on_objective (handled in service).
    return DiscussRouteDecisionV1(
        intent=intent.intent,
        confidence=intent.confidence,
        objective_summary=intent.objective_summary,
        model_role="conversation",
        invoke_planner=False,
        routing_phase="awaiting_confirm",
        reason="objective_detected_awaiting_confirm",
    )
