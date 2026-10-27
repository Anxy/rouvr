from __future__ import annotations

import json
import logging
import re
from typing import Any

from backend.services.aix_v2_executable_plan_protocol_service import (
    V2ExecutablePlanProtocolError,
    validate_v2_executable_plan_protocol,
)
from backend.services.aix_v2_live_file_grounding_service import (
    V2LiveFileGroundingError,
    is_v2_planner_packet,
    validate_v2_edit_against_live_file,
)
from backend.services.devexecutor_mutation_tool_taxonomy_service import is_code_mutation_tool

logger = logging.getLogger(__name__)
PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION = "packet_bound_replace_function"


class QualityEnforcementError(RuntimeError):
    def __init__(self, reason_code: str, detail: dict[str, Any] | None = None) -> None:
        self.reason_code = str(reason_code or "").strip() or "QUALITY_ENFORCEMENT_VIOLATION"
        self.detail = dict(detail or {})
        message = self.reason_code
        if self.detail:
            message += " detail=" + json.dumps(self.detail, ensure_ascii=True, sort_keys=True)
        super().__init__(message)


def _reason_code_to_blocked_reason(reason_code: str | None) -> str | None:
    normalized = str(reason_code or "").strip()
    if not normalized:
        return None
    return normalized.lower()


def _step_tool(step: dict[str, Any]) -> str:
    return str(step.get("tool") or step.get("type") or "").strip()


def _step_args(step: dict[str, Any]) -> dict[str, Any]:
    return dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}


def _step_relative_path(step: dict[str, Any]) -> str | None:
    args = _step_args(step)
    for key in ("relative_path", "path", "file", "filename"):
        value = str(args.get(key) or step.get(key) or "").strip()
        if value:
            return value.replace("\\", "/")
    return None


def _read_buffer_entry(read_buffers: dict[str, Any], relative_path: str | None) -> dict[str, Any]:
    if not relative_path:
        return {}
    value = read_buffers.get(relative_path)
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value:
        return {"text": value}
    return {}


def _extract_search_replace_pairs(step: dict[str, Any]) -> list[tuple[str, str]]:
    args = _step_args(step)
    payload = str(args.get("search_replace_blocks") or "")
    pattern = re.compile(
        r"<<<<<<< SEARCH\s*\n(?P<search>.*?)\n=======\s*\n(?P<replace>.*?)\n>>>>>>> REPLACE",
        flags=re.DOTALL,
    )
    matches = list(pattern.finditer(payload))
    if matches:
        return [(str(match.group("search") or ""), str(match.group("replace") or "")) for match in matches]
    bare = re.fullmatch(
        r"\s*SEARCH\s*:?\s*\n(?P<search>.*?)(?:\r?\n)?REPLACE\s*:?\s*\n(?P<replace>.*)\s*",
        payload,
        flags=re.DOTALL,
    )
    if bare:
        return [(str(bare.group("search") or ""), str(bare.group("replace") or ""))]
    return []


def _read_output_content(step: dict[str, Any], read_buffers: dict[str, str]) -> str | None:
    args = _step_args(step)
    for key in ("output_content", "content", "read_buffer", "buffer", "result_text"):
        value = step.get(key)
        if isinstance(value, str) and value:
            return value
        nested = args.get(key)
        if isinstance(nested, str) and nested:
            return nested
    result = step.get("result")
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, str) and content:
            return content
    relative_path = _step_relative_path(step)
    if relative_path:
        entry = _read_buffer_entry(read_buffers, relative_path)
        text = entry.get("text")
        if isinstance(text, str) and text:
            return text
    return None


def _normalize_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    return normalized.strip()


def _has_mutating_step(steps: list[dict[str, Any]]) -> bool:
    return any(
        is_code_mutation_tool(_step_tool(step), include_deprecated=True) for step in steps if isinstance(step, dict)
    )


def _failure_analysis_count(steps: list[dict[str, Any]]) -> int:
    return sum(1 for step in steps if isinstance(step, dict) and _step_tool(step) == "failure_analysis")


