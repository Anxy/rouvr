from __future__ import annotations

import ast
import py_compile
import re
import shlex
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from math import ceil
from pathlib import Path
from time import monotonic
from typing import Any

import structlog

try:
    import tiktoken
except ImportError:  # pragma: no cover - fallback path is exercised when dependency is unavailable.
    tiktoken = None

from backend.services.aix_v2_live_file_grounding_service import (
    V2LiveFileGroundingError,
    is_v2_planner_packet,
    validate_v2_edit_against_live_file,
)
from backend.services.devexecutor_command_runner_service import CommandPolicyError, run_command
from backend.services.devexecutor_content_validator_service import CONTENT_MUTATION_TOOLS, validate_step_content
from backend.services.devexecutor_mutation_tools_service import (
    DEPRECATED_MUTATION_TOOL_FLAGS,
    apply_patch,
    edit_file,
    extend_python_signature,
    insert_at_anchor,
    orchestration_deprecated_mutation_tool_enabled,
    replace_function,
    replace_range,
    write_file,
)
from backend.services.devexecutor_python_symbol_service import verify_symbol_anchor
from backend.services.devexecutor_readonly_tools_service import get_status, list_dir, read_file, read_range, search
from backend.services.devexecutor_search_replace_blocks_service import (
    SearchReplaceBlockError,
    parse_search_replace_blocks,
)

# Tools that write/mutate Python source and should trigger automatic py_compile check (ROBQ-4).
_PY_COMPILE_HOOK_TOOLS: frozenset[str] = frozenset(
    {"write_file", "edit_file", "extend_python_signature", "replace_function"}
)
_PYTEST_TIMEOUT_FLOOR_SECONDS = 120.0
_PYTEST_TIMEOUT_CEILING_SECONDS = 180.0
_CL100K_ENCODER: Any | None = None
_TOOL_LOOP_LOGGER = structlog.get_logger("hyeve_aix_service.devexecutor.tool_loop")


def _get_cl100k_encoder() -> Any | None:
    global _CL100K_ENCODER
    if _CL100K_ENCODER is not None:
        return _CL100K_ENCODER
    if tiktoken is None:
        return None
    try:
        _CL100K_ENCODER = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _CL100K_ENCODER = False
        return None
    return _CL100K_ENCODER


def _estimate_text_tokens(text: str) -> int:
    normalized = str(text or "")
    if not normalized:
        return 0
    encoder = _get_cl100k_encoder()
    if encoder is not None:
        try:
            return len(encoder.encode(normalized, disallowed_special=()))
        except Exception:
            pass
    # Fallback for environments where tiktoken is not available.
    return max(1, ceil(len(normalized) / 4))


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except Exception:
        return None


def _split_cmd_run_command_text(command_text: str) -> list[str]:
    return [str(item) for item in shlex.split(str(command_text or ""), posix=True) if str(item).strip()]


def _command_result_output_text(result: dict[str, Any]) -> str:
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    return "\n".join(part for part in (stdout, stderr) if part).strip()


def _validation_output_contains_passed_marker(result: dict[str, Any]) -> bool:
    return bool(re.search(r"\bPASSED\b", _command_result_output_text(result), flags=re.IGNORECASE))


def _declared_validation_requires_passed_marker(command: list[str]) -> bool:
    return _is_pytest_command(command)


def _emit_execution_event(
    *,
    execution_context: dict[str, Any] | None,
    event_name: str,
    phase: str,
    reason_code: str | None = None,
    classification: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    event_payload = {
        "event_name": event_name,
        "phase": phase,
        "reason_code": str(reason_code or "").strip() or None,
        "classification": str(classification or "").strip() or None,
        **dict(payload or {}),
    }
    if execution_context is not None:
        events = list(execution_context.get("structured_events") or [])
        events.append(event_payload)
        execution_context["structured_events"] = events
    _TOOL_LOOP_LOGGER.info(event_name, **event_payload)


def _normalize_declared_validation_command_payload(step: dict[str, Any]) -> dict[str, Any] | None:
    if str(step.get("tool") or "").strip() != "cmd.run":
        return None
    args = dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}
    command_text = str(args.get("command_text") or "").strip()
    command = args.get("command")
    if isinstance(command, list) and command:
        normalized_command = [str(part) for part in command]
    elif command_text:
        normalized_command = _split_cmd_run_command_text(command_text)
    else:
        normalized_command = []
    if not normalized_command:
        return None
    return {
        "command": normalized_command,
        "command_text": command_text or " ".join(normalized_command),
        "timeout_seconds": args.get("timeout_seconds"),
    }


def _remember_declared_validation_command(
    *,
    execution_context: dict[str, Any] | None,
    steps: list[dict[str, Any]],
) -> None:
    if execution_context is None:
        return
    for step in reversed(list(steps or [])):
        payload = _normalize_declared_validation_command_payload(step)
        if payload is not None:
            execution_context["declared_validation_command"] = payload
            return


def _declared_validation_command_step(*, execution_context: dict[str, Any] | None) -> dict[str, Any] | None:
    declared = dict((execution_context or {}).get("declared_validation_command") or {})
    command = declared.get("command")
    if not isinstance(command, list) or not command:
        return None
    command_text = str(declared.get("command_text") or "").strip() or " ".join(str(part) for part in command)
    return {
        "tool": "cmd.run",
        "args": {
            "command_text": command_text,
            "timeout_seconds": declared.get("timeout_seconds"),
        },
        "synthetic_declared_validation": True,
    }


