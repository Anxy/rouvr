from __future__ import annotations

import pytest

from backend.aix_core.persona_runtime.intent_engine import LANE_TO_PERSONA_ROLE, classify_intent


@pytest.mark.parametrize(
    "packet,expected_trigger",
    [
        ({"planner_mode": "packet_bound_replace_function"}, "planner_mode==packet_bound_replace_function"),
        ({"mode": "packet_bound_replace_function"}, "mode==packet_bound_replace_function"),
        ({"lane": "packet_bound_replace_function"}, "lane==packet_bound_replace_function"),
        ({"lane": "strict_replace_function"}, "lane==strict_replace_function"),
        ({"planner_lane": "strict_replace_function"}, "planner_lane==strict_replace_function"),
        (
            {
                "schema_name": "planner_slice.current",
                "target_file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                "task_number": "325",
                "validation_command": "pytest -k skips_duplicate_test_prefix",
            },
            "is_codeintel_325_replace_function_lane",
        ),
    ],
)
def test_classify_intent_strict_replace_function_triggers(packet: dict[str, object], expected_trigger: str) -> None:
    intent = classify_intent(packet)
    assert intent.lane == "strict_replace_function"
    assert intent.matched_trigger == expected_trigger
    assert intent.persona_role == "executor_coder"


def test_classify_intent_defaults_to_generic_when_no_trigger_matches() -> None:
    intent = classify_intent({"objective": "Fix route behavior", "target_file": "backend/routers/x.py"})
    assert intent.lane == "generic"
    assert intent.matched_trigger == "no_strict_trigger_matched"
    assert intent.persona_role == "executor_coder"


def test_classify_intent_handles_none_and_non_dict_packet() -> None:
    assert classify_intent(None).lane == "generic"
    assert classify_intent("not a dict").lane == "generic"  # type: ignore[arg-type]


def test_lane_to_persona_role_map_is_complete_and_honest() -> None:
    """Both lanes route to executor_coder today -- there is no real serving path for
    any other role, so the map must not claim otherwise."""
    assert set(LANE_TO_PERSONA_ROLE.values()) == {"executor_coder"}
