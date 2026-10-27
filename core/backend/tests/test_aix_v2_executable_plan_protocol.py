from __future__ import annotations

import pytest

from backend.services.aix_v2_executable_plan_protocol_service import (
    V2ExecutablePlanProtocolError,
    replace_function_example,
    validate_v2_executable_plan_protocol,
)


def _planner_packet() -> dict[str, object]:
    return {
        "schema_name": "planner_slice.current",
        "target_file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
    }


def _codeintel_325_packet() -> dict[str, object]:
    return {
        "schema_name": "planner_slice.current",
        "task_number": 325,
        "target_file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
        "target_symbol": "backend.services.codeintel_query_service.CodeintelQueryService.answer_context_pack",
        "validation_command": "python -m pytest services/hyeve-codeintel-service/tests/test_codeintel_query_service.py -q --tb=short -k skips_duplicate_test_prefix",
    }


def test_protocol_accepts_form_a() -> None:
    protocol = validate_v2_executable_plan_protocol(
        steps=[
            {
                "type": "edit_file",
                "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                "grounding_source": "anchor",
                "search_range": {"start_line": 1, "end_line": 2},
                "replacement_text": "after",
            },
            {"type": "run_validation_command", "command": "pytest -q"},
        ],
        planner_packet=_planner_packet(),
    )

    assert protocol is not None
    assert protocol.form == "A"


def test_protocol_accepts_form_b() -> None:
    protocol = validate_v2_executable_plan_protocol(
        steps=[
            {
                "type": "read_range",
                "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                "symbol": "backend.services.codeintel_query_service.CodeintelQueryService.answer_context_pack",
            },
            {
                "type": "edit_file",
                "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                "grounding_source": "read_range",
                "read_step_idx": 0,
                "edit_range_within_read": {"start_line": 2, "end_line": 3},
                "replacement_text": "after",
            },
            {"type": "run_validation_command", "command": "pytest -q"},
        ],
        planner_packet=_planner_packet(),
    )

    assert protocol is not None
    assert protocol.form == "B"