def _ensure_terminal_declared_validation_step(
    *,
    steps: list[dict[str, Any]],
    execution_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    declared_step = _declared_validation_command_step(execution_context=execution_context)
    if declared_step is None:
        return list(steps or [])
    has_mutation = any(str(step.get("tool") or "").strip() == "edit_file" for step in list(steps or []))
    if not has_mutation:
        return list(steps or [])
    if steps:
        last_payload = _normalize_declared_validation_command_payload(dict(steps[-1] or {}))
        expected_payload = _normalize_declared_validation_command_payload(declared_step)
        if last_payload == expected_payload:
            return list(steps or [])
    return [*list(steps or []), declared_step]


def _transcript_text_payload(entry: dict[str, Any]) -> str:
    tool = str(entry.get("tool") or "")
    result = entry.get("result")
    if not isinstance(result, dict):
        return ""
    if tool in {"read_file", "read_range"}:
        content = result.get("content")
        return content if isinstance(content, str) else ""
    if tool == "cmd.run":
        parts: list[str] = []
        stdout = result.get("stdout")
        stderr = result.get("stderr")
        if isinstance(stdout, str) and stdout:
            parts.append(stdout)
        if isinstance(stderr, str) and stderr:
            parts.append(stderr)
        return "\n".join(parts)
    return ""


def _append_transcript_entry(
    *, transcript: list[dict[str, Any]], entry: dict[str, Any], budget_used: dict[str, int]
) -> None:
    payload = _transcript_text_payload(entry)
    tokens_estimate = _estimate_text_tokens(payload)
    entry["_tokens_estimate"] = tokens_estimate
    budget_used["tokens"] = int(budget_used.get("tokens", 0)) + tokens_estimate
    entry["_budget_used"] = int(budget_used.get("tokens", 0))
    transcript.append(entry)


def _cmd_run_feedback_payload(entry: dict[str, Any]) -> dict[str, Any] | None:
    if str(entry.get("tool") or "") != "cmd.run":
        return None
    if bool(entry.get("ok")):
        return None
    if bool(entry.get("recoverable")):
        return None
    result = entry.get("result")
    if not isinstance(result, dict):
        return None
    if bool(result.get("timed_out")):
        return None
    try:
        returncode = int(str(result.get("returncode")))
    except Exception:
        return None
    if returncode == 0:
        return None
    command = result.get("command")
    command_parts = [str(item).strip() for item in command] if isinstance(command, list) else []
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    feedback = {
        "command": command_parts,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
    }
    feedback["message"] = (
        f"cmd.run failed with returncode={returncode} command={' '.join(command_parts)}\n"
        f"stdout:\n{stdout}\n"
        f"stderr:\n{stderr}"
    ).strip()
    return feedback


def _auto_py_compile(*, workspace_root: str | Path, relative_path: str, index: int) -> dict[str, Any]:
    """Run py_compile in-process and return a synthetic transcript-style entry (ROBQ-4)."""
    abs_path = (Path(workspace_root) / relative_path).resolve()
    synthetic_command = ["python3", "-m", "py_compile", relative_path]
    started_at = datetime.now(UTC).isoformat()
    started_perf = monotonic()
    try:
        py_compile.compile(str(abs_path), doraise=True)
        elapsed_ms = int((monotonic() - started_perf) * 1000)
        return {
            "index": index,
            "tool": "cmd.run",
            "ok": True,
            "auto_injected": True,
            "result": {
                "command": synthetic_command,
                "returncode": 0,
                "stdout": "SYNTAX_OK",
                "stderr": "",
                "injection_kind": "py_compile",
            },
            "diagnostics": {"started_at_utc": started_at, "elapsed_ms": elapsed_ms},
        }
    except py_compile.PyCompileError as exc:
        elapsed_ms = int((monotonic() - started_perf) * 1000)
        return {
            "index": index,
            "tool": "cmd.run",
            "ok": False,
            "auto_injected": True,
            "quality_gate": "COMPILE_FAIL",
            "error": f"QUALITY_WARN: COMPILE_FAIL {exc}",
            "result": {
                "command": synthetic_command,
                "returncode": 1,
                "stdout": "",
                "stderr": str(exc),
                "injection_kind": "py_compile",
            },
            "diagnostics": {"started_at_utc": started_at, "elapsed_ms": elapsed_ms},
        }


def _normalize_relative_path(*, workspace_root: str | Path, path_value: str) -> str:
    raw = str(path_value or "").strip()
    if not raw:
        return raw
    workspace_text = str(workspace_root or "").strip().replace("\\", "/").rstrip("/")
    candidate = raw.replace("\\", "/")
    if workspace_text and candidate == workspace_text:
        return "."
    prefix = f"{workspace_text}/" if workspace_text else ""
    if prefix and candidate.startswith(prefix):
        stripped = candidate[len(prefix) :].strip("/")
        return stripped or "."
    if candidate.startswith("/app/") and workspace_text:
        workspace_parts = [part for part in workspace_text.split("/") if part]
        candidate_parts = [part for part in candidate.removeprefix("/app/").split("/") if part]
        best_match = 0
        max_match = min(len(workspace_parts), len(candidate_parts))
        for match_len in range(1, max_match + 1):
            if workspace_parts[-match_len:] == candidate_parts[:match_len]:
                best_match = match_len
        if best_match:
            stripped_parts = candidate_parts[best_match:]
            return "/".join(stripped_parts) if stripped_parts else "."
    return raw


def _consume_path_alias(*, workspace_root: str | Path, args: dict[str, Any]) -> None:
    if "relative_path" not in args:
        path_value = args.pop("path", None)
        if not (isinstance(path_value, str) and path_value.strip()):
            path_value = args.pop("file", None)
        if not (isinstance(path_value, str) and path_value.strip()):
            path_value = args.pop("rel_path", None)
        if isinstance(path_value, str) and path_value.strip():
            args["relative_path"] = _normalize_relative_path(workspace_root=workspace_root, path_value=path_value)
    else:
        args.pop("path", None)
        args.pop("file", None)
        args.pop("rel_path", None)


def _consume_int_alias(args: dict[str, Any], target: str, *aliases: str) -> None:
    if target in args:
        for alias in aliases:
            args.pop(alias, None)
        return
    for alias in aliases:
        value = args.pop(alias, None)
        if value is None:
            continue
        args[target] = value
        break


def _consume_text_alias(args: dict[str, Any], target: str, *aliases: str) -> None:
    if target in args:
        for alias in aliases:
            args.pop(alias, None)
        return
    for alias in aliases:
        value = args.pop(alias, None)
        if isinstance(value, str):
            args[target] = value
            break


def _normalize_tool_args(*, workspace_root: str | Path, tool: str, raw_args: dict[str, Any]) -> dict[str, Any]:
    args = dict(raw_args or {})

    if tool in {
        "list_dir",
        "read_file",
        "read_range",
        "write_file",
        "edit_file",
        "extend_python_signature",
        "insert_at_anchor",
        "replace_range",
        "replace_in_function",
        "update_call_kwarg",
        "edit_symbol",
        "insert_after_symbol",
        "confirm_anchor",
        "search",
    }:
        _consume_path_alias(workspace_root=workspace_root, args=args)

    if tool == "search":
        if "pattern" not in args:
            query_value = args.pop("query", None)
            if isinstance(query_value, str) and query_value.strip():
                args["pattern"] = query_value.strip()
        else:
            args.pop("query", None)
        args.pop("include_patterns", None)

    if tool == "write_file":
        _consume_text_alias(args, "content", "text", "body")

    if tool == "edit_file":
        _consume_text_alias(args, "search_replace_blocks", "content", "text", "body", "blocks")

    if tool == "insert_at_anchor":
        _consume_text_alias(args, "anchor_text", "anchor", "anchor_quote", "search", "pattern")
        _consume_text_alias(args, "new_content", "content", "text", "body")
        _consume_text_alias(args, "position", "insert_position")
        _consume_text_alias(args, "indent_style", "indent")

    if tool == "extend_python_signature":
        _consume_text_alias(args, "symbol", "function_name", "symbol_name")
        _consume_text_alias(args, "parameter_name", "name")
        _consume_text_alias(args, "parameter_type", "annotation", "type")
        _consume_text_alias(args, "default_value", "default")
        _consume_text_alias(args, "parameter_kind", "kind")

    if tool == "read_range":
        _consume_int_alias(args, "start_line", "start", "line_start", "from_line")
        _consume_int_alias(args, "end_line", "end", "line_end", "to_line")

    if tool in {"read_file", "read_range"}:
        for metadata_key in ("output_content", "content", "read_buffer", "buffer", "result_text", "source"):
            args.pop(metadata_key, None)

    if tool == "replace_range":
        _consume_int_alias(args, "start_line", "start", "line_start", "from_line")
        _consume_int_alias(args, "end_line", "end", "line_end", "to_line")
        _consume_text_alias(args, "replacement", "content", "text", "body")

    if tool == "replace_in_function":
        _consume_text_alias(args, "function_name", "symbol_name", "symbol")
        _consume_text_alias(args, "replacement", "content", "text", "body")

    if tool == "replace_function":
        _consume_text_alias(args, "qualified_symbol", "function_name", "symbol_name", "symbol", "target_symbol")
        _consume_text_alias(args, "replacement", "replacement_text", "content", "text", "body")

    if tool == "update_call_kwarg":
        _consume_text_alias(args, "symbol", "function_name", "symbol_name")
        _consume_text_alias(args, "callee", "call_target")
        _consume_text_alias(args, "kwarg", "keyword", "arg_name")
        _consume_text_alias(args, "new_value", "value", "expression")

    if tool == "edit_symbol":
        _consume_text_alias(args, "symbol", "function_name", "symbol_name")
        _consume_text_alias(args, "mode", "edit_mode")
        _consume_text_alias(args, "new_signature", "signature")
        _consume_text_alias(args, "anchor_pattern", "anchor", "anchor_quote", "pattern")
        _consume_text_alias(args, "position", "insert_position")
        _consume_text_alias(args, "new_code", "content", "text", "body")

    if tool == "insert_after_symbol":
        _consume_text_alias(args, "symbol_name", "function_name", "symbol")
        _consume_text_alias(args, "new_content", "content", "text", "body")

    if tool == "confirm_anchor":
        _consume_text_alias(args, "symbol_name", "function_name", "symbol")
        _consume_text_alias(args, "anchor_quote", "anchor", "quote", "content", "text", "body")

    if tool == "apply_patch":
        _consume_text_alias(args, "patch_text", "patch", "diff")
        args.pop("path", None)
        args.pop("rel_path", None)
        fallback_replace = args.get("fallback_replace")
        if isinstance(fallback_replace, dict):
            fallback = dict(fallback_replace)
            _consume_path_alias(workspace_root=workspace_root, args=fallback)
            _consume_int_alias(fallback, "start_line", "start", "line_start", "from_line")
            _consume_int_alias(fallback, "end_line", "end", "line_end", "to_line")
            _consume_text_alias(fallback, "replacement", "content", "text", "body")
            args["fallback_replace"] = fallback

    if tool == "evidence.attach":
        _consume_text_alias(args, "evidence_ref", "ref", "uri", "artifact_ref")
        _consume_text_alias(args, "evidence_type", "type")

    if tool == "cmd.run":
        command_text = args.get("command_text")
        if isinstance(command_text, str) and command_text.strip():
            args["command"] = _split_cmd_run_command_text(command_text)
        else:
            command_value = args.get("command")
            if isinstance(command_value, str):
                args["command"] = _split_cmd_run_command_text(command_value)
            elif isinstance(command_value, (list, tuple)):
                args["command"] = [str(item) for item in command_value if str(item).strip()]
            else:
                _consume_text_alias(args, "command", "command_text", "cmd")
                command_value = args.get("command")
                if isinstance(command_value, str):
                    args["command"] = _split_cmd_run_command_text(command_value)

    return args


def _pytest_aware_timeout(args: dict[str, Any]) -> dict[str, Any]:
    command = args.get("command")
    if not isinstance(command, list) or not command:
        return args
    is_pytest = command[:3] == ["python", "-m", "pytest"] or str(command[0]).strip().lower() == "pytest"
    if not is_pytest:
        return args
    current = args.get("timeout_seconds")
    try:
        current_float = float(current) if current is not None else 0.0
    except (TypeError, ValueError):
        current_float = 0.0
    if current_float < _PYTEST_TIMEOUT_FLOOR_SECONDS:
        return {**args, "timeout_seconds": _PYTEST_TIMEOUT_FLOOR_SECONDS}
    if current_float > _PYTEST_TIMEOUT_CEILING_SECONDS:
        return {**args, "timeout_seconds": _PYTEST_TIMEOUT_CEILING_SECONDS}
    return args


def _recoverable_readonly_missing(*, tool: str, args: dict[str, Any], exc: FileNotFoundError) -> dict[str, Any]:
    return {
        "tool": tool,
        "ok": False,
        "recoverable": True,
        "error": f"READONLY_TARGET_NOT_FOUND path={exc}",
        "result": {
            "code": "readonly_target_not_found",
            "path": str(exc),
            "relative_path": args.get("relative_path"),
        },
    }


def _structured_mutation_error(*, tool: str, args: dict[str, Any], exc: Exception) -> dict[str, Any] | None:
    message = str(exc or "").strip()
    if "SEARCH_REPLACE_NOT_FOUND" in message.upper():
        block_index: int | None = None
        match = re.search(r"block\s+#(?P<index>\d+)", message, flags=re.IGNORECASE)
        if match is not None:
            try:
                block_index = int(str(match.group("index") or "0"))
            except Exception:
                block_index = None
        relative_path = str(args.get("relative_path") or "").replace("\\", "/").strip() or None
        target_path: str | None = None
        search_text: str | None = None
        lines = message.splitlines()
        for index, line in enumerate(lines):
            stripped = str(line or "").strip()
            if stripped.lower().startswith("target file:"):
                candidate = stripped.split(":", 1)[1].strip()
                if candidate:
                    target_path = candidate
                continue
            if stripped.lower() == "attempted search text:":
                search_text = "\n".join(lines[index + 1 :]).strip() or None
                break
        return {
            "tool": tool,
            "ok": False,
            "recoverable": False,
            "error": message,
            "result": {
                "code": "search_replace_mismatch",
                "reason_code": "SEARCH_REPLACE_NOT_FOUND",
                "message": message,
                "relative_path": relative_path,
                "path": target_path,
                "search_text": search_text,
                "block_index": block_index,
                "symbol_name": str(args.get("symbol") or args.get("function_name") or args.get("symbol_name") or ""),
            },
        }
    code = message.split(" ", 1)[0].split(":", 1)[0].strip()
    if code not in {
        "BASE_DIVERGED",
        "SYMBOL_DIVERGED",
        "SEARCH_REPLACE_NOT_FOUND",
        "SEARCH_REPLACE_AMBIGUOUS",
        "ANCHOR_NOT_FOUND",
        "ANCHOR_AMBIGUOUS",
        "EDIT_FILE_PATCH_APPLY_FAILED",
        "INSERT_AT_ANCHOR_PATCH_APPLY_FAILED",
        "PATCH_APPLY_FAILED",
        "PATCH_ATTEMPT_LIMIT_EXCEEDED",
    }:
        return None
    return {
        "tool": tool,
        "ok": False,
        "error": message,
        "result": {
            "code": code.lower(),
            "message": message,
            "relative_path": str(args.get("relative_path") or "").replace("\\", "/"),
            "symbol_name": str(args.get("symbol") or args.get("function_name") or args.get("symbol_name") or ""),
        },
    }


def _remember_last_read_buffer(
    *,
    execution_context: dict[str, Any] | None,
    tool: str,
    step_idx: int | None,
    args: dict[str, Any],
    result: dict[str, Any],
) -> None:
    if execution_context is None:
        return
    relative_path = str(args.get("relative_path") or "").replace("\\", "/").strip()
    content = result.get("content")
    if not relative_path or not isinstance(content, str):
        return
    anchor_requirements = dict(execution_context.get("planner_anchor_requirements") or {})
    anchor_requirement = dict(anchor_requirements.get(relative_path) or {})
    anchor_window_matched = False
    if tool == "read_range" and anchor_requirement:
        start_line = _safe_int(args.get("start_line"))
        end_line = _safe_int(args.get("end_line"))
        anchor_window_matched = start_line == anchor_requirement.get(
            "start_line"
        ) and end_line == anchor_requirement.get("end_line")
    execution_context["last_read_buffer"] = {
        "tool": tool,
        "step_idx": step_idx,
        "relative_path": relative_path,
        "content": content,
        "start_line": _safe_int(args.get("start_line")),
        "end_line": _safe_int(args.get("end_line")),
        "anchor_window_matched": anchor_window_matched,
        "anchor_requirement": anchor_requirement or None,
    }


def _remember_planner_anchor_requirement(
    *,
    execution_context: dict[str, Any] | None,
    step: dict[str, Any],
) -> None:
    if execution_context is None:
        return
    anchor_window = dict(step.get("planner_anchor_window") or {})
    relative_path = str(anchor_window.get("relative_path") or "").replace("\\", "/").strip()
    if not relative_path:
        return
    start_line = _safe_int(anchor_window.get("start_line"))
    end_line = _safe_int(anchor_window.get("end_line"))
    if start_line is None or end_line is None:
        return
    if start_line < 1 or end_line < start_line:
        return
    requirements = dict(execution_context.get("planner_anchor_requirements") or {})
    requirements[relative_path] = {
        "relative_path": relative_path,
        "start_line": start_line,
        "end_line": end_line,
        "symbol": str(anchor_window.get("symbol") or "").strip() or None,
        "signature": str(anchor_window.get("signature") or "").strip() or None,
    }
    execution_context["planner_anchor_requirements"] = requirements


def _remember_planner_grounded_buffer(
    *,
    execution_context: dict[str, Any] | None,
    step: dict[str, Any],
) -> None:
    if execution_context is None:
        return
    tool = str(step.get("tool") or "").strip()
    if tool not in {"read_file", "read_range", "edit_file"}:
        return
    args = dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}
    relative_path = str(args.get("relative_path") or "").replace("\\", "/").strip()
    grounded_content = step.get("planner_grounded_buffer")
    if not relative_path or not isinstance(grounded_content, str) or not grounded_content:
        return
    grounded_buffers = dict(execution_context.get("planner_grounded_buffers") or {})
    grounded_buffers[relative_path] = {
        "tool": tool,
        "relative_path": relative_path,
        "content": grounded_content,
        "step_idx": int(step.get("step_idx") or 0),
        "source": str(step.get("planner_grounded_source") or "grounded_symbol_source").strip()
        or "grounded_symbol_source",
    }
    execution_context["planner_grounded_buffers"] = grounded_buffers


def _normalize_grounding_buffer_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return ""
    normalized_lines = [line.rstrip(" \t") for line in normalized.split("\n")]
    normalized = "\n".join(normalized_lines)
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


def _remember_confirm_anchor_buffer(
    *,
    execution_context: dict[str, Any] | None,
    args: dict[str, Any],
    result: dict[str, Any],
) -> None:
    if execution_context is None or not bool(result.get("matched")):
        return
    relative_path = str(args.get("relative_path") or "").replace("\\", "/").strip()
    symbol_source = result.get("symbol_source")
    if not relative_path or not isinstance(symbol_source, str) or not symbol_source:
        return
    execution_context["last_read_buffer"] = {
        "tool": "confirm_anchor",
        "relative_path": relative_path,
        "content": symbol_source,
    }


