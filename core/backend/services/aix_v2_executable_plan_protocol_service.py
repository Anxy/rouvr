from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class V2ExecutablePlanProtocolError(RuntimeError):
    def __init__(self, code: str, detail: dict[str, Any] | None = None) -> None:
        self.code = str(code or "").strip() or "PLANNER_V2_PROTOCOL_INVALID"
        self.detail = dict(detail or {})
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class V2ExecutablePlanProtocol:
    form: str
    read_step: dict[str, Any] | None
    edit_step: dict[str, Any]
    validation_step: dict[str, Any]


def is_v2_codeintel_mutation_lane(planner_packet: dict[str, Any] | None) -> bool:
    packet = dict(planner_packet or {})
    if str(packet.get("schema_name") or "").strip() != "planner_slice.current":
        return False
    target_file = str(packet.get("target_file") or "").strip().replace("\\", "/")
    return target_file.startswith("services/hyeve-codeintel-service/")


def is_codeintel_325_replace_function_lane(planner_packet: dict[str, Any] | None) -> bool:
    packet = dict(planner_packet or {})
    target_file = str(packet.get("target_file") or "").strip().replace("\\", "/")
    validation_command = str(packet.get("validation_command") or "").strip()
    try:
        task_number = int(str(packet.get("task_number")))
    except Exception:
        task_number = 0
    return (
        str(packet.get("schema_name") or "").strip() == "planner_slice.current"
        and task_number == 325
        and target_file == "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py"
        and "skips_duplicate_test_prefix" in validation_command
    )


def is_packet_bound_single_symbol_replace_function_lane(planner_packet: dict[str, Any] | None) -> bool:
    packet = dict(planner_packet or {})
    if str(packet.get("schema_name") or "").strip() != "planner_slice.current":
        return False
    if str(packet.get("planner_mode") or "").strip() != "packet_bound_replace_function":
        return False
    target_file = str(packet.get("target_file") or "").strip().replace("\\", "/")
    target_symbol = str(packet.get("target_symbol") or "").strip()
    anchor = dict((packet.get("allowed_surface") or {}).get("anchor") or {})
    compact_snippet = str(anchor.get("compact_snippet") or "").strip()
    output_schema = dict(packet.get("output_schema") or {})
    output_type = str(output_schema.get("type") or "").strip()
    return bool(target_file and target_symbol and compact_snippet and output_type == "replace_function")


def form_a_example() -> str:
    return (
        '{"steps":[{"type":"edit_file","file":"<target file>","grounding_source":"anchor",'
        '"search_range":{"start_line":<ANCHOR_START>,"end_line":<ANCHOR_END>},"replacement_text":"<UPDATED TEXT>"},'
        '{"type":"run_validation_command","command":"<DECLARED VALIDATION COMMAND>"}]}'
    )


def form_b_example() -> str:
    return (
        '{"steps":[{"type":"read_range","file":"<target file>","start_line":<START>,"end_line":<END>},'
        '{"type":"edit_file","file":"<target file>","grounding_source":"read_range","read_step_idx":0,'
        '"edit_range_within_read":{"start_line":<START_IN_READ>,"end_line":<END_IN_READ>},"replacement_text":"<UPDATED TEXT>"},'
        '{"type":"run_validation_command","command":"<DECLARED VALIDATION COMMAND>"}]}'
    )


def replace_function_example() -> str:
    return (
        '{"steps":[{"type":"read_range","file":"<target file>","symbol":"<qualified symbol>"},'
        '{"type":"replace_function","file":"<target file>","qualified_symbol":"<qualified symbol>",'
        '"edit_intent":"<single bounded behavior change>","preserve_constraints":["preserve async/sync kind","preserve exact symbol name","preserve receiver","preserve unchanged lines unless intentionally edited"],'
        '"line_targets":["<optional exact observed line or behavior hotspot>"]}'
        ',{"type":"run_validation_command","command":"<DECLARED VALIDATION COMMAND>"}]}'
    )


