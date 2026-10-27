from __future__ import annotations

import pytest

from backend.infrastructure.model_gateway.intelligence_layer.chat_intent import classify_chat_intent
from backend.infrastructure.model_gateway.intelligence_layer.execution_routing import (
    assert_independent_verification,
    map_verification_outcome,
)
from backend.infrastructure.model_gateway.intelligence_layer.routing import route_discuss_turn


def test_greeting_is_conversation() -> None:
    result = classify_chat_intent(latest_user_text="hey")
    assert result.intent == "conversation"
    route = route_discuss_turn(result)
    assert route.invoke_planner is False
    assert route.model_role == "conversation"


def test_objective_keywords_detect_objective() -> None:
    result = classify_chat_intent(latest_user_text="Build a login page with HTML and CSS")
    assert result.intent == "objective"
    assert result.objective_summary


def test_planner_unreachable_until_escalated() -> None:
    intent = classify_chat_intent(latest_user_text="Create a migration plan for auth")
    waiting = route_discuss_turn(intent, escalate_to_planner=False)
    assert waiting.invoke_planner is False
    assert waiting.routing_phase == "awaiting_confirm"
    planning = route_discuss_turn(intent, escalate_to_planner=True)
    assert planning.invoke_planner is True
    assert planning.model_role == "planner"


def test_self_verification_forbidden() -> None:
    with pytest.raises(ValueError, match="SELF_VERIFICATION_FORBIDDEN"):
        assert_independent_verification(
            executor_model_ref="same-model",
            verifier_model_ref="same-model",
        )


def test_verification_outcome_map() -> None:
    assert map_verification_outcome("pass") == "PASS"
    assert map_verification_outcome("replan") == "REPLAN"
    assert map_verification_outcome("nope") == "FAIL"