def _buffer_grounding_error(
    *,
    relative_path: str,
    last_read_relative_path: str | None,
    search_text: str | None,
    actual_buffer_snippet: str | None = None,
    normalized_search_text: str | None = None,
    normalized_last_read_buffer_snippet: str | None = None,
    normalized_actual_buffer_snippet: str | None = None,
    containment: dict[str, Any] | None = None,
    message: str,
    contract_validation: dict[str, Any] | None = None,
    code: str = "GROUNDING_VIOLATION_BUFFER_MISMATCH",
    reason_code: str | None = None,
) -> dict[str, Any]:
    normalized_reason_code = str(reason_code or code or "GROUNDING_VIOLATION_BUFFER_MISMATCH").strip()
    normalized_code = str(code or normalized_reason_code or "GROUNDING_VIOLATION_BUFFER_MISMATCH").strip()
    return {
        "tool": "edit_file",
        "ok": False,
        "error": message,
        "result": {
            "code": normalized_code,
            "reason_code": normalized_reason_code,
            "message": message,
            "relative_path": relative_path,
            "last_read_relative_path": last_read_relative_path,
            "search_text": search_text,
            "actual_buffer_snippet": actual_buffer_snippet,
            "normalized_search_text": normalized_search_text,
            "normalized_last_read_buffer_snippet": normalized_last_read_buffer_snippet,
            "normalized_actual_buffer_snippet": normalized_actual_buffer_snippet,
            "containment": dict(containment or {}),
            "contract_validation": dict(contract_validation or {}),
        },
    }


def _live_file_grounding_error(
    *,
    relative_path: str,
    error: V2LiveFileGroundingError,
) -> dict[str, Any]:
    detail = dict(error.detail)
    detail.setdefault("relative_path", relative_path)
    reason = str(detail.get("reason") or error.code).strip()
    return {
        "tool": "edit_file",
        "ok": False,
        "error": f"{error.code} {reason}".strip(),
        "result": {
            "status": "blocked",
            "code": error.code,
            "reason_code": error.code,
            "message": reason,
            **detail,
        },
    }


def _extract_line_range_content(*, content: str, start_line: int, end_line: int) -> str:
    lines = str(content or "").splitlines(keepends=True)
    if start_line < 1 or end_line < start_line:
        return ""
    if start_line > len(lines):
        return ""
    return "".join(lines[start_line - 1 : end_line])


def _extract_grounding_buffer_snippet(*, content: str | None, search_text: str | None) -> str | None:
    if not isinstance(content, str):
        return None
    normalized_content = content.replace("\r\n", "\n")
    if not normalized_content:
        return None
    anchor = ""
    for line in str(search_text or "").replace("\r\n", "\n").splitlines():
        stripped = line.strip()
        if stripped:
            anchor = stripped
            break
    if anchor:
        match_index = normalized_content.find(anchor)
        if match_index >= 0:
            start = max(0, match_index - 160)
            end = min(len(normalized_content), match_index + len(anchor) + 240)
            return normalized_content[start:end]
    snippet_lines = normalized_content.splitlines()[:12]
    if not snippet_lines:
        return None
    return "\n".join(snippet_lines)


def _validate_edit_file_against_last_read_buffer(
    *,
    args: dict[str, Any],
    execution_context: dict[str, Any] | None,
    workspace_root: str | Path,
) -> dict[str, Any] | None:
    planner_context = dict((execution_context or {}).get("planner_context") or {})
    planner_packet = dict(planner_context.get("packet") or {})
    relative_path = str(args.get("relative_path") or "").replace("\\", "/").strip()
    if is_v2_planner_packet(planner_packet):
        try:
            read_buffers: dict[str, Any] = {}
            last_read_buffer = dict((execution_context or {}).get("last_read_buffer") or {})
            last_read_relative_path = str(last_read_buffer.get("relative_path") or "").replace("\\", "/").strip()
            last_read_content = str(last_read_buffer.get("content") or "")
            last_read_step_idx = _safe_int(last_read_buffer.get("step_idx"))
            last_read_start_line = _safe_int(last_read_buffer.get("start_line"))
            last_read_end_line = _safe_int(last_read_buffer.get("end_line"))
            if last_read_relative_path and last_read_content:
                entry: dict[str, Any] = {"text": last_read_content}
                if last_read_step_idx is not None:
                    entry["step_idx"] = last_read_step_idx
                if last_read_start_line is not None:
                    entry["start_line"] = last_read_start_line
                if last_read_end_line is not None:
                    entry["end_line"] = last_read_end_line
                read_buffers[last_read_relative_path] = entry
            grounding = validate_v2_edit_against_live_file(
                workspace_root=workspace_root,
                args=args,
                planner_packet=planner_packet,
                read_buffers=read_buffers,
            )
        except V2LiveFileGroundingError as exc:
            return _live_file_grounding_error(relative_path=relative_path, error=exc)
        args["relative_path"] = grounding.relative_path
        args["__aix_live_file_contract_validation"] = grounding.contract_validation()
        return None
    if execution_context is None:
        return None
    if not relative_path:
        return None
    read_source = dict(args.get("read_source") or {}) if isinstance(args.get("read_source"), dict) else {}
    read_source_step_idx = read_source.get("step_idx")
    last_read_buffer = dict((execution_context or {}).get("last_read_buffer") or {})
    anchor_requirements = dict((execution_context or {}).get("planner_anchor_requirements") or {})
    anchor_requirement = dict(anchor_requirements.get(relative_path) or {})
    last_read_relative_path = str(last_read_buffer.get("relative_path") or "").replace("\\", "/").strip() or None
    last_read_content = last_read_buffer.get("content")
    if (
        (not isinstance(last_read_content, str) or not last_read_relative_path)
        and isinstance(read_source_step_idx, int)
        and read_source_step_idx == 0
    ):
        grounded_buffers = dict((execution_context or {}).get("planner_grounded_buffers") or {})
        grounded = dict(grounded_buffers.get(relative_path) or {})
        grounded_content = grounded.get("content")
        if isinstance(grounded_content, str) and grounded_content:
            last_read_buffer = grounded
            last_read_relative_path = str(grounded.get("relative_path") or "").replace("\\", "/").strip() or None
            last_read_content = grounded_content
    if not isinstance(last_read_content, str) or not last_read_relative_path:
        return _buffer_grounding_error(
            relative_path=relative_path,
            last_read_relative_path=last_read_relative_path,
            search_text=None,
            message=(
                "GROUNDING_VIOLATION_BUFFER_MISMATCH edit_file requires an immediately preceding read_file or "
                "read_range on the same file before mutation"
            ),
        )
    if anchor_requirement and not bool(last_read_buffer.get("anchor_window_matched")):
        return {
            "tool": "edit_file",
            "ok": False,
            "error": (
                "PLANNER_SKIPPED_ANCHOR_WINDOW_READ edit_file requires the exact authoritative read_range window "
                f"before mutation relative_path={relative_path}"
            ),
            "result": {
                "code": "PLANNER_SKIPPED_ANCHOR_WINDOW_READ",
                "reason_code": "PLANNER_SKIPPED_ANCHOR_WINDOW_READ",
                "relative_path": relative_path,
                "target_symbol": anchor_requirement.get("symbol"),
                "signature": anchor_requirement.get("signature"),
                "required_tool": "read_range",
                "required_start_line": anchor_requirement.get("start_line"),
                "required_end_line": anchor_requirement.get("end_line"),
                "last_read_tool": last_read_buffer.get("tool"),
                "last_read_relative_path": last_read_relative_path,
                "last_read_anchor_window_matched": bool(last_read_buffer.get("anchor_window_matched")),
                "search_text": None,
                "planner_anchor_requirement": anchor_requirement,
            },
        }
    if last_read_relative_path != relative_path:
        return _buffer_grounding_error(
            relative_path=relative_path,
            last_read_relative_path=last_read_relative_path,
            search_text=None,
            message=(
                "GROUNDING_VIOLATION_BUFFER_MISMATCH edit_file SEARCH blocks must be grounded on the immediately "
                f"preceding read buffer for the same file current={relative_path} last_read={last_read_relative_path}"
            ),
        )
    try:
        blocks = parse_search_replace_blocks(str(args.get("search_replace_blocks") or ""))
    except SearchReplaceBlockError:
        return None
    current_file_content: str | None = None
    try:
        current_read_result = read_file(workspace_root=workspace_root, relative_path=relative_path)
        if isinstance(current_read_result, dict):
            current_content_candidate = current_read_result.get("content")
            if isinstance(current_content_candidate, str):
                current_file_content = current_content_candidate
    except Exception:
        current_file_content = None
    normalized_last_read_content = _normalize_grounding_buffer_text(str(last_read_content or ""))
    normalized_current_file_content = _normalize_grounding_buffer_text(str(current_file_content or ""))
    for block in blocks:
        contract_validation = {
            "status": "pending",
            "mode": "normalized_same_file_buffer",
            "relative_path": relative_path,
            "read_source_step_idx": read_source_step_idx,
        }
        if anchor_requirement and bool(last_read_buffer.get("anchor_window_matched")):
            exact_anchor_buffer = str(args.get("__aix_exact_anchor_buffer") or block.search or "")
            contract_validation.update(
                {
                    "mode": "exact_anchor_buffer",
                    "anchor_start_line": anchor_requirement.get("start_line"),
                    "anchor_end_line": anchor_requirement.get("end_line"),
                    "contract_rule": "edit_file.search must exactly equal the anchor read_range.output_content",
                }
            )
            anchor_start_line = _safe_int(anchor_requirement.get("start_line"))
            anchor_end_line = _safe_int(anchor_requirement.get("end_line"))
            current_anchor_content = ""
            if anchor_start_line is not None and anchor_end_line is not None:
                try:
                    current_anchor_result = read_range(
                        workspace_root=workspace_root,
                        relative_path=relative_path,
                        start_line=anchor_start_line,
                        end_line=anchor_end_line,
                    )
                except Exception:
                    current_anchor_result = None
                if isinstance(current_anchor_result, dict):
                    current_anchor_content = str(current_anchor_result.get("content") or "")
            if (
                not current_anchor_content
                and isinstance(current_file_content, str)
                and anchor_start_line is not None
                and anchor_end_line is not None
            ):
                current_anchor_content = _extract_line_range_content(
                    content=current_file_content,
                    start_line=anchor_start_line,
                    end_line=anchor_end_line,
                )
            exact_match_with_last_read = exact_anchor_buffer == str(last_read_content or "")
            exact_match_with_current_anchor = isinstance(current_file_content, str) and exact_anchor_buffer == str(
                current_anchor_content or ""
            )
            contract_validation.update(
                {
                    "exact_match_with_last_read": exact_match_with_last_read,
                    "exact_match_with_current_anchor_buffer": exact_match_with_current_anchor,
                    "before_buffer_excerpt": _extract_grounding_buffer_snippet(
                        content=str(last_read_content or ""),
                        search_text=str(last_read_content or ""),
                    ),
                    "current_anchor_buffer_excerpt": _extract_grounding_buffer_snippet(
                        content=current_anchor_content,
                        search_text=current_anchor_content,
                    ),
                }
            )
            if not exact_match_with_last_read or not exact_match_with_current_anchor:
                contract_validation["status"] = "failed"
                stale_anchor_workspace_mutated = exact_match_with_last_read and not exact_match_with_current_anchor
                if stale_anchor_workspace_mutated:
                    contract_validation["stale_anchor_workspace_mutated"] = True
                    return _buffer_grounding_error(
                        relative_path=relative_path,
                        last_read_relative_path=last_read_relative_path,
                        search_text=block.search,
                        actual_buffer_snippet=current_anchor_content
                        or _extract_grounding_buffer_snippet(
                            content=current_file_content,
                            search_text=block.search,
                        ),
                        message=(
                            "STALE_ANCHOR_WORKSPACE_MUTATED exact anchor buffer matched the planner read, "
                            "but the live workspace anchor changed before mutation. Another run or external "
                            "edit modified the grounded region."
                        ),
                        contract_validation=contract_validation,
                        code="GROUNDING_VIOLATION_BUFFER_MISMATCH",
                        reason_code="STALE_ANCHOR_WORKSPACE_MUTATED",
                    )
                return _buffer_grounding_error(
                    relative_path=relative_path,
                    last_read_relative_path=last_read_relative_path,
                    search_text=block.search,
                    actual_buffer_snippet=current_anchor_content
                    or _extract_grounding_buffer_snippet(
                        content=current_file_content,
                        search_text=block.search,
                    ),
                    message=(
                        "GROUNDING_VIOLATION_BUFFER_MISMATCH edit_file SEARCH blocks must match the exact "
                        "anchor-window buffer byte-for-byte before mutation"
                    ),
                    contract_validation=contract_validation,
                )
            contract_validation["status"] = "passed"
            continue
        normalized_search = _normalize_grounding_buffer_text(str(block.search or ""))
        containment = {
            "normalized_search_in_last_read": bool(
                normalized_search and normalized_search in normalized_last_read_content
            ),
            "normalized_search_in_current_buffer": bool(
                normalized_search and normalized_search in normalized_current_file_content
            ),
        }
        planner_context = dict((execution_context or {}).get("planner_context") or {})
        planner_packet = dict(planner_context.get("packet") or {})
        search_str = str(block.search or "")

        def _norm(s: str) -> str:
            return str(s or "").replace("\r\n", "\n").rstrip("\n")

        # AIX Core safe grounding (Codex counter-proposal)
        preloaded = str(planner_context.get("pre_loaded_read_result", {}).get("content", "") or "")
        current_live_buffer = str(current_file_content or "")
        live = _norm(current_live_buffer)
        source = _norm(preloaded or current_live_buffer)
        symbol_source_hash = str(
            planner_packet.get("symbol_source_hash")
            or ((planner_packet.get("codeintel_anchor") or {}).get("symbol_source_hash"))
            or ""
        ).strip()
        normalized_symbol_source_hash = symbol_source_hash.removeprefix("sha256:")
        containment["normalized_search_in_source_buffer"] = bool(_norm(search_str) and _norm(search_str) in source)

        if _norm(search_str) not in live:
            if normalized_symbol_source_hash and sha256(live.encode()).hexdigest() != normalized_symbol_source_hash:
                return _buffer_grounding_error(
                    relative_path=relative_path,
                    last_read_relative_path=last_read_relative_path,
                    search_text=search_str,
                    actual_buffer_snippet=_extract_grounding_buffer_snippet(
                        content=current_file_content,
                        search_text=search_str,
                    ),
                    normalized_search_text=normalized_search or None,
                    normalized_last_read_buffer_snippet=_extract_grounding_buffer_snippet(
                        content=normalized_last_read_content,
                        search_text=normalized_search,
                    ),
                    normalized_actual_buffer_snippet=_extract_grounding_buffer_snippet(
                        content=normalized_current_file_content,
                        search_text=normalized_search,
                    ),
                    containment=containment,
                    contract_validation=contract_validation,
                    message="Live file hash != pre_loaded hash. File changed after PacketFactory read. Re-emit packet.",
                )
            return {
                "tool": "edit_file",
                "ok": False,
                "error": "SEARCH_REPLACE_MISMATCH",
                "result": {
                    "status": "blocked",
                    "code": "SEARCH_REPLACE_MISMATCH",
                    "reason_code": "SEARCH_REPLACE_MISMATCH",
                    "failure_analysis": "Cannot apply bounded mutation: pre_loaded buffer differs from live file at offset 0. Planner generated non-verbatim search. Action: re-run PacketFactory to refresh grounding.",
                    "relative_path": relative_path,
                    "search_text": search_str,
                },
            }
        if not containment["normalized_search_in_last_read"]:
            return _buffer_grounding_error(
                relative_path=relative_path,
                last_read_relative_path=last_read_relative_path,
                search_text=search_str,
                actual_buffer_snippet=_extract_grounding_buffer_snippet(
                    content=current_file_content,
                    search_text=block.search,
                ),
                normalized_search_text=normalized_search or None,
                normalized_last_read_buffer_snippet=_extract_grounding_buffer_snippet(
                    content=normalized_last_read_content,
                    search_text=normalized_search,
                ),
                normalized_actual_buffer_snippet=_extract_grounding_buffer_snippet(
                    content=normalized_current_file_content,
                    search_text=normalized_search,
                ),
                containment=containment,
                contract_validation=contract_validation,
                message=(
                    "GROUNDING_VIOLATION_BUFFER_MISMATCH edit_file SEARCH blocks must be exact substrings of the "
                    "immediately preceding read buffer after grounding normalization"
                ),
            )
        if isinstance(current_file_content, str) and not containment["normalized_search_in_current_buffer"]:
            return _buffer_grounding_error(
                relative_path=relative_path,
                last_read_relative_path=last_read_relative_path,
                search_text=search_str,
                actual_buffer_snippet=_extract_grounding_buffer_snippet(
                    content=current_file_content,
                    search_text=block.search,
                ),
                normalized_search_text=normalized_search or None,
                normalized_last_read_buffer_snippet=_extract_grounding_buffer_snippet(
                    content=normalized_last_read_content,
                    search_text=normalized_search,
                ),
                normalized_actual_buffer_snippet=_extract_grounding_buffer_snippet(
                    content=normalized_current_file_content,
                    search_text=normalized_search,
                ),
                containment=containment,
                contract_validation=contract_validation,
                message=(
                    "GROUNDING_VIOLATION_BUFFER_MISMATCH edit_file SEARCH blocks must remain exact substrings of "
                    "the current live file buffer at mutation time after grounding normalization"
                ),
            )
    return None