def test_protocol_rejects_form_b_without_read_step_zero() -> None:
    try:
        validate_v2_executable_plan_protocol(
            steps=[
                {
                    "type": "read_range",
                    "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                },
                {
                    "type": "edit_file",
                    "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                    "grounding_source": "read_range",
                    "edit_range_within_read": {"start_line": 1, "end_line": 1},
                    "replacement_text": "after",
                },
                {"type": "run_validation_command", "command": "pytest -q"},
            ],
            planner_packet=_planner_packet(),
        )
    except V2ExecutablePlanProtocolError as exc:
        assert "read_step_idx=0" in str(exc.detail.get("reason") or "")
    else:
        raise AssertionError("expected V2ExecutablePlanProtocolError")


def test_protocol_rejects_form_b_with_search_text() -> None:
    with_search: list[dict[str, object]] = [
        {
            "type": "read_range",
            "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
            "start_line": 10,
            "end_line": 20,
        },
        {
            "type": "edit_file",
            "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
            "grounding_source": "read_range",
            "read_step_idx": 0,
            "search": "before",
            "edit_range_within_read": {"start_line": 1, "end_line": 1},
            "replacement_text": "after",
        },
        {"type": "run_validation_command", "command": "pytest -q"},
    ]
    try:
        validate_v2_executable_plan_protocol(steps=with_search, planner_packet=_planner_packet())
    except V2ExecutablePlanProtocolError as exc:
        assert "must not emit textual SEARCH/REPLACE fields" in str(exc.detail.get("reason") or "")
    else:
        raise AssertionError("expected V2ExecutablePlanProtocolError")


def test_protocol_rejects_form_a_without_structured_range() -> None:
    with pytest.raises(V2ExecutablePlanProtocolError) as exc_info:
        validate_v2_executable_plan_protocol(
            steps=[
                {
                    "type": "edit_file",
                    "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                    "grounding_source": "anchor",
                    "replacement_text": "after",
                },
                {"type": "run_validation_command", "command": "pytest -q"},
            ],
            planner_packet=_planner_packet(),
        )

    assert "search_range" in str(exc_info.value.detail.get("reason") or "")


def test_protocol_accepts_codeintel_325_replace_function_form() -> None:
    protocol = validate_v2_executable_plan_protocol(
        steps=[
            {
                "type": "read_range",
                "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                "symbol": "backend.services.codeintel_query_service.CodeintelQueryService.answer_context_pack",
            },
            {
                "type": "replace_function",
                "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                "qualified_symbol": "backend.services.codeintel_query_service.CodeintelQueryService.answer_context_pack",
                "edit_intent": "Skip duplicate test_ prefix when synthesizing likely test paths.",
                "preserve_constraints": [
                    "preserve async/sync kind",
                    "preserve exact symbol name",
                    "preserve receiver",
                ],
                "line_targets": ["candidate = f'tests/test_{file_name}'"],
            },
            {"type": "run_validation_command", "command": "pytest -q"},
        ],
        planner_packet=_codeintel_325_packet(),
    )

    assert protocol is not None
    assert protocol.form == "replace_function_read_grounded"


def test_protocol_rejects_codeintel_325_replace_function_short_name_mismatch() -> None:
    with pytest.raises(V2ExecutablePlanProtocolError) as exc_info:
        validate_v2_executable_plan_protocol(
            steps=[
                {
                    "type": "read_range",
                    "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                    "symbol": "CodeintelQueryService.answer_context_pack",
                },
                {
                    "type": "replace_function",
                    "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                    "qualified_symbol": "backend.services.codeintel_query_service.CodeintelQueryService.answer_context_pack",
                    "edit_intent": "Skip duplicate test prefix.",
                },
                {"type": "run_validation_command", "command": "pytest -q"},
            ],
            planner_packet=_codeintel_325_packet(),
        )

    assert exc_info.value.detail["reason"] == (
        "replace_function preread must declare the same fully qualified symbol as replace_function"
    )
    assert exc_info.value.detail["expected_symbol"] == (
        "backend.services.codeintel_query_service.CodeintelQueryService.answer_context_pack"
    )


def test_protocol_rejects_codeintel_325_replace_function_method_qualification_mismatch() -> None:
    with pytest.raises(V2ExecutablePlanProtocolError) as exc_info:
        validate_v2_executable_plan_protocol(
            steps=[
                {
                    "type": "read_range",
                    "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                    "symbol": "backend.services.codeintel_query_service.answer_context_pack",
                },
                {
                    "type": "replace_function",
                    "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                    "qualified_symbol": "backend.services.codeintel_query_service.CodeintelQueryService.answer_context_pack",
                    "edit_intent": "Skip duplicate test prefix.",
                },
                {"type": "run_validation_command", "command": "pytest -q"},
            ],
            planner_packet=_codeintel_325_packet(),
        )

    assert exc_info.value.detail["reason"] == (
        "replace_function preread must declare the same fully qualified symbol as replace_function"
    )


def test_protocol_rejects_codeintel_325_replace_function_preread_without_symbol() -> None:
    with pytest.raises(V2ExecutablePlanProtocolError) as exc_info:
        validate_v2_executable_plan_protocol(
            steps=[
                {
                    "type": "read_range",
                    "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                    "start_line": 1,
                    "end_line": 10,
                },
                {
                    "type": "replace_function",
                    "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                    "qualified_symbol": "backend.services.codeintel_query_service.CodeintelQueryService.answer_context_pack",
                    "edit_intent": "Skip duplicate test prefix.",
                },
                {"type": "run_validation_command", "command": "pytest -q"},
            ],
            planner_packet=_codeintel_325_packet(),
        )

    assert exc_info.value.detail["reason"] == (
        "replace_function preread must declare the same fully qualified symbol as replace_function"
    )
    assert exc_info.value.detail["read_symbol"] == ""


def test_replace_function_example_avoids_placeholder_elision() -> None:
    example = replace_function_example()

    assert '"type":"replace_function"' in example
    assert '"edit_intent"' in example
    assert "# ..." not in example
    assert "omitted unchanged code" not in example
    assert "self, ..." not in example