def _planner_mode(planner_packet: dict[str, Any]) -> str:
    explicit = str(planner_packet.get("planner_mode") or "").strip()
    if explicit in {PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION, "generic"}:
        return explicit
    return "generic"


def validate_planner_output(payload: dict[str, Any], context: dict[str, Any]) -> None:
    steps = [dict(step) for step in list(payload.get("steps") or []) if isinstance(step, dict)]
    read_buffers = dict(context.get("read_buffers") or {})
    step_outputs = {
        int(key): dict(value)
        for key, value in dict(context.get("step_outputs") or {}).items()
        if isinstance(key, int) and isinstance(value, dict)
    }
    grounded_symbol_source = str(context.get("grounded_symbol_source") or "").strip()
    task = dict(context.get("task") or {})
    planner_packet = dict(context.get("planner_packet") or {})
    planner_mode = _planner_mode(planner_packet)
    workspace_root = context.get("workspace_root")
    protocol_form: str | None = None
    if workspace_root and is_v2_planner_packet(planner_packet) and planner_mode == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION:
        try:
            protocol = validate_v2_executable_plan_protocol(steps=steps, planner_packet=planner_packet)
        except V2ExecutablePlanProtocolError as exc:
            raise QualityEnforcementError(exc.code, exc.detail) from exc
        protocol_form = protocol.form if protocol is not None else None
    prior_steps: list[dict[str, Any]] = []
    for step in steps:
        tool = _step_tool(step)
        if tool in {"read_file", "read_range"}:
            relative_path = _step_relative_path(step)
            read_content = _read_output_content(step, read_buffers)
            step_idx = step.get("step_idx")
            if relative_path and isinstance(read_content, str) and read_content:
                entry = _read_buffer_entry(read_buffers, relative_path)
                entry["text"] = read_content
                if isinstance(step_idx, int):
                    entry["step_idx"] = int(step_idx)
                read_buffers[relative_path] = entry
            prior_steps.append(step)
            continue
        if tool == "replace_function":
            prior_steps.append(step)
            continue
        if tool != "edit_file":
            prior_steps.append(step)
            continue
        relative_path = _step_relative_path(step)
        args = _step_args(step)
        read_source = dict(args.get("read_source") or {}) if isinstance(args.get("read_source"), dict) else {}
        if read_source and planner_mode == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION:
            raise QualityEnforcementError(
                "PLANNER_EDIT_CROSS_STEP_LINKAGE_FORBIDDEN",
                {"file": relative_path, "reason": "edit_file must be self-contained and may not declare read_source"},
            )
        pairs = _extract_search_replace_pairs(step)
        if pairs and all(_normalize_text(search) == _normalize_text(replace) for search, replace in pairs):
            raise QualityEnforcementError(
                "MUTATION_REQUIRED_BUT_ABSENT",
                {"file": relative_path, "reason": "edit_file_missing_semantic_mutation"},
            )
        if workspace_root and is_v2_planner_packet(planner_packet) and planner_mode == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION:
            try:
                if protocol_form == "A":
                    validate_v2_edit_against_live_file(
                        workspace_root=workspace_root,
                        args=args,
                        planner_packet=planner_packet,
                        read_buffers=read_buffers,
                    )
            except V2LiveFileGroundingError as exc:
                raise QualityEnforcementError(exc.code, exc.detail) from exc
        prior_steps.append(step)
    if str(task.get("task_intent") or "").strip() == "code_change_required":
        has_mutation = _has_mutating_step(steps)
        failure_analysis_count = _failure_analysis_count(steps)
        if has_mutation and failure_analysis_count:
            raise QualityEnforcementError(
                "PLANNER_OUTPUT_AMBIGUOUS_OUTCOME",
                {
                    "reason": "code_change_plan_must_choose_mutation_or_failure_analysis",
                    "mutating_steps_present": True,
                    "failure_analysis_count": failure_analysis_count,
                },
            )
        if failure_analysis_count > 1:
            raise QualityEnforcementError(
                "PLANNER_OUTPUT_AMBIGUOUS_OUTCOME",
                {
                    "reason": "multiple_failure_analysis_steps",
                    "failure_analysis_count": failure_analysis_count,
                },
            )
        if not has_mutation and failure_analysis_count == 0:
            logger.warning(
                "mutation_gate_emit %s",
                json.dumps(
                    {
                        "source": "aix_quality_enforcement.validate_planner_output",
                        "reason_code": "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE",
                        "step_tools": [_step_tool(step) for step in steps if isinstance(step, dict)],
                        "task_intent": str(task.get("task_intent") or "").strip() or None,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            )
            raise QualityEnforcementError(
                "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE",
                {"reason": "mutation_or_failure_analysis_required_for_code_change_task"},
            )


def validate_executor_output(payload: dict[str, Any], context: dict[str, Any]) -> None:
    first_blocker_reason = str(context.get("first_blocker_reason") or "").strip() or None
    if not first_blocker_reason:
        return
    first_blocker_summary = str(context.get("first_blocker_summary") or "").strip() or None
    first_blocker_blocked_reason = str(
        context.get("first_blocker_blocked_reason") or ""
    ).strip() or _reason_code_to_blocked_reason(first_blocker_reason)
    quality_gate = dict(payload.get("quality_gate") or {})
    previous_gate_code = str(quality_gate.get("code") or "").strip() or None
    if (
        previous_gate_code
        in {
            "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE",
            "MUTATION_REQUIRED_BUT_ABSENT",
        }
        and previous_gate_code != first_blocker_reason
    ):
        quality_gate["secondary_code"] = previous_gate_code
        if first_blocker_summary:
            quality_gate["secondary_message"] = str(quality_gate.get("message") or "").strip() or previous_gate_code
            quality_gate["message"] = first_blocker_summary
        quality_gate["code"] = first_blocker_reason
        quality_gate["blocked_reason"] = first_blocker_blocked_reason
        payload["quality_gate"] = quality_gate
    control_plane_outcome = dict(payload.get("control_plane_outcome") or {})
    nested_result = dict(payload.get("result") or {})
    nested_evidence = dict(nested_result.get("evidence") or {})
    nested_control_plane = dict(nested_evidence.get("control_plane_outcome") or {})
    for target in (control_plane_outcome, nested_control_plane):
        previous_reason = str(target.get("reason_code") or "").strip() or None
        if previous_reason and not (
            previous_reason.startswith("PLANNER_EDIT_")
            or previous_reason.startswith("EDIT_FILE_")
            or previous_reason == "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE"
        ):
            continue
        if previous_reason and previous_reason != first_blocker_reason:
            target["secondary_reason_code"] = previous_reason
        previous_summary = str(target.get("reason_summary") or "").strip() or None
        if previous_summary and previous_reason and previous_reason != first_blocker_reason:
            target["secondary_reason_summary"] = previous_summary
        target["reason_code"] = first_blocker_reason
        if first_blocker_summary:
            target["reason_summary"] = first_blocker_summary
        if first_blocker_blocked_reason:
            target["blocked_reason"] = first_blocker_blocked_reason
    if control_plane_outcome:
        payload["control_plane_outcome"] = control_plane_outcome
    if nested_control_plane:
        nested_evidence["control_plane_outcome"] = nested_control_plane
        nested_result["evidence"] = nested_evidence
        payload["result"] = nested_result


def validate(stage: str, payload: dict[str, Any], context: dict[str, Any] | None = None) -> None:
    normalized_stage = str(stage or "").strip().upper()
    active_context = dict(context or {})
    if normalized_stage == "PLANNER_OUTPUT":
        if not isinstance(payload, dict) or "steps" not in payload:
            raise QualityEnforcementError("PLANNER_OUTPUT_NOT_JSON", {"reason": "missing_steps_object"})
        validate_planner_output(payload, active_context)
    elif normalized_stage == "EXECUTOR_OUTPUT":
        validate_executor_output(payload, active_context)


__all__ = [
    "QualityEnforcementError",
    "validate",
    "validate_executor_output",
    "validate_planner_output",
]