def _run_declared_validation_command(
    *,
    workspace_root: str | Path,
    command: list[str],
    command_text: str,
    timeout_seconds: float | None,
    allow_prefixes: list[list[str]],
    command_runtime: dict[str, Any] | None,
    execution_context: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any] | None, dict[str, Any] | None]:
    max_attempts = 2
    attempts: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    exit_code = -1
    passed_marker_present = False
    requires_passed_marker = _declared_validation_requires_passed_marker(command)
    last_policy_event = ""
    last_policy_payload: dict[str, Any] = {}

    def _policy_hook(event: str, payload: dict[str, Any]) -> None:
        nonlocal last_policy_event, last_policy_payload
        last_policy_event = str(event or "")
        last_policy_payload = dict(payload or {})
        mapped_reason = "COMMAND_ALLOWED" if event == "command_allowed" else "COMMAND_DENIED"
        _emit_execution_event(
            execution_context=execution_context,
            event_name="validation_command_policy",
            phase="execution",
            reason_code=mapped_reason,
            classification="POLICY_FAIL" if event == "command_denied" else "success",
            payload={
                "validation_command": [str(part) for part in command],
                "policy_event": event,
                **dict(payload or {}),
            },
        )

    for attempt_number in range(1, max_attempts + 1):
        last_policy_event = ""
        last_policy_payload = {}
        try:
            result = run_command(
                workspace_root=workspace_root,
                allow_prefixes=allow_prefixes,
                runtime=command_runtime,
                command=[str(part) for part in command],
                timeout_seconds=float(timeout_seconds or _PYTEST_TIMEOUT_FLOOR_SECONDS),
                policy_hook=_policy_hook,
            )
            returncode = result.get("returncode")
            try:
                exit_code = int(str(returncode))
            except Exception:
                exit_code = -1
            passed_marker_present = _validation_output_contains_passed_marker(result)
            parser_result = (
                "passed_marker_required_and_found"
                if requires_passed_marker and passed_marker_present
                else "passed_marker_required_missing"
                if requires_passed_marker
                else "exit_code_zero_only"
            )
            attempt_ok = (
                (not bool(result.get("timed_out")))
                and exit_code == 0
                and ((not requires_passed_marker) or passed_marker_present)
            )
            classification = "success" if attempt_ok else "VERIFICATION_FAIL"
            reason_code = "VALIDATION_PASSED" if attempt_ok else "VALIDATION_FAILED"
        except CommandPolicyError as exc:
            result = {
                "command": [str(part) for part in command],
                "returncode": exit_code,
                "timed_out": False,
                "stdout": "",
                "stderr": str(exc),
                "policy_event": last_policy_event or "command_denied",
                "policy_source": str(last_policy_payload.get("policy_source") or "").strip() or None,
            }
            passed_marker_present = False
            parser_result = "command_denied"
            attempt_ok = False
            classification = "POLICY_FAIL"
            reason_code = "COMMAND_DENIED"
        allowlist_source = str(last_policy_payload.get("policy_source") or "").strip() or None
        allowlist_compliance = "allowed" if last_policy_event == "command_allowed" else "denied"
        classification_mapping = {
            "classification": classification,
            "reason_code": reason_code,
        }
        attempts.append(
            {
                "attempt_number": attempt_number,
                "command": [str(part) for part in list(result.get("command") or [])],
                "returncode": exit_code,
                "exit_code": exit_code,
                "timed_out": bool(result.get("timed_out")),
                "stdout": str(result.get("stdout") or ""),
                "stderr": str(result.get("stderr") or ""),
                "passed_marker_present": passed_marker_present,
                "requires_passed_marker": requires_passed_marker,
                "parser_result": parser_result,
                "allowlist_compliance": allowlist_compliance,
                "allowlist_source": allowlist_source,
                "policy_event": last_policy_event or None,
                "classification_mapping": classification_mapping,
            }
        )
        _emit_execution_event(
            execution_context=execution_context,
            event_name="validation_command_result",
            phase="execution",
            reason_code=reason_code,
            classification=classification,
            payload={
                "validation_command": [str(part) for part in command],
                "command_text": command_text,
                "exit_code": exit_code,
                "timed_out": bool(result.get("timed_out")),
                "requires_passed_marker": requires_passed_marker,
                "passed_marker_present": passed_marker_present,
                "parser_result": parser_result,
                "attempt_number": attempt_number,
                "allowlist_compliance": allowlist_compliance,
                "allowlist_source": allowlist_source,
            },
        )
        if attempt_ok:
            break
        if classification == "POLICY_FAIL":
            break
        if bool(result.get("timed_out")):
            break
    final_attempt = attempts[-1] if attempts else {}
    contract_validation = {
        "status": (
            "passed"
            if (
                not bool(result.get("timed_out"))
                and exit_code == 0
                and ((not requires_passed_marker) or passed_marker_present)
            )
            else "failed"
        ),
        "mode": "declared_validation_command",
        "command_text": command_text,
        "exit_code": exit_code,
        "passed_marker_present": passed_marker_present,
        "requires_passed_marker": requires_passed_marker,
        "attempts_used": len(attempts),
        "max_attempts": max_attempts,
        "validation_attempts": attempts,
        "stdout_excerpt": str(result.get("stdout") or "")[:4000],
        "stderr_excerpt": str(result.get("stderr") or "")[:4000],
        "allowlist_compliance": final_attempt.get("allowlist_compliance"),
        "allowlist_source": final_attempt.get("allowlist_source"),
        "classification_mapping": final_attempt.get("classification_mapping"),
    }
    return contract_validation["status"] == "passed", result, contract_validation


def _deprecated_mutation_tool_disabled_error(*, tool: str) -> dict[str, Any]:
    feature_flag = DEPRECATED_MUTATION_TOOL_FLAGS.get(tool)
    return {
        "tool": tool,
        "ok": False,
        "error": (f"DEPRECATED_MUTATION_TOOL_DISABLED primitive={tool} " f"feature_flag={feature_flag or 'unknown'}"),
        "result": {
            "code": "PLANNER_INVALID_TOOL_SELECTION",
            "reason_code": "DEPRECATED_MUTATION_TOOL_DISABLED",
            "primitive": tool,
            "feature_flag": feature_flag,
            "planner_phase": "execution_preflight",
            "primary_mutation_lane": "edit_file -> precheck -> patch -> apply -> postcheck",
        },
    }


def _removed_mutation_tool_error(*, tool: str) -> dict[str, Any]:
    return {
        "tool": tool,
        "ok": False,
        "error": f"REMOVED_MUTATION_TOOL primitive={tool} primary_lane=edit_file",
        "result": {
            "code": "removed_mutation_tool",
            "primitive": tool,
            "primary_mutation_lane": "edit_file -> precheck -> patch -> apply -> postcheck",
        },
    }


def _removed_mutation_mode_error(*, tool: str, mode: str) -> dict[str, Any]:
    return {
        "tool": tool,
        "ok": False,
        "error": f"REMOVED_MUTATION_MODE primitive={tool} mode={mode} primary_lane=edit_file",
        "result": {
            "code": "removed_mutation_mode",
            "primitive": tool,
            "mode": mode,
            "primary_mutation_lane": "edit_file -> precheck -> patch -> apply -> postcheck",
        },
    }


