"""Shared devexecutor plan step validator."""

from __future__ import annotations

import json
from typing import Any

from backend.services.aix_v2_live_file_grounding_service import (
    V2LiveFileGroundingError,
    validate_v2_edit_against_live_file,
)


def _decode_validation_error(error: str | None) -> tuple[str, dict[str, Any]]:
    if not error:
        return "", {}
    text = str(error)
    if "::" not in text:
        return text, {}
    code, raw_details = text.split("::", 1)
    try:
        details = json.loads(raw_details)
    except json.JSONDecodeError:
        details = {"detail": raw_details}
    return code, details if isinstance(details, dict) else {"detail": details}


def _encode_validation_error(code: str, details: dict[str, Any] | None = None) -> str:
    payload = json.dumps(dict(details or {}), sort_keys=True)
    return f"{code}::{payload}"


def _step_tool(step: dict[str, Any]) -> str:
    return str(step.get("tool") or step.get("type") or "").strip()


def _step_args(step: dict[str, Any]) -> dict[str, Any]:
    args = step.get("args")
    if isinstance(args, dict):
        return dict(args)
    return {key: value for key, value in step.items() if key not in {"tool", "type"} and not key.startswith("_")}


def _command_text(args: dict[str, Any]) -> str:
    raw = args.get("command_text", args.get("command"))
    if isinstance(raw, list):
        return " ".join(str(part) for part in raw)
    return str(raw or "").strip()


def _is_mutation_step(tool: str) -> bool:
    return tool in {"edit_file", "apply_patch", "write_file"}


def validate_planned_steps(
    *,
    steps: list[dict[str, Any]],
    task_intent: str,
    metadata: dict[str, Any],
    workspace_root: Any | None = None,
) -> str | None:
    """Validate a list of planned devexecutor steps.

    Returns an error code string if validation fails, or ``None`` if valid.
    """
    normalized_steps = [dict(step or {}) for step in list(steps or [])]
    if not normalized_steps:
        return _encode_validation_error("PLAN_STEPS_REQUIRED")

    target_files = [str(item).replace("\\", "/") for item in list((metadata or {}).get("target_files") or [])]
    validation_commands = [
        str(item).strip() for item in list((metadata or {}).get("validation_commands") or []) if str(item).strip()
    ]
    saw_mutation = False
    saw_validation = False
    planner_packet = dict((metadata or {}).get("planner_packet") or {})

    for index, step in enumerate(normalized_steps):
        tool = _step_tool(step)
        args = _step_args(step)
        if _is_mutation_step(tool):
            saw_mutation = True
            relative_path = str(args.get("relative_path") or args.get("file") or args.get("path") or "").replace(
                "\\", "/"
            )
            if target_files and relative_path and relative_path not in target_files:
                return _encode_validation_error(
                    "PLAN_MUTATION_OUTSIDE_TARGET_FILES",
                    {"step_index": index, "relative_path": relative_path, "target_files": target_files},
                )
            if tool == "edit_file" and workspace_root is not None:
                try:
                    validate_v2_edit_against_live_file(
                        workspace_root=workspace_root,
                        args=args,
                        planner_packet=planner_packet or None,
                    )
                except V2LiveFileGroundingError as exc:
                    return _encode_validation_error(exc.code, exc.context)
        if tool in {"cmd.run", "run_command"}:
            command_text = _command_text(args)
            if command_text and command_text in validation_commands:
                saw_validation = True

    if str(task_intent or "").strip() == "code_change_required" and not saw_mutation:
        return _encode_validation_error("PLAN_REQUIRES_MUTATION")
    if validation_commands and not saw_validation:
        return _encode_validation_error("PLAN_REQUIRES_DECLARED_VALIDATION_COMMAND")
    return None


__all__ = ["_decode_validation_error", "validate_planned_steps"]
