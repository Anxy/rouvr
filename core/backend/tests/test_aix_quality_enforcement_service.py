from __future__ import annotations

from typing import Any

from backend.services.aix_quality_enforcement_service import validate_executor_output


def test_validate_executor_output_preserves_specific_replace_function_failure() -> None:
    payload: dict[str, Any] = {
        "quality_gate": {
            "passed": False,
            "code": "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE",
            "message": "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE",
        },
        "control_plane_outcome": {
            "reason_code": "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE",
            "reason_summary": "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE",
        },
        "result": {
            "evidence": {
                "control_plane_outcome": {
                    "reason_code": "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE",
                    "reason_summary": "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE",
                }
            }
        },
    }

    validate_executor_output(
        payload,
        {
            "first_blocker_reason": "REPLACE_FUNCTION_OBSERVED_SOURCE_INVALID",
            "first_blocker_summary": (
                "REPLACE_FUNCTION_OBSERVED_SOURCE_INVALID detail="
                '{"reason":"invalid_observed_symbol_source exactly_one_function_required"}'
            ),
            "first_blocker_blocked_reason": "replace_function_observed_source_invalid",
        },
    )

    assert payload["quality_gate"]["code"] == "REPLACE_FUNCTION_OBSERVED_SOURCE_INVALID"
    assert payload["quality_gate"]["secondary_code"] == "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE"
    assert payload["quality_gate"]["blocked_reason"] == "replace_function_observed_source_invalid"
    assert payload["quality_gate"]["message"].startswith("REPLACE_FUNCTION_OBSERVED_SOURCE_INVALID detail=")
    assert payload["control_plane_outcome"]["reason_code"] == "REPLACE_FUNCTION_OBSERVED_SOURCE_INVALID"
    assert payload["control_plane_outcome"]["secondary_reason_code"] == "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE"
    assert payload["control_plane_outcome"]["blocked_reason"] == "replace_function_observed_source_invalid"
    assert payload["result"]["evidence"]["control_plane_outcome"]["reason_code"] == (
        "REPLACE_FUNCTION_OBSERVED_SOURCE_INVALID"
    )
