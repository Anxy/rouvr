"""Verifies:
1. executor_coder's Layer A is now switched to the persona_runtime governance-derived
   format BY DEFAULT (no flag) -- confirmed safe because
   `grep -rn "role=code mutation planner" backend/ --include="*.py"` found matches only
   inside aix_prompt_builder_service.py itself, nowhere else in production or tests.
2. Layer B / Task Brief / Structured Envelope remain byte-identical to the old
   PlannerPromptBuilder output regardless of the flag -- only Layer A changed.
3. The AIX_PERSONA_RUNTIME_DARK_LAUNCH flag still gates only the full-prompt
   comparison LOG, defaults off, and never affects the return value either way.
"""
from __future__ import annotations

import logging

import pytest

from backend.services.aix_prompt_builder_service import PlannerPromptBuilder
from backend.services.devexecutor_tool_contract_registry_service import (
    _PERSONA_RUNTIME_DARK_LAUNCH_ENV,
    _persona_runtime_dark_launch_enabled,
    build_planner_contract_text,
)

_PACKET = {
    "objective": "Add a null check before dereferencing user.profile in the settings handler.",
    "target_file": "backend/services/settings_handler_service.py",
    "target_symbol": "settings_handler_service.render_settings",
    "mutation_instructions": "Guard user.profile access with an explicit None check before attribute access.",
    "validation_commands": ["python -m pytest backend/tests/services/test_settings_handler_service.py"],
}


def _non_layer_a_tail(text: str) -> str:
    """Everything from 'Layer B: Lane Instructions' onward -- the part that must stay
    byte-identical to the old builder's output."""
    marker = "Layer B: Lane Instructions"
    return text[text.index(marker) :]


def test_flag_defaults_off_when_env_var_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_PERSONA_RUNTIME_DARK_LAUNCH_ENV, raising=False)
    assert _persona_runtime_dark_launch_enabled() is False


@pytest.mark.parametrize("falsy_value", ["", "0", "false", "off", "no"])
def test_flag_off_for_falsy_env_values(monkeypatch: pytest.MonkeyPatch, falsy_value: str) -> None:
    monkeypatch.setenv(_PERSONA_RUNTIME_DARK_LAUNCH_ENV, falsy_value)
    assert _persona_runtime_dark_launch_enabled() is False


@pytest.mark.parametrize("truthy_value", ["1", "true", "True", "on"])
def test_flag_on_for_truthy_env_values(monkeypatch: pytest.MonkeyPatch, truthy_value: str) -> None:
    monkeypatch.setenv(_PERSONA_RUNTIME_DARK_LAUNCH_ENV, truthy_value)
    assert _persona_runtime_dark_launch_enabled() is True


@pytest.mark.parametrize("dark_launch", ["", "true"])
def test_layer_a_is_switched_by_default_regardless_of_flag(
    monkeypatch: pytest.MonkeyPatch, dark_launch: str
) -> None:
    if dark_launch:
        monkeypatch.setenv(_PERSONA_RUNTIME_DARK_LAUNCH_ENV, dark_launch)
    else:
        monkeypatch.delenv(_PERSONA_RUNTIME_DARK_LAUNCH_ENV, raising=False)
    text = build_planner_contract_text(_PACKET)
    assert "role=code mutation planner" not in text
    assert "persona_id=objective_cycle.executor_coder.v1" in text
    assert "mission=Execute one approved step attempt inside its scoped workspace and Move Token." in text
    assert "autonomy_cap=L2" in text


@pytest.mark.parametrize("dark_launch", ["", "true"])
def test_layer_b_task_brief_and_envelope_are_still_byte_identical_to_old_builder(
    monkeypatch: pytest.MonkeyPatch, dark_launch: str
) -> None:
    """Only Layer A changed. Everything downstream of it must be exactly what
    PlannerPromptBuilder produced before this change -- no silent content drift."""
    if dark_launch:
        monkeypatch.setenv(_PERSONA_RUNTIME_DARK_LAUNCH_ENV, dark_launch)
    else:
        monkeypatch.delenv(_PERSONA_RUNTIME_DARK_LAUNCH_ENV, raising=False)
    expected_tail = _non_layer_a_tail(PlannerPromptBuilder.system_prompt(packet=_PACKET, lane="generic"))
    actual_tail = _non_layer_a_tail(build_planner_contract_text(_PACKET))
    assert actual_tail == expected_tail


def test_strict_replace_function_lane_layer_b_also_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_PERSONA_RUNTIME_DARK_LAUNCH_ENV, raising=False)
    packet = dict(_PACKET, lane="strict_replace_function")
    expected_tail = _non_layer_a_tail(
        PlannerPromptBuilder.system_prompt(packet=packet, lane="strict_replace_function")
    )
    actual_tail = _non_layer_a_tail(build_planner_contract_text(packet))
    assert actual_tail == expected_tail


def test_flag_on_logs_comparison_without_raising(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(_PERSONA_RUNTIME_DARK_LAUNCH_ENV, "true")
    with caplog.at_level(logging.INFO, logger="backend.services.devexecutor_tool_contract_registry_service"):
        result = build_planner_contract_text(_PACKET)
    assert result  # did not raise, produced real output
    assert any("persona_runtime_dark_launch_comparison" in record.message for record in caplog.records)
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


def test_flag_off_produces_no_dark_launch_comparison_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv(_PERSONA_RUNTIME_DARK_LAUNCH_ENV, raising=False)
    with caplog.at_level(logging.DEBUG, logger="backend.services.devexecutor_tool_contract_registry_service"):
        build_planner_contract_text(_PACKET)
    assert not any("persona_runtime_dark_launch_comparison" in record.message for record in caplog.records)