def validate_v2_executable_plan_protocol(
    *,
    steps: list[dict[str, Any]],
    planner_packet: dict[str, Any] | None,
) -> V2ExecutablePlanProtocol | None:
    replace_function_lane = is_codeintel_325_replace_function_lane(
        planner_packet
    ) or is_packet_bound_single_symbol_replace_function_lane(planner_packet)
    if not (is_v2_codeintel_mutation_lane(planner_packet) or replace_function_lane):
        return None
    if replace_function_lane:
        executable_steps = [dict(step) for step in steps if isinstance(step, dict) and not bool(step.get("synthetic"))]
        if len(executable_steps) != 3:
            raise V2ExecutablePlanProtocolError(
                "PLANNER_V2_PROTOCOL_INVALID",
                {
                    "reason": "replace_function lane requires read_range -> replace_function -> run_validation_command only",
                    "step_count": len(executable_steps),
                    "step_kinds": [_step_kind(step) for step in executable_steps],
                },
            )
        first, second, third = executable_steps
        if (
            _step_kind(first) != "read_range"
            or _step_kind(second) != "replace_function"
            or _step_kind(third) != "cmd.run"
        ):
            raise V2ExecutablePlanProtocolError(
                "PLANNER_V2_PROTOCOL_INVALID",
                {
                    "reason": "replace_function lane requires read_range -> replace_function -> run_validation_command only",
                    "step_kinds": [_step_kind(step) for step in executable_steps],
                },
            )
        _validate_replace_function_read_grounded_form(first, second, planner_packet=planner_packet)
        return V2ExecutablePlanProtocol(
            form="replace_function_read_grounded",
            read_step=first,
            edit_step=second,
            validation_step=third,
        )
    executable_steps = [dict(step) for step in steps if isinstance(step, dict) and not bool(step.get("synthetic"))]
    if len(executable_steps) == 2:
        first, second = executable_steps
        if _step_kind(first) != "edit_file" or _step_kind(second) != "cmd.run":
            raise V2ExecutablePlanProtocolError(
                "PLANNER_V2_PROTOCOL_INVALID",
                {
                    "reason": "legal Form A is edit_file -> run_validation_command only",
                    "step_kinds": [_step_kind(step) for step in executable_steps],
                },
            )
        _validate_form_a(first)
        return V2ExecutablePlanProtocol(form="A", read_step=None, edit_step=first, validation_step=second)
    if len(executable_steps) == 3:
        first, second, third = executable_steps
        if _step_kind(first) != "read_range" or _step_kind(second) != "edit_file" or _step_kind(third) != "cmd.run":
            raise V2ExecutablePlanProtocolError(
                "PLANNER_V2_PROTOCOL_INVALID",
                {
                    "reason": "legal Form B is read_range -> edit_file -> run_validation_command only",
                    "step_kinds": [_step_kind(step) for step in executable_steps],
                },
            )
        _validate_form_b(first, second)
        return V2ExecutablePlanProtocol(form="B", read_step=first, edit_step=second, validation_step=third)
    raise V2ExecutablePlanProtocolError(
        "PLANNER_V2_PROTOCOL_INVALID",
        {
            "reason": "only Form A (2 steps) or Form B (3 steps) are legal for this lane",
            "step_count": len(executable_steps),
            "step_kinds": [_step_kind(step) for step in executable_steps],
        },
    )


def _step_kind(step: dict[str, Any]) -> str:
    value = str(step.get("tool") or step.get("type") or "").strip()
    if value == "run_validation_command":
        return "cmd.run"
    return value


def _step_args(step: dict[str, Any]) -> dict[str, Any]:
    return dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}


def _step_file(step: dict[str, Any]) -> str:
    args = _step_args(step)
    return str(args.get("relative_path") or step.get("file") or args.get("file") or "").strip().replace("\\", "/")


def _edit_grounding_source(step: dict[str, Any]) -> str:
    args = _step_args(step)
    return str(args.get("grounding_source") or step.get("grounding_source") or "").strip().lower()


def _edit_read_step_idx(step: dict[str, Any]) -> int | None:
    args = _step_args(step)
    value: object = args.get("read_step_idx")
    if value in (None, ""):
        value = step.get("read_step_idx")
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, str)):
            return int(value)
        raise TypeError("read_step_idx must be int or str")
    except Exception as exc:
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "read_step_idx must be an integer", "read_step_idx": value},
        ) from exc


def _step_has_search_range(step: dict[str, Any]) -> bool:
    args = _step_args(step)
    value = args.get("search_range")
    if not isinstance(value, dict):
        value = step.get("search_range")
    return isinstance(value, dict) and "start_line" in value and "end_line" in value


def _step_edit_range_within_read(step: dict[str, Any]) -> dict[str, int] | None:
    args = _step_args(step)
    value = args.get("edit_range_within_read")
    if not isinstance(value, dict):
        value = step.get("edit_range_within_read")
    if not isinstance(value, dict):
        return None
    try:
        start_line = int(str(value.get("start_line")))
        end_line = int(str(value.get("end_line")))
    except Exception as exc:
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "edit_range_within_read must declare integer start_line/end_line"},
        ) from exc
    if start_line < 1 or end_line < start_line:
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "edit_range_within_read must be 1-based and end_line >= start_line"},
        )
    return {"start_line": start_line, "end_line": end_line}