def execute_tool_step(
    *,
    workspace_root: str | Path,
    step: dict[str, Any],
    allow_prefixes: list[list[str]],
    attempt_tracker: dict[str, int],
    command_runtime: dict[str, Any] | None = None,
    mutation_runtime: dict[str, Any] | None = None,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tool = str(step.get("tool"))
    args = _normalize_tool_args(workspace_root=workspace_root, tool=tool, raw_args=dict(step.get("args") or {}))
    if tool == "edit_symbol":
        return _removed_mutation_tool_error(tool=tool)
    if tool == "insert_before_return":
        return _removed_mutation_tool_error(tool=tool)
    if tool == "insert_after_symbol":
        return _removed_mutation_tool_error(tool=tool)
    if tool == "replace_in_function":
        return _removed_mutation_tool_error(tool=tool)
    if tool == "update_call_kwarg":
        return _removed_mutation_tool_error(tool=tool)
    if tool in DEPRECATED_MUTATION_TOOL_FLAGS and not orchestration_deprecated_mutation_tool_enabled(tool):
        return _deprecated_mutation_tool_disabled_error(tool=tool)
    if tool == "list_dir":
        try:
            return {"tool": tool, "ok": True, "result": list_dir(workspace_root=workspace_root, **args)}
        except FileNotFoundError as exc:
            return _recoverable_readonly_missing(tool=tool, args=args, exc=exc)
    if tool == "read_file":
        try:
            result = read_file(workspace_root=workspace_root, **args)
            _remember_last_read_buffer(
                execution_context=execution_context,
                tool=tool,
                step_idx=_safe_int(step.get("step_idx")),
                args=args,
                result=result,
            )
            return {"tool": tool, "ok": True, "result": result}
        except FileNotFoundError as exc:
            return _recoverable_readonly_missing(tool=tool, args=args, exc=exc)
    if tool == "read_range":
        try:
            result = read_range(workspace_root=workspace_root, **args)
            _remember_last_read_buffer(
                execution_context=execution_context,
                tool=tool,
                step_idx=_safe_int(step.get("step_idx")),
                args=args,
                result=result,
            )
            return {"tool": tool, "ok": True, "result": result}
        except FileNotFoundError as exc:
            return _recoverable_readonly_missing(tool=tool, args=args, exc=exc)
    if tool == "search":
        try:
            return {"tool": tool, "ok": True, "result": search(workspace_root=workspace_root, **args)}
        except FileNotFoundError as exc:
            return _recoverable_readonly_missing(tool=tool, args=args, exc=exc)
    if tool == "get_status":
        return {"tool": tool, "ok": True, "result": get_status(workspace_root=workspace_root)}
    if tool == "confirm_anchor":
        relative_path = str(args.get("relative_path") or "").strip()
        resolved_path = str(args.get("resolved_path") or "").strip()
        symbol_name = str(args.get("symbol_name") or "").strip()
        anchor_quote = str(args.get("anchor_quote") or "")
        if not relative_path:
            raise ValueError("confirm_anchor requires args.relative_path")
        if not symbol_name:
            raise ValueError("confirm_anchor requires args.symbol_name")
        file_path = Path(resolved_path) if resolved_path else Path(workspace_root) / relative_path
        result = verify_symbol_anchor(
            file_path=file_path,
            symbol_name=symbol_name,
            anchor_quote=anchor_quote,
        )
        result["relative_path"] = relative_path.replace("\\", "/")
        if not bool(result.get("matched")):
            return {
                "tool": tool,
                "ok": False,
                "error": "GROUNDING_MISMATCH anchor did not match resolved symbol source",
                "result": result,
            }
        _remember_confirm_anchor_buffer(execution_context=execution_context, args=args, result=result)
        return {"tool": tool, "ok": True, "result": result}
    if tool in {"failure_analysis", "report_failure"}:
        detail = (
            dict(args.get("detail") or step.get("detail") or {})
            if isinstance(args.get("detail") or step.get("detail"), dict)
            else {}
        )
        failure_analysis = str(
            args.get("failure_analysis")
            or step.get("failure_analysis")
            or detail.get("detail")
            or detail.get("message")
            or "Planner reported grounded infeasibility for this bounded mutation."
        ).strip()
        return {
            "tool": "failure_analysis",
            "ok": True,
            "result": {
                "status": "completed",
                "reason_code": "PLANNER_COMPLETED_WITH_FAILURE_ANALYSIS",
                "failure_analysis": failure_analysis,
                "detail": detail,
            },
        }
    if tool == "write_file":
        return {
            "tool": tool,
            "ok": True,
            "result": write_file(
                workspace_root=workspace_root,
                runtime=mutation_runtime,
                **_strip_mutation_only_transport_args(args),
            ),
        }
    if tool == "edit_file":
        grounding_error = _validate_edit_file_against_last_read_buffer(
            args=args,
            execution_context=execution_context,
            workspace_root=workspace_root,
        )
        if grounding_error is not None:
            return grounding_error
        try:
            relative_path = str(args.get("relative_path") or "").replace("\\", "/").strip()
            grounding_validation = dict(args.pop("__aix_live_file_contract_validation", {}) or {})
            if not grounding_validation:
                grounding_validation = {
                    "status": "passed",
                    "mode": "normalized_same_file_buffer",
                    "relative_path": relative_path,
                    "read_source_step_idx": (
                        dict(args.get("read_source") or {}).get("step_idx")
                        if isinstance(args.get("read_source"), dict)
                        else None
                    ),
                }
            edit_range_within_read = (
                dict(args.get("edit_range_within_read") or {})
                if isinstance(args.get("edit_range_within_read"), dict)
                else {}
            )
            grounding_validation["applied_structural_patch"] = str(args.get("search_replace_blocks") or "")
            anchor_requirements = dict((execution_context or {}).get("planner_anchor_requirements") or {})
            relative_path = str(args.get("relative_path") or "").replace("\\", "/").strip()
            anchor_requirement = dict(anchor_requirements.get(relative_path) or {})
            last_read_buffer = dict((execution_context or {}).get("last_read_buffer") or {})
            if anchor_requirement and bool(last_read_buffer.get("anchor_window_matched")):
                grounding_validation["mode"] = "exact_anchor_buffer"
                grounding_validation["anchor_start_line"] = anchor_requirement.get("start_line")
                grounding_validation["anchor_end_line"] = anchor_requirement.get("end_line")
                grounding_validation["exact_match_with_last_read"] = True
                grounding_validation["exact_match_with_current_anchor_buffer"] = True
                grounding_validation["before_buffer_excerpt"] = _extract_grounding_buffer_snippet(
                    content=str(last_read_buffer.get("content") or ""),
                    search_text=str(last_read_buffer.get("content") or ""),
                )
                replacement_text = str(args.get("replace_text") or args.get("replacement_text") or "")
                if replacement_text:
                    grounding_validation["after_buffer_excerpt"] = _extract_grounding_buffer_snippet(
                        content=replacement_text,
                        search_text=replacement_text,
                    )
            search_range = dict(args.get("search_range") or {}) if isinstance(args.get("search_range"), dict) else {}
            replacement = str(args.get("replace_text") or args.get("replacement_text") or "")
            if edit_range_within_read or search_range:
                absolute_range = dict(grounding_validation.get("search_range") or {})
                if not absolute_range and edit_range_within_read:
                    declared_read_step_idx = _safe_int(args.get("read_step_idx"))
                    last_read_step_idx = _safe_int(last_read_buffer.get("step_idx"))
                    last_read_start_line = _safe_int(last_read_buffer.get("start_line"))
                    last_read_relative_path = (
                        str(last_read_buffer.get("relative_path") or "").replace("\\", "/").strip()
                    )
                    if (
                        last_read_relative_path == relative_path
                        and declared_read_step_idx is not None
                        and declared_read_step_idx == last_read_step_idx
                        and last_read_start_line is not None
                    ):
                        absolute_range = {
                            "start_line": last_read_start_line + int(edit_range_within_read["start_line"]) - 1,
                            "end_line": last_read_start_line + int(edit_range_within_read["end_line"]) - 1,
                        }
                        grounding_validation.update(
                            {
                                "mode": "live_file_read_range_within_read",
                                "grounding_source": "explicit_read_output",
                                "search_range": dict(absolute_range),
                            }
                        )
                start_line = _safe_int(absolute_range.get("start_line"))
                end_line = _safe_int(absolute_range.get("end_line"))
                if start_line is None or end_line is None:
                    raise ValueError("structured edit_file requires resolved absolute search_range")
                edit_result = replace_range(
                    workspace_root=workspace_root,
                    relative_path=relative_path,
                    start_line=start_line,
                    end_line=end_line,
                    replacement=replacement,
                    runtime=mutation_runtime,
                )
                grounding_validation["applied_structural_patch"] = ""
                grounding_validation["runtime_adapter"] = "replace_range"
            else:
                edit_result = edit_file(
                    workspace_root=workspace_root,
                    runtime=mutation_runtime,
                    execution_context=execution_context,
                    **_strip_edit_file_execution_args(args),
                )
            declared = dict((execution_context or {}).get("declared_validation_command") or {})
            if declared:
                grounding_validation["declared_validation"] = {
                    "status": "pending",
                    "mode": "declared_validation_command",
                    "command_text": str(declared.get("command_text") or ""),
                    "max_attempts": 2,
                    "enforced_at_tool": "cmd.run",
                }
            return {
                "tool": tool,
                "ok": True,
                "result": {
                    **edit_result,
                    "contract_validation": grounding_validation,
                },
            }
        except Exception as exc:
            structured = _structured_mutation_error(tool=tool, args=args, exc=exc)
            if structured is not None:
                return structured
            raise
    if tool == "replace_function":
        try:
            return {
                "tool": tool,
                "ok": True,
                "result": replace_function(
                    workspace_root=workspace_root,
                    runtime=mutation_runtime,
                    execution_context=execution_context,
                    **_strip_replace_function_execution_args(args),
                ),
            }
        except Exception as exc:
            structured = _structured_mutation_error(tool=tool, args=args, exc=exc)
            if structured is not None:
                return structured
            raise
    if tool == "insert_at_anchor":
        try:
            return {
                "tool": tool,
                "ok": True,
                "result": insert_at_anchor(
                    workspace_root=workspace_root,
                    runtime=mutation_runtime,
                    execution_context=execution_context,
                    **_strip_mutation_only_transport_args(args),
                ),
            }
        except Exception as exc:
            structured = _structured_mutation_error(tool=tool, args=args, exc=exc)
            if structured is not None:
                return structured
            raise
    if tool == "extend_python_signature":
        try:
            return {
                "tool": tool,
                "ok": True,
                "result": extend_python_signature(
                    workspace_root=workspace_root,
                    runtime=mutation_runtime,
                    **_strip_mutation_only_transport_args(args),
                ),
            }
        except Exception as exc:
            structured = _structured_mutation_error(tool=tool, args=args, exc=exc)
            if structured is not None:
                return structured
            raise
    if tool == "replace_range":
        return {
            "tool": tool,
            "ok": True,
            "result": replace_range(
                workspace_root=workspace_root,
                runtime=mutation_runtime,
                **_strip_mutation_only_transport_args(args),
            ),
        }
    if tool == "replace_in_function":
        return _removed_mutation_tool_error(tool=tool)
    if tool == "update_call_kwarg":
        return _removed_mutation_tool_error(tool=tool)
    if tool == "apply_patch":
        try:
            return {
                "tool": tool,
                "ok": True,
                "result": apply_patch(
                    workspace_root=workspace_root,
                    attempt_tracker=attempt_tracker,
                    runtime=mutation_runtime,
                    execution_context=execution_context,
                    **_strip_mutation_only_transport_args(args),
                ),
            }
        except Exception as exc:
            structured = _structured_mutation_error(tool=tool, args=args, exc=exc)
            if structured is not None:
                return structured
            raise
    if tool == "cmd.run":
        args = _pytest_aware_timeout(args)
        run_args = dict(args)
        run_args.pop("command_text", None)
        declared = dict((execution_context or {}).get("declared_validation_command") or {})
        command_text = str(declared.get("command_text") or "")
        if command_text and str(args.get("command_text") or "").strip() == command_text:
            ok, result, contract_validation = _run_declared_validation_command(
                workspace_root=workspace_root,
                command=[str(part) for part in list(run_args.get("command") or [])],
                command_text=command_text,
                timeout_seconds=args.get("timeout_seconds"),
                allow_prefixes=allow_prefixes,
                command_runtime=command_runtime,
                execution_context=execution_context,
            )
            result = dict(result or {})
            result["contract_validation"] = contract_validation
        else:

            def _policy_hook(event: str, payload: dict[str, Any]) -> None:
                mapped_reason = "COMMAND_ALLOWED" if event == "command_allowed" else "COMMAND_DENIED"
                _emit_execution_event(
                    execution_context=execution_context,
                    event_name="execution_command_policy",
                    phase="execution",
                    reason_code=mapped_reason,
                    classification="POLICY_FAIL" if event == "command_denied" else "success",
                    payload={
                        "command": [str(part) for part in list(run_args.get("command") or [])],
                        "policy_event": event,
                        **dict(payload or {}),
                    },
                )

            result = run_command(
                workspace_root=workspace_root,
                allow_prefixes=allow_prefixes,
                runtime=command_runtime,
                policy_hook=_policy_hook,
                **run_args,
            )
            returncode = result.get("returncode")
            returncode_ok = False
            try:
                returncode_ok = int(str(returncode)) == 0
            except Exception:
                returncode_ok = False
            ok = (not bool(result.get("timed_out"))) and returncode_ok
            _emit_execution_event(
                execution_context=execution_context,
                event_name="execution_command_result",
                phase="execution",
                reason_code="COMMAND_OK" if ok else "COMMAND_FAILED",
                classification="success" if ok else "CODE_FAIL",
                payload={
                    "command": [str(part) for part in list(result.get("command") or [])],
                    "exit_code": returncode if returncode is not None else None,
                    "timed_out": bool(result.get("timed_out")),
                },
            )
        return {
            "tool": tool,
            "ok": ok,
            "result": result,
        }
    if tool == "evidence.attach":
        evidence_ref = str(args.get("evidence_ref") or "").strip()
        if not evidence_ref:
            raise ValueError("evidence.attach requires args.evidence_ref")
        metadata = dict(args.get("metadata") or {})
        return {
            "tool": tool,
            "ok": True,
            "result": {
                "operation": "evidence.attach",
                "evidence_type": str(args.get("evidence_type") or "artifact"),
                "evidence_ref": evidence_ref,
                "metadata": metadata,
                "entry": {
                    "schema_version": "evidence_entry.current",
                    "captured_at_utc": datetime.now(UTC).isoformat(),
                    "artifact_refs": [evidence_ref],
                    "metadata": metadata,
                },
            },
        }
    raise ValueError(f"Unsupported tool in loop: {tool}")


PlannerFn = Callable[[list[dict[str, Any]], dict[str, Any]], dict[str, Any] | None]
PolicyGateFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None]
EscalationHookFn = Callable[[dict[str, Any]], None]
HeartbeatHookFn = Callable[[dict[str, Any]], None]


def _runtime_budget_exceeded(*, started_at: float, max_runtime_seconds: float) -> bool:
    return (monotonic() - started_at) > max_runtime_seconds


def _runtime_budget_error(*, index: int, tool: str | None, max_runtime_seconds: float) -> dict[str, Any]:
    return {
        "index": index,
        "tool": tool,
        "ok": False,
        "error": f"TOOL_LOOP_RUNTIME_BUDGET_EXCEEDED max_runtime_seconds={max_runtime_seconds}",
    }


def _patch_budget_error(*, index: int, tool: str | None, max_patch_attempts: int) -> dict[str, Any]:
    return {
        "index": index,
        "tool": tool,
        "ok": False,
        "error": f"PATCH_ATTEMPT_LIMIT_EXCEEDED max_patch_attempts={max_patch_attempts}",
    }


def _token_budget_error(*, index: int, tool: str | None, token_budget: int, budget_used: int) -> dict[str, Any]:
    return {
        "index": index,
        "tool": tool,
        "ok": False,
        "error": f"TOKEN_BUDGET_EXCEEDED budget={token_budget} used={budget_used} step_failed={index}",
        "result": {
            "code": "TOKEN_BUDGET_EXCEEDED",
            "budget": token_budget,
            "used": budget_used,
            "step_failed": index,
        },
    }


def _enforce_token_budget(
    *,
    transcript: list[dict[str, Any]],
    budget_used: dict[str, int],
    index: int,
    tool: str | None,
    token_budget: int | None,
) -> bool:
    if token_budget is None:
        return False
    used = int(budget_used.get("tokens", 0))
    if used <= int(token_budget):
        return False
    _append_transcript_entry(
        transcript=transcript,
        budget_used=budget_used,
        entry=_token_budget_error(
            index=index,
            tool=tool,
            token_budget=int(token_budget),
            budget_used=used,
        ),
    )
    return True


def _changed_python_files_from_transcript(transcript: list[dict[str, Any]]) -> list[str]:
    changed: list[str] = []
    for item in transcript:
        if not isinstance(item, dict):
            continue
        raw_result = item.get("result")
        result: dict[str, Any] = dict(raw_result) if isinstance(raw_result, dict) else {}
        for path_value in result.get("changed_files") or []:
            path_text = str(path_value or "").strip()
            if path_text.endswith(".py") and path_text not in changed:
                changed.append(path_text)
    return changed


def _symbol_mutation_prerequisite_error(
    *, index: int, tool: str, relative_path: str, symbol_name: str
) -> dict[str, Any]:
    return {
        "index": index,
        "tool": tool,
        "ok": False,
        "error": (
            "GROUNDING_CONFIRMATION_REQUIRED " f"tool={tool} relative_path={relative_path} symbol_name={symbol_name}"
        ),
    }


def _has_anchor_confirmation(*, transcript: list[dict[str, Any]], relative_path: str, symbol_name: str) -> bool:
    if not transcript:
        return False
    normalized_path = str(relative_path or "").replace("\\", "/")
    for item in reversed(transcript):
        if not isinstance(item, dict):
            continue
        if not bool(item.get("ok")):
            return False
        tool_name = str(item.get("tool") or "")
        raw_result = item.get("result")
        result: dict[str, Any] = dict(raw_result) if isinstance(raw_result, dict) else {}
        item_path = str(result.get("relative_path") or normalized_path).replace("\\", "/")
        item_symbol = str(
            result.get("qualified_name")
            or result.get("symbol_name")
            or result.get("function_name")
            or result.get("symbol")
            or ""
        ).strip()
        if tool_name == "confirm_anchor":
            return (
                str(result.get("reason_code") or "") == "MATCH"
                and _symbols_equivalent(item_symbol, symbol_name)
                and item_path == normalized_path
            )
        if tool_name in {"edit_symbol", "insert_after_symbol", "extend_python_signature"}:
            if item_path == normalized_path and _symbols_equivalent(item_symbol, symbol_name):
                continue
            return False
        if tool_name in {"read_file", "read_range"} and item_path == normalized_path:
            continue
        # Auto-injected py_compile steps (ROBQ-4) are synthetic cmd.run entries inserted
        # after each mutation tool. They do not break the grounding chain — the anchor
        # confirmed before the preceding mutation remains valid for a subsequent mutation
        # on the same file/symbol. Skip them so the traversal can reach the confirm_anchor.
        if tool_name == "cmd.run" and bool(item.get("auto_injected")):
            continue
        return False
    return False


def _latest_anchor_grounding(
    *,
    transcript: list[dict[str, Any]],
    relative_path: str,
    symbol_name: str,
) -> dict[str, Any] | None:
    if not transcript:
        return None
    normalized_path = str(relative_path or "").replace("\\", "/")
    for item in reversed(transcript):
        if not isinstance(item, dict) or not bool(item.get("ok")):
            continue
        if str(item.get("tool") or "") != "confirm_anchor":
            continue
        result = dict(item.get("result") or {})
        item_path = str(result.get("relative_path") or "").replace("\\", "/")
        item_symbol = str(result.get("qualified_name") or result.get("symbol_name") or "").strip()
        if item_path == normalized_path and _symbols_equivalent(item_symbol, symbol_name):
            return result
    return None


def _attach_grounding_expectations(
    *,
    workspace_root: str | Path,
    transcript: list[dict[str, Any]],
    step: dict[str, Any],
) -> dict[str, Any]:
    tool_name = str(step.get("tool") or "")
    if tool_name not in {
        "replace_in_function",
        "update_call_kwarg",
        "edit_symbol",
        "insert_after_symbol",
        "extend_python_signature",
    }:
        return step
    args = _normalize_tool_args(workspace_root=workspace_root, tool=tool_name, raw_args=dict(step.get("args") or {}))
    relative_path = str(args.get("relative_path") or "").replace("\\", "/")
    symbol_name = str(args.get("symbol") or args.get("function_name") or args.get("symbol_name") or "").strip()
    grounding = _latest_anchor_grounding(transcript=transcript, relative_path=relative_path, symbol_name=symbol_name)
    if grounding is None:
        return {"tool": tool_name, "args": args}
    if grounding.get("commit_sha") and not args.get("expected_base_commit_sha"):
        args["expected_base_commit_sha"] = grounding.get("commit_sha")
    if grounding.get("file_blob_sha") and not args.get("expected_file_blob_sha"):
        args["expected_file_blob_sha"] = grounding.get("file_blob_sha")
    if grounding.get("symbol_source_hash") and not args.get("expected_symbol_source_hash"):
        args["expected_symbol_source_hash"] = grounding.get("symbol_source_hash")
    return {"tool": tool_name, "args": args}


def _refresh_expected_grounding_from_preflight(args: dict[str, Any], preflight: dict[str, Any]) -> dict[str, Any]:
    refreshed = dict(args)
    grounded_symbol = dict(preflight.get("grounded_symbol") or {})
    if grounded_symbol.get("commit_sha"):
        refreshed["expected_base_commit_sha"] = grounded_symbol.get("commit_sha")
    if grounded_symbol.get("file_blob_sha"):
        refreshed["expected_file_blob_sha"] = grounded_symbol.get("file_blob_sha")
    if grounded_symbol.get("symbol_source_hash"):
        refreshed["expected_symbol_source_hash"] = grounded_symbol.get("symbol_source_hash")
    return refreshed


def _clear_expected_grounding_for_preflight(args: dict[str, Any]) -> dict[str, Any]:
    preflight_args = dict(args)
    preflight_args.pop("expected_base_commit_sha", None)
    preflight_args.pop("expected_file_blob_sha", None)
    preflight_args.pop("expected_symbol_source_hash", None)
    return preflight_args


def _refresh_extend_signature_grounding(
    *,
    workspace_root: str | Path,
    step: dict[str, Any],
    mutation_runtime: dict[str, Any] | None,
) -> dict[str, Any]:
    tool_name = str(step.get("tool") or "").strip()
    if tool_name != "extend_python_signature":
        return step
    raw_args = step.get("args")
    args = dict(raw_args) if isinstance(raw_args, dict) else {}
    preflight_args = _clear_expected_grounding_for_preflight(args)
    preflight = extend_python_signature(
        workspace_root=workspace_root,
        runtime=mutation_runtime,
        dry_run=True,
        **_strip_mutation_only_transport_args(preflight_args),
    )
    refreshed = _refresh_expected_grounding_from_preflight(args, preflight)
    return {"tool": tool_name, "args": refreshed}