def _step_replacement_text(step: dict[str, Any]) -> str:
    args = _step_args(step)
    return str(
        args.get("replacement")
        or step.get("replacement")
        or args.get("replace")
        or step.get("replace")
        or args.get("replacement_text")
        or step.get("replacement_text")
        or args.get("replace_text")
        or step.get("replace_text")
        or ""
    )


def _step_qualified_symbol(step: dict[str, Any]) -> str:
    args = _step_args(step)
    return str(
        args.get("qualified_symbol")
        or args.get("function_name")
        or step.get("qualified_symbol")
        or step.get("target_symbol")
        or args.get("target_symbol")
        or ""
    ).strip()


def _packet_target_symbol(planner_packet: dict[str, Any] | None) -> str:
    return str(dict(planner_packet or {}).get("target_symbol") or "").strip()


def _validate_replace_function_form(step: dict[str, Any]) -> None:
    if not _step_file(step):
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "replace_function must declare file"},
        )
    if not _step_qualified_symbol(step):
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "replace_function must declare qualified_symbol"},
        )
    args = _step_args(step)
    if str(args.get("edit_intent") or step.get("edit_intent") or "").strip() == "":
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "replace_function must declare edit_intent"},
        )
    if str(args.get("replacement") or step.get("replacement") or "").strip():
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "replace_function must not declare replacement in planner output for this lane"},
        )


def _validate_replace_function_read_grounded_form(
    read_step: dict[str, Any],
    edit_step: dict[str, Any],
    *,
    planner_packet: dict[str, Any] | None,
) -> None:
    read_file = _step_file(read_step)
    edit_file = _step_file(edit_step)
    target_file = str(dict(planner_packet or {}).get("target_file") or "").strip().replace("\\", "/")
    if not read_file or read_file != edit_file or (target_file and read_file != target_file):
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {
                "reason": "replace_function preread must be a same-file read_range on the target file",
                "read_file": read_file,
                "edit_file": edit_file,
                "target_file": target_file,
            },
        )
    read_args = _step_args(read_step)
    read_symbol = str(read_args.get("symbol") or read_step.get("symbol") or "").strip()
    qualified_symbol = _step_qualified_symbol(edit_step)
    expected_symbol = _packet_target_symbol(planner_packet)
    expected_mismatch = bool(expected_symbol) and (
        not read_symbol or not qualified_symbol or read_symbol != expected_symbol or qualified_symbol != expected_symbol
    )
    if expected_mismatch or not read_symbol or not qualified_symbol or read_symbol != qualified_symbol:
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {
                "reason": "replace_function preread must declare the same fully qualified symbol as replace_function",
                "read_symbol": read_symbol,
                "qualified_symbol": qualified_symbol,
                "expected_symbol": expected_symbol or None,
            },
        )
    _validate_replace_function_form(edit_step)


def _validate_form_a(edit_step: dict[str, Any]) -> None:
    if _edit_grounding_source(edit_step) != "anchor":
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "Form A edit_file must declare grounding_source=anchor"},
        )
    if _edit_read_step_idx(edit_step) is not None:
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "Form A edit_file may not declare read_step_idx"},
        )
    if not _step_has_search_range(edit_step):
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "Form A edit_file must declare explicit search_range inside the anchor window"},
        )
    if str(_step_replacement_text(edit_step) or "") == "":
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "Form A edit_file must declare replacement_text"},
        )
    args = _step_args(edit_step)
    if any(str(args.get(key) or edit_step.get(key) or "") for key in ("search", "replace", "search_replace_blocks")):
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "Form A edit_file must not emit textual SEARCH/REPLACE fields"},
        )


def _validate_form_b(read_step: dict[str, Any], edit_step: dict[str, Any]) -> None:
    if _edit_grounding_source(edit_step) != "read_range":
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "Form B edit_file must declare grounding_source=read_range"},
        )
    if _edit_read_step_idx(edit_step) != 0:
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "Form B edit_file must declare read_step_idx=0"},
        )
    if _step_file(read_step) != _step_file(edit_step):
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {
                "reason": "Form B read_range and edit_file must target the same file",
                "read_file": _step_file(read_step),
                "edit_file": _step_file(edit_step),
            },
        )
    if _step_has_search_range(edit_step):
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "Form B edit_file must not emit live-file search_range"},
        )
    if _step_edit_range_within_read(edit_step) is None:
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "Form B edit_file must declare edit_range_within_read"},
        )
    if str(_step_replacement_text(edit_step) or "") == "":
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "Form B edit_file must declare replacement_text"},
        )
    args = _step_args(edit_step)
    if any(str(args.get(key) or edit_step.get(key) or "") for key in ("search", "replace", "search_replace_blocks")):
        raise V2ExecutablePlanProtocolError(
            "PLANNER_V2_PROTOCOL_INVALID",
            {"reason": "Form B edit_file must not emit textual SEARCH/REPLACE fields"},
        )