def _strip_mutation_only_transport_args(args: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(args)
    cleaned.pop("resolved_path", None)
    cleaned.pop("read_source", None)
    cleaned.pop("grounding_source", None)
    cleaned.pop("read_step_idx", None)
    for key in tuple(cleaned.keys()):
        if str(key).startswith("__aix_"):
            cleaned.pop(key, None)
    return cleaned


def _strip_edit_file_execution_args(args: dict[str, Any]) -> dict[str, Any]:
    cleaned = _strip_mutation_only_transport_args(args)
    cleaned.pop("search", None)
    cleaned.pop("replace", None)
    cleaned.pop("replace_text", None)
    cleaned.pop("replacement_text", None)
    cleaned.pop("search_range", None)
    cleaned.pop("edit_range_within_read", None)
    return cleaned


def _strip_replace_function_execution_args(args: dict[str, Any]) -> dict[str, Any]:
    cleaned = _strip_mutation_only_transport_args(args)
    cleaned.pop("function_name", None)
    cleaned.pop("edit_intent", None)
    cleaned.pop("preserve_constraints", None)
    cleaned.pop("line_targets", None)
    cleaned.pop("rewrite_generation", None)
    return cleaned


def _runtime_uses_container(runtime: dict[str, Any] | None) -> bool:
    runtime_payload = dict(runtime or {})
    runtime_mode = str(runtime_payload.get("mode") or "").strip().lower()
    return bool(runtime_payload.get("container_required")) or runtime_mode == "docker"


def _symbols_equivalent(left: str, right: str) -> bool:
    normalized_left = str(left or "").strip()
    normalized_right = str(right or "").strip()
    if not normalized_left or not normalized_right:
        return False
    if normalized_left == normalized_right:
        return True
    if normalized_left.count(".") >= 2 and normalized_left.rsplit(".", 1)[-1] == normalized_right:
        return True
    if normalized_right.count(".") >= 2 and normalized_right.rsplit(".", 1)[-1] == normalized_left:
        return True
    if normalized_left.endswith(f".{normalized_right}"):
        return True
    if normalized_right.endswith(f".{normalized_left}"):
        return True
    return False


def _syntax_failure_details(*, workspace_root: str | Path, relative_path: str) -> dict[str, Any] | None:
    candidate = (Path(workspace_root) / relative_path).resolve()
    try:
        source = candidate.read_text(encoding="utf-8")
        ast.parse(source, filename=str(candidate))
    except Exception as exc:
        return {
            "relative_path": relative_path,
            "message": str(exc),
            "path": str(candidate),
        }
    return None


def _pre_pytest_syntax_error(
    *, index: int, workspace_root: str | Path, transcript: list[dict[str, Any]]
) -> dict[str, Any] | None:
    changed_python_files = _changed_python_files_from_transcript(transcript)
    if not changed_python_files:
        return None
    failures = []
    for relative_path in changed_python_files:
        failure = _syntax_failure_details(workspace_root=workspace_root, relative_path=relative_path)
        if failure is not None:
            failures.append(failure)
    if not failures:
        return None
    return {
        "index": index,
        "tool": "validate_syntax",
        "ok": False,
        "error": "CANDIDATE_SYNTAX_INVALID pre-pytest syntax validation failed",
        "result": {
            "operation": "validate_syntax",
            "failures": failures,
            "changed_python_files": changed_python_files,
        },
    }


def _invalid_plan_error(*, index: int, step: Any) -> dict[str, Any]:
    return {
        "index": index,
        "tool": "planner",
        "ok": False,
        "error": f"TOOL_LOOP_PLANNER_STEP_INVALID step={step!r}",
    }


def _policy_denied_error(*, index: int, tool: str | None, code: str, message: str) -> dict[str, Any]:
    return {
        "index": index,
        "tool": tool,
        "ok": False,
        "error": f"TOOL_LOOP_POLICY_DENIED code={code} message={message}",
    }


def _policy_escalation_channel_denied_error(
    *,
    index: int,
    tool: str | None,
    channel: str,
    approved_channels: set[str],
) -> dict[str, Any]:
    return {
        "index": index,
        "tool": tool,
        "ok": False,
        "error": (
            "POLICY_ESCALATION_CHANNEL_DENIED " f"channel={channel!r} approved_channels={sorted(approved_channels)}"
        ),
    }


def _policy_escalation_route_failed_error(*, index: int, tool: str | None, error: str) -> dict[str, Any]:
    return {
        "index": index,
        "tool": tool,
        "ok": False,
        "error": f"POLICY_ESCALATION_ROUTE_FAILED error={error}",
    }


def _evaluate_policy_gate(
    *,
    index: int,
    step: dict[str, Any],
    transcript: list[dict[str, Any]],
    tracker: dict[str, int],
    mode: str,
    max_patch_attempts: int,
    policy_gate: PolicyGateFn | None,
    escalation_hook: EscalationHookFn | None,
    approved_escalation_channels: set[str],
) -> dict[str, Any] | None:
    if policy_gate is None:
        return None
    tool = str(step.get("tool") or "")
    try:
        decision = policy_gate(
            step,
            {
                "index": index,
                "mode": mode,
                "transcript": transcript,
                "patch_attempts": int(tracker.get("patch_attempts", 0)),
                "max_patch_attempts": max_patch_attempts,
            },
        )
    except Exception as exc:
        return _policy_denied_error(index=index, tool=tool, code="policy_gate_error", message=str(exc))

    payload = dict(decision or {})
    allowed = bool(payload.get("allow", True))
    if allowed:
        return None

    code = str(payload.get("code") or "policy_denied")
    message = str(payload.get("message") or "step denied by iteration policy gate")
    if bool(payload.get("escalate", False)):
        channel = str(payload.get("escalation_channel") or "").strip()
        if channel not in approved_escalation_channels:
            return _policy_escalation_channel_denied_error(
                index=index,
                tool=tool,
                channel=channel,
                approved_channels=approved_escalation_channels,
            )
        if escalation_hook is not None:
            try:
                escalation_hook(
                    {
                        "index": index,
                        "mode": mode,
                        "step": step,
                        "code": code,
                        "message": message,
                        "channel": channel,
                        "details": payload.get("details") or {},
                    }
                )
            except Exception as exc:
                return _policy_escalation_route_failed_error(index=index, tool=tool, error=str(exc))
    return _policy_denied_error(index=index, tool=tool, code=code, message=message)


def execute_tool_loop(
    *,
    workspace_root: str | Path,
    steps: list[dict[str, Any]],
    allow_prefixes: list[list[str]],
    max_runtime_seconds: float = 120.0,
    max_patch_attempts: int = 10,
    token_budget: int | None = None,
    policy_gate: PolicyGateFn | None = None,
    escalation_hook: EscalationHookFn | None = None,
    heartbeat_hook: HeartbeatHookFn | None = None,
    approved_escalation_channels: set[str] | None = None,
    command_runtime: dict[str, Any] | None = None,
    mutation_runtime: dict[str, Any] | None = None,
    execution_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if execution_context is None:
        execution_context = {}
    _remember_declared_validation_command(execution_context=execution_context, steps=steps)
    steps = _ensure_terminal_declared_validation_step(steps=steps, execution_context=execution_context)
    transcript: list[dict[str, Any]] = []
    tracker = {"patch_attempts": 0}
    budget_used = {"tokens": 0}
    approved_channels = set(approved_escalation_channels or {"human_review", "model_gateway"})
    started_at = monotonic()
    first_tool_heartbeat_emitted = False
    for index, step in enumerate(steps):
        _remember_planner_anchor_requirement(execution_context=execution_context, step=step)
        _remember_planner_grounded_buffer(execution_context=execution_context, step=step)
        if bool(step.get("synthetic")) and str(step.get("tool") or "").strip() in {"read_file", "read_range"}:
            continue
        if _runtime_budget_exceeded(started_at=started_at, max_runtime_seconds=max_runtime_seconds):
            _append_transcript_entry(
                transcript=transcript,
                budget_used=budget_used,
                entry=_runtime_budget_error(
                    index=index, tool=str(step.get("tool") or ""), max_runtime_seconds=max_runtime_seconds
                ),
            )
            break
        if str(step.get("tool") or "") == "apply_patch" and int(tracker.get("patch_attempts", 0)) >= max_patch_attempts:
            _append_transcript_entry(
                transcript=transcript,
                budget_used=budget_used,
                entry=_patch_budget_error(index=index, tool="apply_patch", max_patch_attempts=max_patch_attempts),
            )
            break
        policy_error = _evaluate_policy_gate(
            index=index,
            step=step,
            transcript=transcript,
            tracker=tracker,
            mode="steps",
            max_patch_attempts=max_patch_attempts,
            policy_gate=policy_gate,
            escalation_hook=escalation_hook,
            approved_escalation_channels=approved_channels,
        )
        if policy_error is not None:
            _append_transcript_entry(transcript=transcript, entry=policy_error, budget_used=budget_used)
            break
        tool_name = str(step.get("tool") or "")
        if tool_name in {"edit_symbol", "insert_after_symbol", "extend_python_signature"}:
            step_args = _normalize_tool_args(
                workspace_root=workspace_root, tool=tool_name, raw_args=dict(step.get("args") or {})
            )
            relative_path = str(step_args.get("relative_path") or "").replace("\\", "/")
            symbol_name = str(
                step_args.get("symbol") or step_args.get("function_name") or step_args.get("symbol_name") or ""
            ).strip()
            if not _has_anchor_confirmation(
                transcript=transcript,
                relative_path=relative_path,
                symbol_name=symbol_name,
            ):
                _append_transcript_entry(
                    transcript=transcript,
                    budget_used=budget_used,
                    entry=_symbol_mutation_prerequisite_error(
                        index=index,
                        tool=tool_name,
                        relative_path=relative_path,
                        symbol_name=symbol_name,
                    ),
                )
                break
            step = _attach_grounding_expectations(workspace_root=workspace_root, transcript=transcript, step=step)
            step = _refresh_extend_signature_grounding(
                workspace_root=workspace_root,
                step=step,
                mutation_runtime=mutation_runtime,
            )
            tool_name = str(step.get("tool") or tool_name)
        if str(step.get("tool") or "") == "cmd.run":
            args = _normalize_tool_args(
                workspace_root=workspace_root, tool="cmd.run", raw_args=dict(step.get("args") or {})
            )
            args = _pytest_aware_timeout(args)
            command = args.get("command")
            if _is_pytest_command(command if isinstance(command, list) else []):
                validation_command = [str(part) for part in command] if isinstance(command, list) else ["pytest"]
                syntax_error = _pre_pytest_syntax_error(
                    index=index,
                    workspace_root=workspace_root,
                    transcript=transcript,
                )
                if syntax_error is not None:
                    syntax_error["diagnostics"] = {"failed_at_utc": datetime.now(UTC).isoformat()}
                    _append_transcript_entry(transcript=transcript, entry=syntax_error, budget_used=budget_used)
                    break
                preflight_result = _run_validation_preflight(
                    workspace_root=workspace_root,
                    transcript=transcript,
                    allow_prefixes=allow_prefixes,
                    command_runtime=command_runtime,
                    index=index,
                    validation_command=validation_command,
                )
                if preflight_result is not None:
                    _append_transcript_entry(transcript=transcript, entry=preflight_result, budget_used=budget_used)
                    if preflight_result.get("ok") is False:
                        break
        # — content static analysis gate (ROBQ-3) —
        if tool_name in CONTENT_MUTATION_TOOLS:
            _content_args = _normalize_tool_args(
                workspace_root=workspace_root, tool=tool_name, raw_args=dict(step.get("args") or {})
            )
            _content_err = validate_step_content(
                tool=tool_name,
                args=_content_args,
                workspace_root=workspace_root,
            )
            if _content_err is not None:
                _append_transcript_entry(
                    transcript=transcript,
                    budget_used=budget_used,
                    entry={
                        "index": index,
                        "tool": tool_name,
                        "ok": False,
                        "error": _content_err,
                        "result": {
                            "code": "content_validation_failed",
                            "relative_path": str(_content_args.get("relative_path") or ""),
                        },
                    },
                )
                break
        try:
            step_started_at = datetime.now(UTC).isoformat()
            step_started_perf = monotonic()
            if heartbeat_hook is not None and not first_tool_heartbeat_emitted:
                try:
                    heartbeat_hook(
                        {
                            "stage": "first_tool_execution",
                            "summary": f"starting first tool step via {tool_name or 'unknown'}",
                            "details": {
                                "tool": tool_name or None,
                                "index": index,
                            },
                            "timestamp_utc": step_started_at,
                        }
                    )
                except Exception:
                    pass
                first_tool_heartbeat_emitted = True
            output = execute_tool_step(
                workspace_root=workspace_root,
                step=_with_patch_budget(step=step, max_patch_attempts=max_patch_attempts),
                allow_prefixes=allow_prefixes,
                attempt_tracker=tracker,
                command_runtime=command_runtime,
                mutation_runtime=mutation_runtime,
                execution_context=execution_context,
            )
            output["index"] = index
            output["diagnostics"] = {
                "started_at_utc": step_started_at,
                "elapsed_ms": int((monotonic() - step_started_perf) * 1000),
            }
            _append_transcript_entry(transcript=transcript, entry=output, budget_used=budget_used)
            if _runtime_budget_exceeded(started_at=started_at, max_runtime_seconds=max_runtime_seconds):
                _append_transcript_entry(
                    transcript=transcript,
                    budget_used=budget_used,
                    entry=_runtime_budget_error(index=index, tool=tool_name, max_runtime_seconds=max_runtime_seconds),
                )
                break
            if _enforce_token_budget(
                transcript=transcript,
                budget_used=budget_used,
                index=index,
                tool=tool_name,
                token_budget=token_budget,
            ):
                break
            if output.get("ok") is False and not bool(output.get("recoverable")):
                break
            # — py_compile auto-injection (ROBQ-4) —
            if output.get("ok") and tool_name in _PY_COMPILE_HOOK_TOOLS:
                _py_args = _normalize_tool_args(
                    workspace_root=workspace_root, tool=tool_name, raw_args=dict(step.get("args") or {})
                )
                _rel = str(_py_args.get("relative_path") or "")
                if _rel.endswith(".py"):
                    _compile_entry = _auto_py_compile(workspace_root=workspace_root, relative_path=_rel, index=index)
                    _append_transcript_entry(transcript=transcript, entry=_compile_entry, budget_used=budget_used)
                    if _enforce_token_budget(
                        transcript=transcript,
                        budget_used=budget_used,
                        index=index,
                        tool="cmd.run",
                        token_budget=token_budget,
                    ):
                        break
                    if _compile_entry.get("ok") is False:
                        break
        except Exception as exc:
            _append_transcript_entry(
                transcript=transcript,
                budget_used=budget_used,
                entry={
                    "index": index,
                    "tool": step.get("tool"),
                    "ok": False,
                    "recoverable": False,
                    "error": str(exc),
                    "result": {
                        "code": "tool_exception_unhandled",
                        "exception_type": type(exc).__name__,
                        "message": str(exc),
                    },
                    "diagnostics": {
                        "failed_at_utc": datetime.now(UTC).isoformat(),
                        "exception_type": type(exc).__name__,
                    },
                },
            )
            break
    return transcript


def _with_patch_budget(*, step: dict[str, Any], max_patch_attempts: int) -> dict[str, Any]:
    if str(step.get("tool") or "") != "apply_patch":
        return step
    args = dict(step.get("args") or {})
    args.setdefault("max_attempts", max_patch_attempts)
    return {"tool": "apply_patch", "args": args}


def _validation_bootstrap_command(*, workspace_root: str | Path) -> list[str] | None:
    pyproject_path = Path(workspace_root) / "pyproject.toml"
    if not pyproject_path.exists():
        return None
    try:
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except Exception:
        return ["python", "-m", "pip", "install", "-e", "."]
    optional = payload.get("project", {}).get("optional-dependencies", {})
    if isinstance(optional, dict) and "dev" in optional:
        return ["python", "-m", "pip", "install", "-e", ".[dev]"]
    return ["python", "-m", "pip", "install", "-e", "."]


def _is_pytest_command(command: list[str]) -> bool:
    normalized = [str(item).strip() for item in list(command or []) if str(item).strip()]
    if not normalized:
        return False
    if normalized[0].lower() == "pytest":
        return True
    return len(normalized) >= 3 and normalized[0].lower().startswith("python") and normalized[1:3] == ["-m", "pytest"]


def _pytest_collect_only_command(command: list[str]) -> list[str]:
    normalized = [str(item) for item in command if str(item).strip()]
    if not normalized:
        return ["pytest", "--collect-only"]
    if normalized[0].lower() == "pytest":
        extra_args = [arg for arg in normalized[1:] if arg != "--collect-only"]
        return ["pytest", "--collect-only", *extra_args]
    if _is_pytest_command(normalized):
        extra_args = [arg for arg in normalized[3:] if arg != "--collect-only"]
        return [normalized[0], "-m", "pytest", "--collect-only", *extra_args]
    extra_args = [arg for arg in normalized[1:] if arg != "--collect-only"]
    return ["pytest", "--collect-only", *extra_args]


def _has_successful_validation_preflight(*, transcript: list[dict[str, Any]], command: list[str]) -> bool:
    expected_command = _pytest_collect_only_command(command)
    for item in transcript:
        if str(item.get("tool") or "") != "validation.preflight" or not bool(item.get("ok")):
            continue
        result = dict(item.get("result") or {})
        collect_only = dict(result.get("collect_only") or {})
        actual_command = [str(part) for part in list(collect_only.get("command") or [])]
        if actual_command == expected_command:
            return True
    return False


def _run_validation_preflight(
    *,
    workspace_root: str | Path,
    transcript: list[dict[str, Any]],
    allow_prefixes: list[list[str]],
    command_runtime: dict[str, Any] | None,
    index: int,
    validation_command: list[str],
) -> dict[str, Any] | None:
    if _has_successful_validation_preflight(transcript=transcript, command=validation_command):
        return None
    started_at = datetime.now(UTC).isoformat()
    started_perf = monotonic()
    bootstrap_result: dict[str, Any] | None = None
    bootstrap_command = _validation_bootstrap_command(workspace_root=workspace_root)
    if bootstrap_command:
        bootstrap_result = run_command(
            workspace_root=workspace_root,
            allow_prefixes=allow_prefixes,
            runtime=command_runtime,
            command=bootstrap_command,
            timeout_seconds=180.0,
        )
        assert bootstrap_result is not None
        bootstrap_ok = (not bool(bootstrap_result.get("timed_out"))) and int(
            str(bootstrap_result.get("returncode", 1))
        ) == 0
    pythonpath_probe = run_command(
        workspace_root=workspace_root,
        allow_prefixes=allow_prefixes,
        runtime=command_runtime,
        command=["python", "-c", "import os; print(os.getenv('PYTHONPATH', ''))"],
        timeout_seconds=30.0,
    )
    collect_only_command = _pytest_collect_only_command(validation_command)
    collect_only_result = run_command(
        workspace_root=workspace_root,
        allow_prefixes=allow_prefixes,
        runtime=command_runtime,
        command=collect_only_command,
        timeout_seconds=120.0,
    )
    ok = (not bool(collect_only_result.get("timed_out"))) and int(str(collect_only_result.get("returncode", 1))) == 0
    bootstrap_failed = bool(bootstrap_command) and not bootstrap_ok
    diagnostics = {
        "started_at_utc": started_at,
        "elapsed_ms": int((monotonic() - started_perf) * 1000),
    }
    if bootstrap_failed:
        diagnostics["bootstrap_warning"] = "VALIDATION_BOOTSTRAP_FAILED_CONTINUED"
    return {
        "index": index,
        "tool": "validation.preflight",
        "ok": ok,
        "error": None if ok else "PRECHECK_ENVIRONMENT_FAILED validation collect-only probe failed",
        "result": {
            "phase": "ready" if ok else "collect_only",
            "bootstrap": bootstrap_result,
            "pythonpath_probe": pythonpath_probe,
            "collect_only": collect_only_result,
        },
        "diagnostics": diagnostics,
    }


def execute_react_tool_loop(
    *,
    workspace_root: str | Path,
    planner: PlannerFn,
    allow_prefixes: list[list[str]],
    max_iterations: int = 16,
    max_runtime_seconds: float = 120.0,
    max_patch_attempts: int = 10,
    token_budget: int | None = None,
    max_feedback_rounds: int = 2,
    planner_context: dict[str, Any] | None = None,
    policy_gate: PolicyGateFn | None = None,
    escalation_hook: EscalationHookFn | None = None,
    approved_escalation_channels: set[str] | None = None,
    command_runtime: dict[str, Any] | None = None,
    mutation_runtime: dict[str, Any] | None = None,
    execution_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    transcript: list[dict[str, Any]] = []
    tracker = {"patch_attempts": 0}
    budget_used = {"tokens": 0}
    approved_channels = set(approved_escalation_channels or {"human_review", "model_gateway"})
    started_at = monotonic()
    extra_context = dict(planner_context or {})
    if execution_context is None:
        execution_context = {}
    elif not isinstance(execution_context, dict):
        execution_context = dict(execution_context)
    execution_context["planner_context"] = dict(extra_context)
    feedback_messages: list[str] = list(extra_context.get("planner_feedback_messages") or [])
    feedback_rounds_used = int(extra_context.get("feedback_rounds_used", 0) or 0)

    for index in range(max_iterations):
        if _runtime_budget_exceeded(started_at=started_at, max_runtime_seconds=max_runtime_seconds):
            _append_transcript_entry(
                transcript=transcript,
                budget_used=budget_used,
                entry=_runtime_budget_error(index=index, tool="planner", max_runtime_seconds=max_runtime_seconds),
            )
            break

        decision = planner(
            transcript,
            {
                "iteration_index": index,
                "max_iterations": max_iterations,
                "elapsed_seconds": monotonic() - started_at,
                "remaining_iterations": max_iterations - index,
                "patch_attempts": int(tracker.get("patch_attempts", 0)),
                "max_patch_attempts": max_patch_attempts,
                "feedback_rounds_used": feedback_rounds_used,
                "max_feedback_rounds": max_feedback_rounds,
                "planner_feedback_messages": list(feedback_messages),
                **extra_context,
            },
        )
        if decision is None:
            return transcript
        if not isinstance(decision, dict) or not str(decision.get("tool") or "").strip():
            _append_transcript_entry(
                transcript=transcript,
                entry=_invalid_plan_error(index=index, step=decision),
                budget_used=budget_used,
            )
            break
        if (
            str(decision.get("tool") or "") == "apply_patch"
            and int(tracker.get("patch_attempts", 0)) >= max_patch_attempts
        ):
            _append_transcript_entry(
                transcript=transcript,
                budget_used=budget_used,
                entry=_patch_budget_error(index=index, tool="apply_patch", max_patch_attempts=max_patch_attempts),
            )
            break
        policy_error = _evaluate_policy_gate(
            index=index,
            step=decision,
            transcript=transcript,
            tracker=tracker,
            mode="react",
            max_patch_attempts=max_patch_attempts,
            policy_gate=policy_gate,
            escalation_hook=escalation_hook,
            approved_escalation_channels=approved_channels,
        )
        if policy_error is not None:
            _append_transcript_entry(transcript=transcript, entry=policy_error, budget_used=budget_used)
            break
        tool_name = str(decision.get("tool") or "")
        if tool_name in {"edit_symbol", "insert_after_symbol", "extend_python_signature"}:
            step_args = _normalize_tool_args(
                workspace_root=workspace_root,
                tool=tool_name,
                raw_args=dict(decision.get("args") or {}),
            )
            relative_path = str(step_args.get("relative_path") or "").replace("\\", "/")
            symbol_name = str(
                step_args.get("symbol") or step_args.get("function_name") or step_args.get("symbol_name") or ""
            ).strip()
            if not _has_anchor_confirmation(
                transcript=transcript,
                relative_path=relative_path,
                symbol_name=symbol_name,
            ):
                _append_transcript_entry(
                    transcript=transcript,
                    budget_used=budget_used,
                    entry=_symbol_mutation_prerequisite_error(
                        index=index,
                        tool=tool_name,
                        relative_path=relative_path,
                        symbol_name=symbol_name,
                    ),
                )
                break
        if str(decision.get("tool") or "") == "cmd.run":
            args = _normalize_tool_args(
                workspace_root=workspace_root, tool="cmd.run", raw_args=dict(decision.get("args") or {})
            )
            args = _pytest_aware_timeout(args)
            command = args.get("command")
            if _is_pytest_command(command if isinstance(command, list) else []):
                validation_command = [str(part) for part in command] if isinstance(command, list) else ["pytest"]
                syntax_error = _pre_pytest_syntax_error(
                    index=index,
                    workspace_root=workspace_root,
                    transcript=transcript,
                )
                if syntax_error is not None:
                    syntax_error["diagnostics"] = {"failed_at_utc": datetime.now(UTC).isoformat()}
                    _append_transcript_entry(transcript=transcript, entry=syntax_error, budget_used=budget_used)
                    break
                preflight_result = _run_validation_preflight(
                    workspace_root=workspace_root,
                    transcript=transcript,
                    allow_prefixes=allow_prefixes,
                    command_runtime=command_runtime,
                    index=index,
                    validation_command=validation_command,
                )
                if preflight_result is not None:
                    _append_transcript_entry(transcript=transcript, entry=preflight_result, budget_used=budget_used)
                    if preflight_result.get("ok") is False:
                        break

        try:
            step_started_at = datetime.now(UTC).isoformat()
            step_started_perf = monotonic()
            output = execute_tool_step(
                workspace_root=workspace_root,
                step=_with_patch_budget(step=decision, max_patch_attempts=max_patch_attempts),
                allow_prefixes=allow_prefixes,
                attempt_tracker=tracker,
                command_runtime=command_runtime,
                mutation_runtime=mutation_runtime,
                execution_context=execution_context,
            )
            output["index"] = index
            output["diagnostics"] = {
                "started_at_utc": step_started_at,
                "elapsed_ms": int((monotonic() - step_started_perf) * 1000),
            }
            _append_transcript_entry(transcript=transcript, entry=output, budget_used=budget_used)
            if tool_name in {"failure_analysis", "report_failure"}:
                break
            if _runtime_budget_exceeded(started_at=started_at, max_runtime_seconds=max_runtime_seconds):
                _append_transcript_entry(
                    transcript=transcript,
                    budget_used=budget_used,
                    entry=_runtime_budget_error(index=index, tool=tool_name, max_runtime_seconds=max_runtime_seconds),
                )
                break
            if _enforce_token_budget(
                transcript=transcript,
                budget_used=budget_used,
                index=index,
                tool=tool_name,
                token_budget=token_budget,
            ):
                break
            if output.get("ok") is False and not bool(output.get("recoverable")):
                cmd_feedback = _cmd_run_feedback_payload(output)
                if cmd_feedback is not None and feedback_rounds_used < max_feedback_rounds:
                    feedback_rounds_used += 1
                    feedback_messages.append(str(cmd_feedback.get("message") or ""))
                    extra_context["feedback_rounds_used"] = feedback_rounds_used
                    extra_context["planner_feedback_messages"] = list(feedback_messages)
                    extra_context["last_cmd_run_feedback"] = cmd_feedback
                    continue
                break
        except Exception as exc:
            _append_transcript_entry(
                transcript=transcript,
                budget_used=budget_used,
                entry={
                    "index": index,
                    "tool": decision.get("tool"),
                    "ok": False,
                    "recoverable": False,
                    "error": str(exc),
                    "result": {
                        "code": "tool_exception_unhandled",
                        "exception_type": type(exc).__name__,
                        "message": str(exc),
                    },
                    "diagnostics": {
                        "failed_at_utc": datetime.now(UTC).isoformat(),
                        "exception_type": type(exc).__name__,
                    },
                },
            )
            break
    else:
        _append_transcript_entry(
            transcript=transcript,
            budget_used=budget_used,
            entry={
                "index": max_iterations,
                "tool": "planner",
                "ok": False,
                "error": f"TOOL_LOOP_ITERATION_BUDGET_EXCEEDED max_iterations={max_iterations}",
            },
        )

    return transcript
