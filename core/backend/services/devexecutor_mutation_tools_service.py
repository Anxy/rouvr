from __future__ import annotations

import ast
import builtins
import difflib
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import libcst as cst

from backend.services.devexecutor_mutation_runtime_service import execute_mutation_in_runtime
from backend.services.devexecutor_mutation_tool_taxonomy_service import (
    ORCHESTRATION_FALLBACK_MUTATION_TOOLS,
    PRIMARY_MUTATION_LANE,
)
from backend.services.devexecutor_patch_engine_service import PatchAttemptBudget, apply_patch_with_outcome
from backend.services.devexecutor_python_symbol_service import (
    build_symbol_grounding_payload,
    resolve_python_symbol_range,
)
from backend.services.devexecutor_readonly_tools_service import WorkspaceSecurityError, _safe_resolve
from backend.services.devexecutor_search_replace_blocks_service import (
    SearchReplaceBlockError,
    apply_search_replace_blocks,
    find_anchor_matches,
    parse_search_replace_blocks,
)

try:
    from aix.contracts.ayrpack_current import SpineEntryKind
except ModuleNotFoundError:
    _AIX_SRC = Path(__file__).resolve().parents[3] / "src"
    if str(_AIX_SRC) not in sys.path:
        sys.path.insert(0, str(_AIX_SRC))
    from aix.contracts.ayrpack_current import SpineEntryKind

PolicyHook = Callable[[str, dict[str, Any]], None]
logger = logging.getLogger(__name__)
DEPRECATED_MUTATION_TOOL_FLAGS = {
    "edit_symbol": "AIX_ENABLE_EDIT_SYMBOL_FALLBACK",
    "replace_in_function": "AIX_ENABLE_REPLACE_IN_FUNCTION_FALLBACK",
    "update_call_kwarg": "AIX_ENABLE_UPDATE_CALL_KWARG_FALLBACK",
    "insert_after_symbol": "AIX_ENABLE_INSERT_AFTER_SYMBOL_FALLBACK",
    "insert_before_return": "AIX_ENABLE_INSERT_BEFORE_RETURN_FALLBACK",
}


class MutationDivergenceError(RuntimeError):
    def __init__(self, *, code: str, message: str, details: dict[str, Any]) -> None:
        super().__init__(f"{code} {message}")
        self.code = str(code or "").strip() or "BASE_DIVERGED"
        self.message = str(message or "").strip() or self.code
        self.details = dict(details or {})


def _emit(hook: PolicyHook | None, event: str, payload: dict[str, Any]) -> None:
    if hook is None:
        return
    hook(event, payload)


def _function_header_text(source: str) -> str:
    for line in str(source or "").splitlines():
        if line.strip():
            return line.rstrip()
    return ""


def _replacement_preview_lines(replacement: str, *, limit: int = 20) -> list[str]:
    return [str(line) for line in str(replacement or "").splitlines()[:limit]]


def _feature_flag_enabled(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "disabled"}


def orchestration_deprecated_mutation_tool_enabled(primitive: str) -> bool:
    feature_flag = DEPRECATED_MUTATION_TOOL_FLAGS.get(str(primitive or "").strip())
    if not feature_flag:
        return False
    return _feature_flag_enabled(feature_flag, default=False)


def orchestration_visible_deprecated_mutation_tools() -> set[str]:
    return {
        primitive
        for primitive in ORCHESTRATION_FALLBACK_MUTATION_TOOLS
        if orchestration_deprecated_mutation_tool_enabled(primitive)
    }


def _mutation_scope(*, relative_path: str | None = None, symbol: str | None = None) -> dict[str, Any]:
    scope: dict[str, Any] = {}
    if str(relative_path or "").strip():
        scope["relative_path"] = str(relative_path or "").replace("\\", "/").strip()
    if str(symbol or "").strip():
        scope["symbol"] = str(symbol or "").strip()
    return scope


def _record_mutation_fallback_decision(
    *,
    execution_context: dict[str, Any] | None,
    policy_hook: PolicyHook | None,
    primitive: str,
    reason: str,
    relative_path: str | None = None,
    symbol: str | None = None,
) -> None:
    scope = _mutation_scope(relative_path=relative_path, symbol=symbol)
    payload = {
        "primitive": primitive,
        "reason": reason,
        "scope": dict(scope),
        "primary_mutation_lane": PRIMARY_MUTATION_LANE,
    }
    _emit(policy_hook, "mutation_fallback_used", payload)
    _emit(policy_hook, "deprecated_mutation_tool_used", payload)
    if not isinstance(execution_context, dict):
        return
    packet = execution_context.get("ayrpack")
    if not isinstance(packet, dict):
        return
    raw_spine = packet.get("spine") or []
    spine = list(raw_spine)
    next_index = max((int(item.get("index", 0)) for item in spine), default=-1) + 1
    spine.append(
        {
            "entry_id": f"spine-{uuid4().hex}",
            "index": next_index,
            "kind": SpineEntryKind.DECISION,
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "phase": "mutation",
            "status": "fallback",
            "actor": {"kind": "system", "id": "devexecutor.mutation_tools.fallback"},
            "title": f"primitive={primitive} reason={reason}",
            "summary": "fallback edit primitive used",
            "evidence_ref": str(scope.get("relative_path") or ""),
            "details": {
                "primitive": primitive,
                "reason": reason,
                "scope": dict(scope),
                "primary_mutation_lane": PRIMARY_MUTATION_LANE,
            },
        }
    )
    packet.update({"spine": spine})
    latest = spine[-1]
    latest_kind = str(getattr(latest.get("kind"), "value", latest.get("kind")) or "").lower()
    if latest_kind != SpineEntryKind.DECISION.value:
        raise AssertionError("FALLBACK_DECISION_MISSING fallback primitive use must emit DECISION entry")
    if str(latest.get("summary") or "") != "fallback edit primitive used":
        raise AssertionError("FALLBACK_DECISION_INVALID fallback primitive use must emit canonical summary")
    execution_context["ayrpack"] = packet


def _assert_deprecated_mutation_tool_enabled(
    *,
    primitive: str,
    policy_hook: PolicyHook | None,
    execution_context: dict[str, Any] | None,
    relative_path: str | None = None,
    symbol: str | None = None,
) -> None:
    feature_flag = DEPRECATED_MUTATION_TOOL_FLAGS.get(primitive)
    enabled = True if not feature_flag else _feature_flag_enabled(feature_flag, default=True)
    if enabled:
        _record_mutation_fallback_decision(
            execution_context=execution_context,
            policy_hook=policy_hook,
            primitive=primitive,
            reason="deprecated_primitive_selected",
            relative_path=relative_path,
            symbol=symbol,
        )
        return
    _record_mutation_fallback_decision(
        execution_context=execution_context,
        policy_hook=policy_hook,
        primitive=primitive,
        reason="deprecated_primitive_disabled",
        relative_path=relative_path,
        symbol=symbol,
    )
    raise RuntimeError(
        f"DEPRECATED_MUTATION_TOOL_DISABLED primitive={primitive} feature_flag={feature_flag} "
        f"primary_lane={PRIMARY_MUTATION_LANE}"
    )


def _append_ayrpack_fallback_decision(
    *,
    execution_context: dict[str, Any] | None,
    policy_hook: PolicyHook | None,
    primitive: str,
    reason: str,
    relative_path: str | None = None,
    symbol: str | None = None,
) -> None:
    _record_mutation_fallback_decision(
        execution_context=execution_context,
        policy_hook=policy_hook,
        primitive=primitive,
        reason=reason,
        relative_path=relative_path,
        symbol=symbol,
    )


def _removed_mutation_tool_message(*, primitive: str) -> str:
    return f"REMOVED_MUTATION_TOOL primitive={primitive} primary_lane=edit_file"


def _raise_removed_mutation_tool(*, primitive: str) -> None:
    raise RuntimeError(_removed_mutation_tool_message(primitive=primitive))


def _compute_diff(before_lines: list[str], after_lines: list[str], path: str) -> dict:
    diff_lines = list(difflib.unified_diff(before_lines, after_lines, fromfile=path, tofile=path, lineterm=""))
    if len(diff_lines) > 50:
        return {"type": "unified", "diff_truncated": True, "diff_line_count": len(diff_lines)}
    return {"type": "unified", "diff_text": "\n".join(diff_lines), "diff_truncated": False}


def _grounded_symbol_payload(*, file_path: Path, symbol: Any) -> dict[str, Any]:
    return build_symbol_grounding_payload(file_path=file_path, symbol_range=symbol)


def _normalized_expected_hash(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _assert_symbol_grounding(
    *,
    file_path: Path,
    symbol: Any,
    expected_base_commit_sha: str | None = None,
    expected_file_blob_sha: str | None = None,
    expected_symbol_source_hash: str | None = None,
) -> dict[str, Any]:
    grounded_symbol = _grounded_symbol_payload(file_path=file_path, symbol=symbol)
    expected_commit = _normalized_expected_hash(expected_base_commit_sha)
    expected_file = _normalized_expected_hash(expected_file_blob_sha)
    expected_symbol = _normalized_expected_hash(expected_symbol_source_hash)
    current_commit = _normalized_expected_hash(grounded_symbol.get("commit_sha"))
    current_file = _normalized_expected_hash(grounded_symbol.get("file_blob_sha"))
    current_symbol = _normalized_expected_hash(grounded_symbol.get("symbol_source_hash"))

    if expected_symbol and current_symbol != expected_symbol:
        raise MutationDivergenceError(
            code="SYMBOL_DIVERGED",
            message="resolved symbol source changed after grounding",
            details={
                "file_path": str(file_path),
                "qualified_name": getattr(symbol, "qualified_name", None),
                "expected_symbol_source_hash": expected_symbol,
                "current_symbol_source_hash": current_symbol,
                "expected_file_blob_sha": expected_file,
                "current_file_blob_sha": current_file,
            },
        )
    if expected_file and current_file != expected_file:
        raise MutationDivergenceError(
            code="BASE_DIVERGED",
            message="target file changed after grounding",
            details={
                "file_path": str(file_path),
                "qualified_name": getattr(symbol, "qualified_name", None),
                "expected_file_blob_sha": expected_file,
                "current_file_blob_sha": current_file,
            },
        )
    if expected_commit and current_commit != expected_commit:
        raise MutationDivergenceError(
            code="BASE_DIVERGED",
            message="repository head changed after grounding",
            details={
                "file_path": str(file_path),
                "qualified_name": getattr(symbol, "qualified_name", None),
                "expected_base_commit_sha": expected_commit,
                "current_base_commit_sha": current_commit,
            },
        )
    return grounded_symbol


def write_file(
    *,
    workspace_root: str | Path,
    relative_path: str,
    content: str,
    policy_hook: PolicyHook | None = None,
    format_on_write: bool | dict[str, Any] = False,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_mode = str((runtime or {}).get("mode") or "").strip().lower()
    if runtime is not None and (bool((runtime or {}).get("container_required")) or runtime_mode == "docker"):
        _emit(
            policy_hook,
            "pre_write_file",
            {"relative_path": relative_path, "path": str(relative_path), "runtime": runtime},
        )
        payload = execute_mutation_in_runtime(
            workspace_root=workspace_root,
            tool="write_file",
            args={"relative_path": relative_path, "content": content, "format_on_write": format_on_write},
            runtime=runtime,
        )
        _emit(policy_hook, "post_write_file", payload)
        return payload
    target = _safe_resolve(workspace_root, relative_path)
    _emit(policy_hook, "pre_write_file", {"relative_path": relative_path, "path": str(target)})
    normalized_relative_path = relative_path.replace("\\", "/")
    content_bytes = content.encode("utf-8")
    old_bytes = target.read_bytes() if target.exists() else b""
    if target.exists() and old_bytes == content_bytes:
        payload = {
            "path": str(target),
            "relative_path": normalized_relative_path,
            "operation": "write_file",
            "bytes_before": len(old_bytes),
            "bytes_after": len(old_bytes),
            "changed_bytes_estimate": 0,
            "atomic_write": True,
            "changed_files": [],
            "formatter": None,
            "lines_written": content.count("\n") + 1,
            "content_hash": "sha256:" + hashlib.sha256(content_bytes).hexdigest()[:16],
            "code": "ALREADY_APPLIED",
            "applied": False,
            "skipped": True,
        }
        _emit(policy_hook, "post_write_file", payload)
        return payload
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
            newline="",
        ) as handle:
            handle.write(content)
            tmp_path = Path(handle.name)
        os.replace(str(tmp_path), str(target))
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

    format_result = _run_optional_formatters(target=target, enabled=format_on_write)
    new_bytes = target.read_bytes()
    changed_bytes = abs(len(new_bytes) - len(old_bytes))
    payload = {
        "path": str(target),
        "relative_path": normalized_relative_path,
        "operation": "write_file",
        "bytes_before": len(old_bytes),
        "bytes_after": len(new_bytes),
        "changed_bytes_estimate": changed_bytes,
        "atomic_write": True,
        "changed_files": [normalized_relative_path],
        "formatter": format_result,
        "lines_written": content.count("\n") + 1,
        "content_hash": "sha256:" + hashlib.sha256(content_bytes).hexdigest()[:16],
        "applied": True,
        "skipped": False,
    }
    _emit(policy_hook, "post_write_file", payload)
    return payload


def apply_patch(
    *,
    workspace_root: str | Path,
    patch_text: str,
    policy_hook: PolicyHook | None = None,
    attempt_tracker: dict[str, int] | None = None,
    max_attempts: int = 10,
    max_conflict_retries: int = 1,
    allow_replace_range_fallback: bool = False,
    fallback_replace: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_mode = str((runtime or {}).get("mode") or "").strip().lower()
    if runtime is not None and (bool((runtime or {}).get("container_required")) or runtime_mode == "docker"):
        _emit(
            policy_hook,
            "pre_apply_patch",
            {
                "patch_attempt": int((attempt_tracker or {}).get("patch_attempts", 0)) + 1,
                "patch_bytes": len(patch_text.encode("utf-8")),
                "runtime": runtime,
            },
        )
        payload = execute_mutation_in_runtime(
            workspace_root=workspace_root,
            tool="apply_patch",
            args={
                "patch_text": patch_text,
                "max_attempts": max_attempts,
                "max_conflict_retries": max_conflict_retries,
            },
            runtime=runtime,
        )
        if attempt_tracker is not None:
            attempt_tracker["patch_attempts"] = int(payload.get("patch_attempt") or 1)
        _emit(policy_hook, "post_apply_patch", payload)
        return payload
    tracker = attempt_tracker if attempt_tracker is not None else {}
    budget = PatchAttemptBudget(max_attempts=max_attempts, attempts_used=int(tracker.get("patch_attempts", 0)))

    _emit(
        policy_hook,
        "pre_apply_patch",
        {
            "patch_attempt": budget.attempts_used + 1,
            "patch_bytes": len(patch_text.encode("utf-8")),
        },
    )
    result = apply_patch_with_outcome(
        workspace_root=workspace_root,
        patch_text=patch_text,
        patch_budget=budget,
        max_conflict_retries=max_conflict_retries,
        policy_hook=policy_hook,
        execution_context=execution_context,
    )
    tracker["patch_attempts"] = budget.attempts_used
    if result.status != "applied":
        _emit(
            policy_hook,
            "patch_failed",
            {
                "patch_attempt": result.patch_attempt,
                "reason_code": result.reason_code,
                "stderr": result.stderr.strip(),
                "stdout": result.stdout.strip(),
                "policy_violations": result.policy_violations,
            },
        )
        if allow_replace_range_fallback and isinstance(fallback_replace, dict):
            relative_path = str(fallback_replace.get("relative_path") or "").strip()
            if not relative_path:
                raise RuntimeError("PATCH_APPLY_FAILED fallback requires relative_path")
            _append_ayrpack_fallback_decision(
                execution_context=execution_context,
                policy_hook=policy_hook,
                primitive="replace_range",
                reason=str(result.reason_code or "apply_patch_fallback"),
                relative_path=relative_path.replace("\\", "/"),
            )
            fallback_result = replace_range(
                workspace_root=workspace_root,
                relative_path=relative_path,
                start_line=int(fallback_replace.get("start_line") or 1),
                end_line=int(fallback_replace.get("end_line") or 1),
                replacement=str(fallback_replace.get("replacement") or ""),
                policy_hook=policy_hook,
            )
            fallback_result["fallback"] = {
                "from": "apply_patch",
                "reason_code": result.reason_code,
                "stderr": result.stderr.strip(),
                "stdout": result.stdout.strip(),
            }
            return fallback_result
        if result.reason_code == "attempt_budget_exceeded":
            raise RuntimeError(
                f"PATCH_ATTEMPT_LIMIT_EXCEEDED attempts={result.patch_attempt} max_attempts={max_attempts}"
            )
        raise RuntimeError(_patch_failure_message(prefix="PATCH_APPLY_FAILED", patch_result=result))

    payload = {
        "operation": "apply_patch",
        "patch_attempt": result.patch_attempt,
        "patch_bytes": result.patch_bytes,
        "patch_sha1": result.patch_sha1,
        "approx_diff_bytes": result.approx_diff_bytes,
        "changed_files": result.changed_files,
        "retry_count": result.retry_count,
        "retry_strategy": result.retry_strategy,
        "adapter": "git_apply",
        "adapter_chain": _adapter_chain(),
    }
    _emit(policy_hook, "post_apply_patch", payload)
    return payload


def replace_range(
    *,
    workspace_root: str | Path,
    relative_path: str,
    start_line: int,
    end_line: int,
    replacement: str,
    policy_hook: PolicyHook | None = None,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_mode = str((runtime or {}).get("mode") or "").strip().lower()
    if runtime is not None and (bool((runtime or {}).get("container_required")) or runtime_mode == "docker"):
        _emit(
            policy_hook,
            "pre_replace_range",
            {"relative_path": relative_path, "start_line": start_line, "end_line": end_line, "runtime": runtime},
        )
        payload = execute_mutation_in_runtime(
            workspace_root=workspace_root,
            tool="replace_range",
            args={
                "relative_path": relative_path,
                "start_line": start_line,
                "end_line": end_line,
                "replacement": replacement,
            },
            runtime=runtime,
        )
        _emit(policy_hook, "post_replace_range", payload)
        return payload
    if start_line < 1:
        raise ValueError("start_line must be >= 1")
    if end_line < start_line:
        raise ValueError("end_line must be >= start_line")

    target = _safe_resolve(workspace_root, relative_path)
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_file():
        raise IsADirectoryError(str(target))

    _emit(
        policy_hook,
        "pre_replace_range",
        {"relative_path": relative_path, "start_line": start_line, "end_line": end_line, "path": str(target)},
    )
    replacement_line_count = len(replacement.splitlines()) or (1 if replacement else 0)
    _replace_lines_in_file(target=target, start_line=start_line, end_line=end_line, replacement=replacement)

    payload = {
        "operation": "replace_range",
        "path": str(target),
        "relative_path": relative_path,
        "start_line": start_line,
        "end_line": end_line,
        "replacement_line_count": replacement_line_count,
        "changed_files": [relative_path.replace("\\", "/")],
        "atomic_write": True,
    }
    _emit(policy_hook, "post_replace_range", payload)
    return payload


def _build_file_patch(*, relative_path: str, before_text: str, after_text: str) -> str:
    normalized_relative_path = relative_path.replace("\\", "/")
    before_lines = str(before_text or "").splitlines(keepends=True)
    after_lines = str(after_text or "").splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{normalized_relative_path}",
            tofile=f"b/{normalized_relative_path}",
        )
    )


def _has_terminal_newline(text: str) -> bool:
    return not text or text.endswith(("\n", "\r"))


def _coerce_inserted_content(*, anchor_line: str, new_content: str, indent_style: str) -> str:
    content = str(new_content or "")
    if indent_style != "match_anchor":
        return content
    anchor_indent = anchor_line[: len(anchor_line) - len(anchor_line.lstrip(" \t"))]
    adjusted_lines: list[str] = []
    for line in content.splitlines(keepends=True):
        if line.strip() and not line[:1].isspace():
            adjusted_lines.append(f"{anchor_indent}{line}")
        else:
            adjusted_lines.append(line)
    return "".join(adjusted_lines)


def _patch_failure_message(*, prefix: str, patch_result: Any) -> str:
    details = patch_result.stderr.strip() or patch_result.stdout.strip()
    if str(getattr(patch_result, "reason_code", "") or "") == "pyright_precheck_failed":
        semantic_output = str(getattr(patch_result, "semantic_precheck_output", "") or "").strip()
        if semantic_output:
            details = semantic_output
    return f"{prefix} attempt={patch_result.patch_attempt} reason={patch_result.reason_code}: {details}"


def edit_file(
    *,
    workspace_root: str | Path,
    relative_path: str,
    search_replace_blocks: str,
    policy_hook: PolicyHook | None = None,
    runtime: dict[str, Any] | None = None,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_mode = str((runtime or {}).get("mode") or "").strip().lower()
    if runtime is not None and (bool((runtime or {}).get("container_required")) or runtime_mode == "docker"):
        _emit(
            policy_hook,
            "pre_edit_file",
            {"relative_path": relative_path, "runtime": runtime},
        )
        payload = execute_mutation_in_runtime(
            workspace_root=workspace_root,
            tool="edit_file",
            args={
                "relative_path": relative_path,
                "search_replace_blocks": search_replace_blocks,
            },
            runtime=runtime,
        )
        _emit(policy_hook, "post_edit_file", payload)
        return payload

    target = _safe_resolve(workspace_root, relative_path)
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_file():
        raise IsADirectoryError(str(target))

    blocks = parse_search_replace_blocks(search_replace_blocks)
    before_text = target.read_text(encoding="utf-8", errors="replace")
    try:
        after_text, block_results = apply_search_replace_blocks(original_text=before_text, blocks=blocks)
    except SearchReplaceBlockError as exc:
        raise ValueError(f"{exc.code}: {exc.message}") from exc

    normalized_relative_path = relative_path.replace("\\", "/")
    _emit(policy_hook, "pre_edit_file", {"relative_path": normalized_relative_path, "path": str(target)})
    if after_text == before_text:
        payload = {
            "operation": "edit_file",
            "path": str(target),
            "relative_path": normalized_relative_path,
            "block_count": len(blocks),
            "blocks": block_results,
            "changed_files": [],
            "applied": False,
            "skipped": True,
            "code": "ALREADY_APPLIED",
            "atomic_write": True,
        }
        _emit(policy_hook, "post_edit_file", payload)
        return payload
    if not (_has_terminal_newline(before_text) and _has_terminal_newline(after_text)):
        write_result = write_file(
            workspace_root=workspace_root,
            relative_path=normalized_relative_path,
            content=after_text,
            policy_hook=None,
            format_on_write=False,
            runtime=None,
        )
        _append_ayrpack_fallback_decision(
            execution_context=execution_context,
            policy_hook=policy_hook,
            primitive="write_file",
            reason="missing_terminal_newline",
            relative_path=normalized_relative_path,
        )
        payload = {
            "operation": "edit_file",
            "path": str(target),
            "relative_path": normalized_relative_path,
            "block_count": len(blocks),
            "blocks": block_results,
            "changed_files": list(write_result.get("changed_files") or [normalized_relative_path]),
            "applied": True,
            "skipped": False,
            "atomic_write": True,
            "adapter": "write_file_fallback",
            "adapter_chain": [*_adapter_chain(), {"name": "write_file_fallback", "available": True}],
            "fallback_reason": "missing_terminal_newline",
        }
        _emit(policy_hook, "post_edit_file", payload)
        return payload
    patch_text = _build_file_patch(
        relative_path=normalized_relative_path, before_text=before_text, after_text=after_text
    )
    patch_result = apply_patch_with_outcome(
        workspace_root=workspace_root,
        patch_text=patch_text,
        patch_budget=PatchAttemptBudget(max_attempts=10),
        max_conflict_retries=1,
        policy_hook=policy_hook,
        execution_context=execution_context,
    )
    if patch_result.status != "applied":
        raise RuntimeError(_patch_failure_message(prefix="EDIT_FILE_PATCH_APPLY_FAILED", patch_result=patch_result))
    payload = {
        "operation": "edit_file",
        "path": str(target),
        "relative_path": normalized_relative_path,
        "block_count": len(blocks),
        "blocks": block_results,
        "changed_files": [normalized_relative_path],
        "applied": True,
        "skipped": False,
        "atomic_write": True,
        "patch_attempt": patch_result.patch_attempt,
        "patch_bytes": patch_result.patch_bytes,
        "patch_sha1": patch_result.patch_sha1,
        "approx_diff_bytes": patch_result.approx_diff_bytes,
        "retry_count": patch_result.retry_count,
        "retry_strategy": patch_result.retry_strategy,
        "adapter": "git_apply",
        "adapter_chain": _adapter_chain(),
    }
    _emit(policy_hook, "post_edit_file", payload)
    return payload


def insert_at_anchor(
    *,
    workspace_root: str | Path,
    relative_path: str,
    anchor_text: str,
    new_content: str,
    position: str = "after",
    indent_style: str = "match_anchor",
    policy_hook: PolicyHook | None = None,
    runtime: dict[str, Any] | None = None,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_mode = str((runtime or {}).get("mode") or "").strip().lower()
    if runtime is not None and (bool((runtime or {}).get("container_required")) or runtime_mode == "docker"):
        _emit(policy_hook, "pre_insert_at_anchor", {"relative_path": relative_path, "runtime": runtime})
        payload = execute_mutation_in_runtime(
            workspace_root=workspace_root,
            tool="insert_at_anchor",
            args={
                "relative_path": relative_path,
                "anchor_text": anchor_text,
                "new_content": new_content,
                "position": position,
                "indent_style": indent_style,
            },
            runtime=runtime,
        )
        _emit(policy_hook, "post_insert_at_anchor", payload)
        return payload

    normalized_position = str(position or "after").strip().lower()
    if normalized_position not in {"before", "after"}:
        raise ValueError(f"INVALID_INSERT_POSITION position={position!r}")

    target = _safe_resolve(workspace_root, relative_path)
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_file():
        raise IsADirectoryError(str(target))

    before_text = target.read_text(encoding="utf-8", errors="replace")
    matches = find_anchor_matches(text=before_text, anchor_text=anchor_text)
    if not matches:
        raise ValueError("ANCHOR_NOT_FOUND: anchor_text was not matchable in the target file")
    if len(matches) > 1:
        raise ValueError("ANCHOR_AMBIGUOUS: anchor_text matched multiple locations in the target file")
    match = matches[0]
    anchor_line = anchor_text.splitlines()[0] if anchor_text.splitlines() else ""
    inserted = _coerce_inserted_content(anchor_line=anchor_line, new_content=new_content, indent_style=indent_style)
    insert_at = match.start if normalized_position == "before" else match.end
    after_text = before_text[:insert_at] + inserted + before_text[insert_at:]

    normalized_relative_path = relative_path.replace("\\", "/")
    _emit(policy_hook, "pre_insert_at_anchor", {"relative_path": normalized_relative_path, "path": str(target)})
    if after_text == before_text:
        payload = {
            "operation": "insert_at_anchor",
            "path": str(target),
            "relative_path": normalized_relative_path,
            "changed_files": [],
            "applied": False,
            "skipped": True,
            "code": "ALREADY_APPLIED",
            "atomic_write": True,
            "position": normalized_position,
            "indent_style": indent_style,
        }
        _emit(policy_hook, "post_insert_at_anchor", payload)
        return payload
    if not (_has_terminal_newline(before_text) and _has_terminal_newline(after_text)):
        write_result = write_file(
            workspace_root=workspace_root,
            relative_path=normalized_relative_path,
            content=after_text,
            policy_hook=None,
            format_on_write=False,
            runtime=None,
        )
        _append_ayrpack_fallback_decision(
            execution_context=execution_context,
            policy_hook=policy_hook,
            primitive="write_file",
            reason="missing_terminal_newline",
            relative_path=normalized_relative_path,
        )
        payload = {
            "operation": "insert_at_anchor",
            "path": str(target),
            "relative_path": normalized_relative_path,
            "changed_files": list(write_result.get("changed_files") or [normalized_relative_path]),
            "applied": True,
            "skipped": False,
            "atomic_write": True,
            "position": normalized_position,
            "indent_style": indent_style,
            "match_mode": match.mode,
            "similarity": round(match.similarity, 4),
            "adapter": "write_file_fallback",
            "adapter_chain": [*_adapter_chain(), {"name": "write_file_fallback", "available": True}],
            "fallback_reason": "missing_terminal_newline",
        }
        _emit(policy_hook, "post_insert_at_anchor", payload)
        return payload
    patch_text = _build_file_patch(
        relative_path=normalized_relative_path,
        before_text=before_text,
        after_text=after_text,
    )
    patch_result = apply_patch_with_outcome(
        workspace_root=workspace_root,
        patch_text=patch_text,
        patch_budget=PatchAttemptBudget(max_attempts=10),
        max_conflict_retries=1,
        policy_hook=policy_hook,
        execution_context=execution_context,
    )
    if patch_result.status != "applied":
        raise RuntimeError(
            _patch_failure_message(prefix="INSERT_AT_ANCHOR_PATCH_APPLY_FAILED", patch_result=patch_result)
        )
    payload = {
        "operation": "insert_at_anchor",
        "path": str(target),
        "relative_path": normalized_relative_path,
        "changed_files": [normalized_relative_path],
        "applied": True,
        "skipped": False,
        "atomic_write": True,
        "position": normalized_position,
        "indent_style": indent_style,
        "match_mode": match.mode,
        "similarity": round(match.similarity, 4),
        "patch_attempt": patch_result.patch_attempt,
        "patch_bytes": patch_result.patch_bytes,
        "patch_sha1": patch_result.patch_sha1,
        "approx_diff_bytes": patch_result.approx_diff_bytes,
        "retry_count": patch_result.retry_count,
        "retry_strategy": patch_result.retry_strategy,
        "adapter": "git_apply",
        "adapter_chain": _adapter_chain(),
    }
    _emit(policy_hook, "post_insert_at_anchor", payload)
    return payload


def replace_in_function(
    *,
    workspace_root: str | Path,
    relative_path: str,
    function_name: str,
    replacement: str,
    dry_run: bool = False,
    policy_hook: PolicyHook | None = None,
    runtime: dict[str, Any] | None = None,
    execution_context: dict[str, Any] | None = None,
    expected_base_commit_sha: str | None = None,
    expected_file_blob_sha: str | None = None,
    expected_symbol_source_hash: str | None = None,
) -> dict[str, Any]:
    if not dry_run:
        _assert_deprecated_mutation_tool_enabled(
            primitive="replace_in_function",
            policy_hook=policy_hook,
            execution_context=execution_context,
            relative_path=relative_path,
            symbol=function_name,
        )
    runtime_mode = str((runtime or {}).get("mode") or "").strip().lower()
    # Dry-run never mutates workspace state. When no container runtime is configured,
    # keep validation on host to avoid unnecessary overhead.
    if dry_run and not (
        runtime is not None and (bool((runtime or {}).get("container_required")) or runtime_mode == "docker")
    ):
        runtime = None
        runtime_mode = ""
    if runtime is not None and (bool((runtime or {}).get("container_required")) or runtime_mode == "docker"):
        _emit(
            policy_hook,
            "pre_replace_in_function",
            {"relative_path": relative_path, "function_name": function_name, "runtime": runtime},
        )
        payload = execute_mutation_in_runtime(
            workspace_root=workspace_root,
            tool="replace_in_function",
            args={
                "relative_path": relative_path,
                "function_name": function_name,
                "replacement": replacement,
                "dry_run": dry_run,
                "expected_base_commit_sha": expected_base_commit_sha,
                "expected_file_blob_sha": expected_file_blob_sha,
                "expected_symbol_source_hash": expected_symbol_source_hash,
            },
            runtime=runtime,
        )
        _emit(policy_hook, "post_replace_in_function", payload)
        return payload
    target = _safe_resolve(workspace_root, relative_path)
    symbol = resolve_python_symbol_range(file_path=target, symbol_name=function_name)
    grounded_symbol = _assert_symbol_grounding(
        file_path=target,
        symbol=symbol,
        expected_base_commit_sha=expected_base_commit_sha,
        expected_file_blob_sha=expected_file_blob_sha,
        expected_symbol_source_hash=expected_symbol_source_hash,
    )
    failures = _dry_run_replace_in_function(
        qualified_name=symbol.qualified_name,
        replacement=replacement,
        base_indent=symbol.def_col,
        original_source=symbol.symbol_source,
    )
    if dry_run:
        payload = {
            "operation": "replace_in_function",
            "path": str(target),
            "relative_path": relative_path.replace("\\", "/"),
            "symbol_name": function_name,
            "dry_run": True,
            "dry_run_failures": failures,
            "validated": not failures,
            "changed_files": [],
            "symbol_range": {
                "start_line": symbol.start_line,
                "end_line": symbol.end_line,
                "def_col": symbol.def_col,
                "qualified_name": symbol.qualified_name,
            },
            "grounded_symbol": grounded_symbol,
        }
        _emit(policy_hook, "post_replace_in_function", payload)
        return payload
    if failures:
        first = failures[0]
        raise ValueError(f"{first['failure_code']}: {first['detail']}")
    normalized_replacement = _normalize_function_replacement(
        replacement=replacement,
        base_indent=symbol.def_col,
        original_source=symbol.symbol_source,
        qualified_name=symbol.qualified_name,
    )
    _emit(
        policy_hook,
        "pre_replace_in_function",
        {"relative_path": relative_path, "function_name": function_name, "path": str(target)},
    )
    _replace_function_with_libcst(
        target=target, qualified_name=symbol.qualified_name, replacement=normalized_replacement
    )
    result = {
        "operation": "replace_in_function",
        "path": str(target),
        "relative_path": relative_path.replace("\\", "/"),
        "changed_files": [relative_path.replace("\\", "/")],
        "atomic_write": True,
    }
    result.update(
        {
            "operation": "replace_in_function",
            "symbol_name": function_name,
            "dry_run": False,
            "dry_run_failures": [],
            "symbol_range": {
                "start_line": symbol.start_line,
                "end_line": symbol.end_line,
                "def_col": symbol.def_col,
                "qualified_name": symbol.qualified_name,
            },
            "grounded_symbol": grounded_symbol,
        }
    )
    _emit(policy_hook, "post_replace_in_function", result)
    return result


def replace_function(
    *,
    workspace_root: str | Path,
    relative_path: str,
    qualified_symbol: str,
    replacement: str,
    dry_run: bool = False,
    policy_hook: PolicyHook | None = None,
    runtime: dict[str, Any] | None = None,
    execution_context: dict[str, Any] | None = None,
    expected_base_commit_sha: str | None = None,
    expected_file_blob_sha: str | None = None,
    expected_symbol_source_hash: str | None = None,
) -> dict[str, Any]:
    runtime_mode = str((runtime or {}).get("mode") or "").strip().lower()
    if dry_run and not (
        runtime is not None and (bool((runtime or {}).get("container_required")) or runtime_mode == "docker")
    ):
        runtime = None
        runtime_mode = ""
    if runtime is not None and (bool((runtime or {}).get("container_required")) or runtime_mode == "docker"):
        _emit(
            policy_hook,
            "pre_replace_function",
            {"relative_path": relative_path, "qualified_symbol": qualified_symbol, "runtime": runtime},
        )
        payload = execute_mutation_in_runtime(
            workspace_root=workspace_root,
            tool="replace_in_function",
            args={
                "relative_path": relative_path,
                "function_name": qualified_symbol,
                "replacement": replacement,
                "dry_run": dry_run,
                "expected_base_commit_sha": expected_base_commit_sha,
                "expected_file_blob_sha": expected_file_blob_sha,
                "expected_symbol_source_hash": expected_symbol_source_hash,
            },
            runtime=runtime,
        )
        payload.setdefault("operation", "replace_function")
        payload.setdefault("symbol_name", qualified_symbol)
        _emit(policy_hook, "post_replace_function", payload)
        return payload

    target = _safe_resolve(workspace_root, relative_path)
    symbol = resolve_python_symbol_range(file_path=target, symbol_name=qualified_symbol)
    grounded_symbol = _assert_symbol_grounding(
        file_path=target,
        symbol=symbol,
        expected_base_commit_sha=expected_base_commit_sha,
        expected_file_blob_sha=expected_file_blob_sha,
        expected_symbol_source_hash=expected_symbol_source_hash,
    )
    failures = _dry_run_replace_in_function(
        qualified_name=symbol.qualified_name,
        replacement=replacement,
        base_indent=symbol.def_col,
        original_source=symbol.symbol_source,
        relative_path=relative_path,
    )
    if dry_run:
        payload = {
            "operation": "replace_function",
            "path": str(target),
            "relative_path": relative_path.replace("\\", "/"),
            "symbol_name": qualified_symbol,
            "dry_run": True,
            "dry_run_failures": failures,
            "validated": not failures,
            "changed_files": [],
            "symbol_range": {
                "start_line": symbol.start_line,
                "end_line": symbol.end_line,
                "def_col": symbol.def_col,
                "qualified_name": symbol.qualified_name,
            },
            "grounded_symbol": grounded_symbol,
        }
        _emit(policy_hook, "post_replace_function", payload)
        return payload
    if failures:
        first = failures[0]
        raise ValueError(f"{first['failure_code']}: {first['detail']}")

    normalized_replacement = _normalize_function_replacement(
        replacement=replacement,
        base_indent=symbol.def_col,
        original_source=symbol.symbol_source,
        qualified_name=symbol.qualified_name,
        relative_path=relative_path,
    )
    _emit(
        policy_hook,
        "pre_replace_function",
        {"relative_path": relative_path, "qualified_symbol": qualified_symbol, "path": str(target)},
    )
    _replace_function_with_libcst(
        target=target,
        qualified_name=symbol.qualified_name,
        replacement=normalized_replacement,
    )
    result = {
        "operation": "replace_function",
        "path": str(target),
        "relative_path": relative_path.replace("\\", "/"),
        "changed_files": [relative_path.replace("\\", "/")],
        "atomic_write": True,
        "symbol_name": qualified_symbol,
        "dry_run": False,
        "dry_run_failures": [],
        "symbol_range": {
            "start_line": symbol.start_line,
            "end_line": symbol.end_line,
            "def_col": symbol.def_col,
            "qualified_name": symbol.qualified_name,
        },
        "grounded_symbol": grounded_symbol,
    }
    _emit(policy_hook, "post_replace_function", result)
    return result


def validate_replace_function_candidate(
    *,
    workspace_root: str | Path,
    relative_path: str,
    qualified_symbol: str,
    replacement: str,
    expected_base_commit_sha: str | None = None,
    expected_file_blob_sha: str | None = None,
    expected_symbol_source_hash: str | None = None,
) -> dict[str, Any]:
    target = _safe_resolve(workspace_root, relative_path)
    symbol = resolve_python_symbol_range(file_path=target, symbol_name=qualified_symbol)
    grounded_symbol = _assert_symbol_grounding(
        file_path=target,
        symbol=symbol,
        expected_base_commit_sha=expected_base_commit_sha,
        expected_file_blob_sha=expected_file_blob_sha,
        expected_symbol_source_hash=expected_symbol_source_hash,
    )
    failures = _dry_run_replace_in_function(
        qualified_name=symbol.qualified_name,
        replacement=replacement,
        base_indent=symbol.def_col,
        original_source=symbol.symbol_source,
        relative_path=relative_path,
    )
    original_tree = ast.parse(textwrap.dedent(str(symbol.symbol_source or "").strip()))
    original_func = next(
        (node for node in original_tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
        None,
    )
    replacement_func: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    try:
        _replacement_tree, replacement_func = _single_top_level_function(
            candidate=textwrap.dedent(str(replacement or "")).strip(),
            qualified_name=symbol.qualified_name,
        )
    except ValueError:
        replacement_func = None
    return {
        "validated": not failures,
        "dry_run_failures": failures,
        "symbol_range": {
            "start_line": symbol.start_line,
            "end_line": symbol.end_line,
            "def_col": symbol.def_col,
            "qualified_name": symbol.qualified_name,
        },
        "grounded_symbol": grounded_symbol,
        "replacement_shape_debug": _shape_validation_debug_payload(
            relative_path=relative_path,
            qualified_name=symbol.qualified_name,
            original_source=symbol.symbol_source,
            replacement=replacement,
            original_func=original_func,
            replacement_func=replacement_func,
        ),
    }


def edit_symbol(
    *,
    workspace_root: str | Path,
    relative_path: str,
    symbol: str,
    mode: str,
    new_signature: str | None = None,
    anchor_pattern: str | None = None,
    position: str | None = None,
    new_code: str | None = None,
    dry_run: bool = False,
    policy_hook: PolicyHook | None = None,
    runtime: dict[str, Any] | None = None,
    execution_context: dict[str, Any] | None = None,
    expected_base_commit_sha: str | None = None,
    expected_file_blob_sha: str | None = None,
    expected_symbol_source_hash: str | None = None,
) -> dict[str, Any]:
    if not dry_run:
        _assert_deprecated_mutation_tool_enabled(
            primitive="edit_symbol",
            policy_hook=policy_hook,
            execution_context=execution_context,
            relative_path=relative_path,
            symbol=symbol,
        )
    runtime_mode = str((runtime or {}).get("mode") or "").strip().lower()
    # Dry-run never mutates workspace state. When no container runtime is configured,
    # keep validation on host to avoid unnecessary overhead.
    if dry_run and not (
        runtime is not None and (bool((runtime or {}).get("container_required")) or runtime_mode == "docker")
    ):
        runtime = None
        runtime_mode = ""
    if runtime is not None and (bool((runtime or {}).get("container_required")) or runtime_mode == "docker"):
        _emit(
            policy_hook,
            "pre_edit_symbol",
            {"relative_path": relative_path, "symbol": symbol, "mode": mode, "dry_run": dry_run, "runtime": runtime},
        )
        payload = execute_mutation_in_runtime(
            workspace_root=workspace_root,
            tool="edit_symbol",
            args={
                "relative_path": relative_path,
                "symbol": symbol,
                "mode": mode,
                "new_signature": new_signature,
                "anchor_pattern": anchor_pattern,
                "position": position,
                "new_code": new_code,
                "dry_run": dry_run,
                "expected_base_commit_sha": expected_base_commit_sha,
                "expected_file_blob_sha": expected_file_blob_sha,
                "expected_symbol_source_hash": expected_symbol_source_hash,
            },
            runtime=runtime,
        )
        _emit(policy_hook, "post_edit_symbol", payload)
        return payload

    target = _safe_resolve(workspace_root, relative_path)
    symbol_range = resolve_python_symbol_range(file_path=target, symbol_name=symbol)
    grounded_symbol = _assert_symbol_grounding(
        file_path=target,
        symbol=symbol_range,
        expected_base_commit_sha=expected_base_commit_sha,
        expected_file_blob_sha=expected_file_blob_sha,
        expected_symbol_source_hash=expected_symbol_source_hash,
    )
    failures = _dry_run_edit_symbol(
        target=target,
        symbol=symbol_range.qualified_name,
        mode=mode,
        new_signature=new_signature,
        anchor_pattern=anchor_pattern,
        position=position,
        new_code=new_code,
    )
    if dry_run:
        payload = {
            "operation": "edit_symbol",
            "path": str(target),
            "relative_path": relative_path.replace("\\", "/"),
            "symbol": symbol_range.qualified_name,
            "mode": str(mode or "").strip(),
            "dry_run": True,
            "dry_run_failures": failures,
            "validated": not failures,
            "changed_files": [],
            "grounded_symbol": grounded_symbol,
        }
        _emit(policy_hook, "post_edit_symbol", payload)
        return payload
    if failures:
        first = failures[0]
        if str(first.get("failure") or "") == "ANCHOR_NOT_FOUND":
            _emit(
                policy_hook,
                "anchor_not_found_compat",
                {
                    "tool": "edit_symbol",
                    "symbol": symbol_range.qualified_name,
                    "relative_path": relative_path.replace("\\", "/"),
                    "anchor_pattern": str(anchor_pattern or ""),
                    "shim_event": "anchor_not_found_compat",
                },
            )
        raise ValueError(f"{first['failure']}: {first['detail']}")

    _emit(
        policy_hook,
        "pre_edit_symbol",
        {"relative_path": relative_path, "symbol": symbol_range.qualified_name, "mode": mode, "path": str(target)},
    )
    _edit_symbol_with_libcst(
        target=target,
        qualified_name=symbol_range.qualified_name,
        mode=str(mode or "").strip(),
        new_signature=str(new_signature or ""),
        anchor_pattern=str(anchor_pattern or ""),
        position=str(position or ""),
        new_code=str(new_code or ""),
    )
    payload = {
        "operation": "edit_symbol",
        "path": str(target),
        "relative_path": relative_path.replace("\\", "/"),
        "symbol": symbol_range.qualified_name,
        "mode": str(mode or "").strip(),
        "changed_files": [relative_path.replace("\\", "/")],
        "atomic_write": True,
        "dry_run": False,
        "dry_run_failures": [],
        "symbol_range": {
            "start_line": symbol_range.start_line,
            "end_line": symbol_range.end_line,
            "def_col": symbol_range.def_col,
            "qualified_name": symbol_range.qualified_name,
        },
        "grounded_symbol": grounded_symbol,
    }
    _emit(policy_hook, "post_edit_symbol", payload)
    return payload


def extend_python_signature(
    *,
    workspace_root: str | Path,
    relative_path: str,
    symbol: str,
    parameter_name: str,
    parameter_type: str,
    default_value: str,
    parameter_kind: str = "auto",
    dry_run: bool = False,
    policy_hook: PolicyHook | None = None,
    runtime: dict[str, Any] | None = None,
    expected_base_commit_sha: str | None = None,
    expected_file_blob_sha: str | None = None,
    expected_symbol_source_hash: str | None = None,
) -> dict[str, Any]:
    runtime_mode = str((runtime or {}).get("mode") or "").strip().lower()
    if dry_run and not (
        runtime is not None and (bool((runtime or {}).get("container_required")) or runtime_mode == "docker")
    ):
        runtime = None
        runtime_mode = ""
    if runtime is not None and (bool((runtime or {}).get("container_required")) or runtime_mode == "docker"):
        _emit(
            policy_hook,
            "pre_extend_python_signature",
            {
                "relative_path": relative_path,
                "symbol": symbol,
                "parameter_name": parameter_name,
                "parameter_kind": parameter_kind,
                "dry_run": dry_run,
                "runtime": runtime,
            },
        )
        payload = execute_mutation_in_runtime(
            workspace_root=workspace_root,
            tool="extend_python_signature",
            args={
                "relative_path": relative_path,
                "symbol": symbol,
                "parameter_name": parameter_name,
                "parameter_type": parameter_type,
                "default_value": default_value,
                "parameter_kind": parameter_kind,
                "dry_run": dry_run,
                "expected_base_commit_sha": expected_base_commit_sha,
                "expected_file_blob_sha": expected_file_blob_sha,
                "expected_symbol_source_hash": expected_symbol_source_hash,
            },
            runtime=runtime,
        )
        _emit(policy_hook, "post_extend_python_signature", payload)
        return payload

    target = _safe_resolve(workspace_root, relative_path)
    symbol_range = resolve_python_symbol_range(file_path=target, symbol_name=symbol)
    grounded_symbol = _assert_symbol_grounding(
        file_path=target,
        symbol=symbol_range,
        expected_base_commit_sha=expected_base_commit_sha,
        expected_file_blob_sha=expected_file_blob_sha,
        expected_symbol_source_hash=expected_symbol_source_hash,
    )
    failures = _dry_run_extend_python_signature(
        target=target,
        symbol=symbol_range.qualified_name,
        parameter_name=parameter_name,
        parameter_type=parameter_type,
        default_value=default_value,
        parameter_kind=parameter_kind,
    )
    normalized_relative_path = relative_path.replace("\\", "/")
    if dry_run:
        payload = {
            "operation": "extend_python_signature",
            "path": str(target),
            "relative_path": normalized_relative_path,
            "symbol": symbol_range.qualified_name,
            "parameter_name": str(parameter_name or "").strip(),
            "parameter_kind": _normalize_signature_parameter_kind(parameter_kind),
            "dry_run": True,
            "dry_run_failures": failures,
            "validated": not failures,
            "changed_files": [],
            "grounded_symbol": grounded_symbol,
        }
        _emit(policy_hook, "post_extend_python_signature", payload)
        return payload
    if failures:
        first = failures[0]
        raise ValueError(f"{first['failure']}: {first['detail']}")

    _emit(
        policy_hook,
        "pre_extend_python_signature",
        {
            "relative_path": normalized_relative_path,
            "symbol": symbol_range.qualified_name,
            "parameter_name": parameter_name,
            "parameter_kind": _normalize_signature_parameter_kind(parameter_kind),
            "path": str(target),
        },
    )
    _extend_python_signature_with_libcst(
        target=target,
        qualified_name=symbol_range.qualified_name,
        parameter_name=parameter_name,
        parameter_type=parameter_type,
        default_value=default_value,
        parameter_kind=parameter_kind,
    )
    payload = {
        "operation": "extend_python_signature",
        "path": str(target),
        "relative_path": normalized_relative_path,
        "symbol": symbol_range.qualified_name,
        "parameter_name": str(parameter_name or "").strip(),
        "parameter_kind": _normalize_signature_parameter_kind(parameter_kind),
        "changed_files": [normalized_relative_path],
        "atomic_write": True,
        "dry_run": False,
        "dry_run_failures": [],
        "symbol_range": {
            "start_line": symbol_range.start_line,
            "end_line": symbol_range.end_line,
            "def_col": symbol_range.def_col,
            "qualified_name": symbol_range.qualified_name,
        },
        "grounded_symbol": grounded_symbol,
    }
    _emit(policy_hook, "post_extend_python_signature", payload)
    return payload


def update_call_kwarg(
    *,
    workspace_root: str | Path,
    relative_path: str,
    symbol: str,
    callee: str,
    kwarg: str,
    new_value: str,
    dry_run: bool = False,
    policy_hook: PolicyHook | None = None,
    runtime: dict[str, Any] | None = None,
    execution_context: dict[str, Any] | None = None,
    expected_base_commit_sha: str | None = None,
    expected_file_blob_sha: str | None = None,
    expected_symbol_source_hash: str | None = None,
) -> dict[str, Any]:
    if not dry_run:
        _assert_deprecated_mutation_tool_enabled(
            primitive="update_call_kwarg",
            policy_hook=policy_hook,
            execution_context=execution_context,
            relative_path=relative_path,
            symbol=symbol,
        )
    runtime_mode = str((runtime or {}).get("mode") or "").strip().lower()
    if dry_run and not (
        runtime is not None and (bool((runtime or {}).get("container_required")) or runtime_mode == "docker")
    ):
        runtime = None
        runtime_mode = ""
    if runtime is not None and (bool((runtime or {}).get("container_required")) or runtime_mode == "docker"):
        _emit(
            policy_hook,
            "pre_update_call_kwarg",
            {
                "relative_path": relative_path,
                "symbol": symbol,
                "callee": callee,
                "kwarg": kwarg,
                "dry_run": dry_run,
                "runtime": runtime,
            },
        )
        payload = execute_mutation_in_runtime(
            workspace_root=workspace_root,
            tool="update_call_kwarg",
            args={
                "relative_path": relative_path,
                "symbol": symbol,
                "callee": callee,
                "kwarg": kwarg,
                "new_value": new_value,
                "dry_run": dry_run,
                "expected_base_commit_sha": expected_base_commit_sha,
                "expected_file_blob_sha": expected_file_blob_sha,
                "expected_symbol_source_hash": expected_symbol_source_hash,
            },
            runtime=runtime,
        )
        _emit(policy_hook, "post_update_call_kwarg", payload)
        return payload

    target = _safe_resolve(workspace_root, relative_path)
    symbol_range = resolve_python_symbol_range(file_path=target, symbol_name=symbol)
    grounded_symbol = _assert_symbol_grounding(
        file_path=target,
        symbol=symbol_range,
        expected_base_commit_sha=expected_base_commit_sha,
        expected_file_blob_sha=expected_file_blob_sha,
        expected_symbol_source_hash=expected_symbol_source_hash,
    )
    failures = _dry_run_update_call_kwarg(
        target=target,
        symbol=symbol_range.qualified_name,
        callee=callee,
        kwarg=kwarg,
        new_value=new_value,
    )
    if dry_run:
        payload = {
            "operation": "update_call_kwarg",
            "path": str(target),
            "relative_path": relative_path.replace("\\", "/"),
            "symbol": symbol_range.qualified_name,
            "callee": str(callee or "").strip(),
            "kwarg": str(kwarg or "").strip(),
            "dry_run": True,
            "dry_run_failures": failures,
            "validated": not failures,
            "changed_files": [],
            "grounded_symbol": grounded_symbol,
        }
        _emit(policy_hook, "post_update_call_kwarg", payload)
        return payload
    if failures:
        first = failures[0]
        raise ValueError(f"{first['failure']}: {first['detail']}")

    _emit(
        policy_hook,
        "pre_update_call_kwarg",
        {
            "relative_path": relative_path,
            "symbol": symbol_range.qualified_name,
            "callee": callee,
            "kwarg": kwarg,
            "path": str(target),
        },
    )
    _update_call_kwarg_with_libcst(
        target=target,
        qualified_name=symbol_range.qualified_name,
        callee=str(callee or "").strip(),
        kwarg=str(kwarg or "").strip(),
        new_value=str(new_value or "").strip(),
    )
    payload = {
        "operation": "update_call_kwarg",
        "path": str(target),
        "relative_path": relative_path.replace("\\", "/"),
        "symbol": symbol_range.qualified_name,
        "callee": str(callee or "").strip(),
        "kwarg": str(kwarg or "").strip(),
        "changed_files": [relative_path.replace("\\", "/")],
        "atomic_write": True,
        "dry_run": False,
        "dry_run_failures": [],
        "symbol_range": {
            "start_line": symbol_range.start_line,
            "end_line": symbol_range.end_line,
            "def_col": symbol_range.def_col,
            "qualified_name": symbol_range.qualified_name,
        },
        "grounded_symbol": grounded_symbol,
    }
    _emit(policy_hook, "post_update_call_kwarg", payload)
    return payload


_STAGED_DELIVERY_ALLOWED_TOOLS = {
    "replace_in_function",
    "update_call_kwarg",
}
_STAGED_DELIVERY_MAX_STAGES = 4
_STAGED_DELIVERY_MAX_FILES = 3
_STAGED_DELIVERY_PHASE_ORDER = {"impl": 0, "router": 1, "tests": 2}


def _classify_staged_delivery_path(relative_path: str) -> str:
    normalized = str(relative_path or "").strip().replace("\\", "/").lower()
    if (
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or normalized.endswith("_test.py")
        or normalized.endswith("test_.py")
    ):
        return "tests"
    if "/routers/" in normalized or "/routes/" in normalized or normalized.endswith("_router.py"):
        return "router"
    return "impl"


def _module_scope_import_names(target: Path) -> set[str]:
    try:
        tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    except SyntaxError:
        return set()
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(str(alias.asname or alias.name.split(".", 1)[0]).strip())
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(str(alias.asname or alias.name).strip())
    return {name for name in imported if name}


def _validate_staged_delivery_stages(
    *,
    stages: list[dict[str, Any]],
    max_stages: int,
    max_files: int,
    dry_run: bool,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not stages:
        raise ValueError("STAGED_DELIVERY_EMPTY")
    if len(stages) > max_stages:
        raise ValueError(f"STAGED_DELIVERY_STAGE_LIMIT_EXCEEDED limit={max_stages} provided={len(stages)}")
    seen_files: set[str] = set()
    previous_rank = -1
    for index, raw_stage in enumerate(stages, start=1):
        if not isinstance(raw_stage, dict):
            raise ValueError(f"STAGED_DELIVERY_INVALID_STAGE index={index} detail=stage_must_be_object")
        tool = str(raw_stage.get("tool") or "").strip()
        if tool not in _STAGED_DELIVERY_ALLOWED_TOOLS:
            raise ValueError(f"STAGED_DELIVERY_UNSUPPORTED_TOOL index={index} tool={tool or '<empty>'}")
        args = dict(raw_stage.get("args") or {})
        relative_path = str(args.get("relative_path") or "").strip().replace("\\", "/")
        if not relative_path:
            raise ValueError(f"STAGED_DELIVERY_RELATIVE_PATH_REQUIRED index={index} tool={tool}")
        seen_files.add(relative_path)
        if len(seen_files) > max_files:
            raise ValueError(f"STAGED_DELIVERY_FILE_LIMIT_EXCEEDED limit={max_files} provided={len(seen_files)}")
        phase = _classify_staged_delivery_path(relative_path)
        rank = _STAGED_DELIVERY_PHASE_ORDER[phase]
        if rank < previous_rank:
            raise ValueError(
                f"STAGED_DELIVERY_ORDER_INVALID index={index} phase={phase} previous_phase_rank={previous_rank}"
            )
        previous_rank = rank
        required_imports = [str(item).strip() for item in raw_stage.get("required_imports") or [] if str(item).strip()]
        required_symbols = [str(item).strip() for item in raw_stage.get("required_symbols") or [] if str(item).strip()]
        normalized.append(
            {
                "tool": tool,
                "args": args,
                "relative_path": relative_path,
                "phase": phase,
                "required_imports": required_imports,
                "required_symbols": required_symbols,
            }
        )
    return normalized


def _enforce_staged_delivery_consistency(
    *,
    workspace_root: str | Path,
    relative_path: str,
    required_imports: list[str],
    required_symbols: list[str],
) -> dict[str, Any]:
    target = _safe_resolve(workspace_root, relative_path)
    missing_imports = sorted(set(required_imports) - _module_scope_import_names(target))
    if missing_imports:
        raise ValueError(
            "STAGED_IMPORT_CONSISTENCY_FAILED "
            + f"relative_path={relative_path} missing_imports={','.join(missing_imports)}"
        )
    resolved_symbols: list[str] = []
    for symbol_name in required_symbols:
        symbol = resolve_python_symbol_range(file_path=target, symbol_name=symbol_name)
        resolved_symbols.append(symbol.qualified_name)
    return {
        "required_imports": list(required_imports),
        "required_symbols": list(required_symbols),
        "resolved_symbols": resolved_symbols,
    }


def apply_staged_delivery_slice(
    *,
    workspace_root: str | Path,
    stages: list[dict[str, Any]],
    dry_run: bool = False,
    policy_hook: PolicyHook | None = None,
    runtime: dict[str, Any] | None = None,
    max_stages: int = _STAGED_DELIVERY_MAX_STAGES,
    max_files: int = _STAGED_DELIVERY_MAX_FILES,
) -> dict[str, Any]:
    runtime_mode = str((runtime or {}).get("mode") or "").strip().lower()
    if runtime is not None and (bool((runtime or {}).get("container_required")) or runtime_mode == "docker"):
        _emit(
            policy_hook,
            "pre_apply_staged_delivery_slice",
            {"stage_count": len(stages), "dry_run": dry_run, "runtime": runtime},
        )
        payload = execute_mutation_in_runtime(
            workspace_root=workspace_root,
            tool="apply_staged_delivery_slice",
            args={
                "stages": stages,
                "dry_run": dry_run,
                "max_stages": max_stages,
                "max_files": max_files,
            },
            runtime=runtime,
        )
        _emit(policy_hook, "post_apply_staged_delivery_slice", payload)
        return payload

    normalized_stages = _validate_staged_delivery_stages(
        stages=stages,
        max_stages=max_stages,
        max_files=max_files,
        dry_run=dry_run,
    )
    results: list[dict[str, Any]] = []
    changed_files: set[str] = set()
    for index, stage in enumerate(normalized_stages, start=1):
        tool = stage["tool"]
        args = dict(stage["args"])
        if tool == "replace_in_function":
            stage_result = replace_in_function(
                workspace_root=workspace_root,
                relative_path=str(args.get("relative_path") or ""),
                function_name=str(args.get("function_name") or ""),
                replacement=str(args.get("replacement") or ""),
                dry_run=dry_run,
            )
        elif tool == "update_call_kwarg":
            stage_result = update_call_kwarg(
                workspace_root=workspace_root,
                relative_path=str(args.get("relative_path") or ""),
                symbol=str(args.get("symbol") or ""),
                callee=str(args.get("callee") or ""),
                kwarg=str(args.get("kwarg") or ""),
                new_value=str(args.get("new_value") or ""),
                dry_run=dry_run,
            )
        else:
            raise ValueError(f"STAGED_DELIVERY_UNSUPPORTED_TOOL tool={tool}")
        consistency = _enforce_staged_delivery_consistency(
            workspace_root=workspace_root,
            relative_path=stage["relative_path"],
            required_imports=list(stage.get("required_imports") or []),
            required_symbols=list(stage.get("required_symbols") or []),
        )
        changed_files.update(str(item).strip() for item in stage_result.get("changed_files") or [] if str(item).strip())
        results.append(
            {
                "index": index,
                "tool": tool,
                "relative_path": stage["relative_path"],
                "phase": stage["phase"],
                "consistency": consistency,
                "result": stage_result,
            }
        )
    payload = {
        "operation": "apply_staged_delivery_slice",
        "stage_count": len(results),
        "file_count": len({stage["relative_path"] for stage in normalized_stages}),
        "dry_run": dry_run,
        "validated": True,
        "changed_files": sorted(changed_files),
        "stages": results,
    }
    _emit(policy_hook, "post_apply_staged_delivery_slice", payload)
    return payload


def insert_after_symbol(
    *,
    workspace_root: str | Path,
    relative_path: str,
    symbol_name: str,
    new_content: str,
    policy_hook: PolicyHook | None = None,
    runtime: dict[str, Any] | None = None,
    execution_context: dict[str, Any] | None = None,
    expected_base_commit_sha: str | None = None,
    expected_file_blob_sha: str | None = None,
    expected_symbol_source_hash: str | None = None,
) -> dict[str, Any]:
    _emit(
        policy_hook,
        "removed_insert_after_symbol",
        {"relative_path": relative_path, "symbol_name": symbol_name, "runtime": runtime},
    )
    _raise_removed_mutation_tool(primitive="insert_after_symbol")
    raise AssertionError("unreachable")


def insert_before_return(
    *,
    workspace_root: str | Path,
    relative_path: str,
    function_name: str,
    new_content: str,
    policy_hook: PolicyHook | None = None,
    runtime: dict[str, Any] | None = None,
    execution_context: dict[str, Any] | None = None,
    expected_base_commit_sha: str | None = None,
    expected_file_blob_sha: str | None = None,
    expected_symbol_source_hash: str | None = None,
) -> dict[str, Any]:
    _emit(
        policy_hook,
        "removed_insert_before_return",
        {"relative_path": relative_path, "function_name": function_name, "runtime": runtime},
    )
    _raise_removed_mutation_tool(primitive="insert_before_return")
    raise AssertionError("unreachable")


def _adapter_chain() -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    for name, module_name in [("pygit2", "pygit2"), ("python_patch", "patch"), ("unidiff", "unidiff")]:
        try:
            __import__(module_name)
            available = True
        except Exception:
            available = False
        chain.append({"name": name, "available": available})
    chain.append({"name": "git_apply", "available": True})
    return chain


def _normalize_edit_symbol_mode(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "body":
        return "body_insert"
    return normalized


def _mutation_failure_payload(
    *,
    tool: str,
    symbol: str,
    failure_code: str,
    detail: str,
    suggested_fix: str,
    mode: str | None = None,
) -> dict[str, str]:
    payload = {
        "tool": str(tool or "").strip(),
        "symbol": str(symbol or "").strip(),
        "failure": str(failure_code or "").strip(),
        "failure_code": str(failure_code or "").strip(),
        "detail": str(detail or "").strip(),
        "suggested_fix": str(suggested_fix or "").strip(),
    }
    if str(mode or "").strip():
        payload["mode"] = str(mode or "").strip()
    return payload


def _single_top_level_function(
    *, candidate: str, qualified_name: str
) -> tuple[ast.Module, ast.FunctionDef | ast.AsyncFunctionDef]:
    try:
        tree = ast.parse(candidate)
    except SyntaxError as exc:
        if "expected an indented block" in str(exc):
            raise ValueError("MUTATION_FRAGMENT_REJECTED function body required") from exc
        raise ValueError(f"MUTATION_INVALID_FUNCTION_REPLACEMENT syntax={exc.msg}") from exc
    function_nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if len(function_nodes) != 1 or len(tree.body) != 1:
        raise ValueError("MUTATION_INVALID_FUNCTION_REPLACEMENT exactly_one_function_definition_required")
    func = function_nodes[0]
    expected_name = str(qualified_name or "").split(".")[-1].strip()
    if expected_name and func.name != expected_name:
        raise ValueError(
            f"MUTATION_INVALID_FUNCTION_SHAPE symbol_name_mismatch expected={expected_name} got={func.name}"
        )
    return tree, func


def _shape_validation_debug_payload(
    *,
    relative_path: str | None,
    qualified_name: str,
    original_source: str,
    replacement: str,
    original_func: ast.FunctionDef | ast.AsyncFunctionDef | None,
    replacement_func: ast.FunctionDef | ast.AsyncFunctionDef | None,
    failure_detail: str | None = None,
) -> dict[str, Any]:
    return {
        "relative_path": str(relative_path or "").strip().replace("\\", "/") or None,
        "resolved_symbol": str(qualified_name or "").strip() or None,
        "original_symbol_header": _function_header_text(original_source) or None,
        "replacement_header": _function_header_text(replacement) or None,
        "is_async_original": isinstance(original_func, ast.AsyncFunctionDef),
        "is_async_replacement": isinstance(replacement_func, ast.AsyncFunctionDef),
        "parsed_replacement_symbol_name": getattr(replacement_func, "name", None),
        "replacement_preview_lines": _replacement_preview_lines(replacement),
        "failure_detail": str(failure_detail or "").strip() or None,
    }


def _log_replace_function_shape_violation(
    *,
    relative_path: str | None,
    qualified_name: str,
    original_source: str,
    replacement: str,
    original_func: ast.FunctionDef | ast.AsyncFunctionDef | None,
    replacement_func: ast.FunctionDef | ast.AsyncFunctionDef | None,
    failure_detail: str,
) -> dict[str, Any]:
    payload = _shape_validation_debug_payload(
        relative_path=relative_path,
        qualified_name=qualified_name,
        original_source=original_source,
        replacement=replacement,
        original_func=original_func,
        replacement_func=replacement_func,
        failure_detail=failure_detail,
    )
    logger.error(
        "replace_function_shape_validation %s",
        json.dumps(payload, ensure_ascii=True, sort_keys=True),
    )
    return payload


def _dry_run_edit_symbol(
    *,
    target: Path,
    symbol: str,
    mode: str,
    new_signature: str | None,
    anchor_pattern: str | None,
    position: str | None,
    new_code: str | None,
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    normalized_mode = _normalize_edit_symbol_mode(mode)
    if normalized_mode not in {"signature", "body_insert", "sql_blocks", "full", "refactor_symbol"}:
        return [
            _mutation_failure_payload(
                tool="edit_symbol",
                symbol=symbol,
                mode=normalized_mode,
                failure_code="INVALID_MODE",
                detail=f"unsupported mode={normalized_mode!r}",
                suggested_fix="Use mode='signature', mode='body_insert', mode='sql_blocks', mode='full', or mode='refactor_symbol'.",
            )
        ]
    if "." not in str(symbol or ""):
        return [
            _mutation_failure_payload(
                tool="edit_symbol",
                symbol=str(symbol or ""),
                mode=normalized_mode,
                failure_code="INVALID_SYMBOL_FORMAT",
                detail="symbol must be fully qualified",
                suggested_fix="Use ClassName.method_name or module-level qualified symbol naming.",
            )
        ]
    if normalized_mode == "signature":
        failures.extend(
            _validate_signature_edit(
                target=target,
                symbol=symbol,
                new_signature=str(new_signature or ""),
            )
        )
    elif normalized_mode == "body_insert":
        failures.extend(
            _validate_body_insert_edit(
                target=target,
                symbol=symbol,
                anchor_pattern=str(anchor_pattern or ""),
                position=str(position or ""),
                new_code=str(new_code or ""),
            )
        )
    elif normalized_mode == "sql_blocks":
        failures.extend(
            _validate_sql_block_edit(
                target=target,
                symbol=symbol,
                new_code=str(new_code or ""),
            )
        )
    elif normalized_mode == "full":
        failures.extend(
            _validate_full_symbol_edit(
                target=target,
                symbol=symbol,
                replacement=str(new_code or ""),
            )
        )
    elif normalized_mode == "refactor_symbol":
        failures.extend(
            _validate_refactor_symbol_edit(
                target=target,
                symbol=symbol,
                new_code=str(new_code or ""),
            )
        )
    return failures


def _replace_in_function_suggested_fix(error_text: str) -> tuple[str, str]:
    normalized = str(error_text or "").strip()
    if normalized.startswith("MUTATION_INVALID_FUNCTION_REPLACEMENT missing_function_signature"):
        return (
            "MUTATION_INVALID_FUNCTION_REPLACEMENT",
            "Provide the full replacement function source beginning with def or async def.",
        )
    if normalized.startswith("MUTATION_INVALID_FUNCTION_REPLACEMENT syntax="):
        return (
            "MUTATION_INVALID_FUNCTION_REPLACEMENT",
            "Provide valid Python for the full replacement function source.",
        )
    if normalized.startswith("MUTATION_INVALID_FUNCTION_REPLACEMENT missing_function_node"):
        return (
            "MUTATION_INVALID_FUNCTION_REPLACEMENT",
            "Provide exactly one replacement function or method definition.",
        )
    if normalized.startswith("MUTATION_INVALID_FUNCTION_REPLACEMENT exactly_one_function_definition_required"):
        return (
            "MUTATION_INVALID_FUNCTION_REPLACEMENT",
            "Provide exactly one complete replacement function definition and no extra helpers, prose, or fences.",
        )
    if normalized.startswith("MUTATION_FRAGMENT_REJECTED"):
        return (
            "MUTATION_FRAGMENT_REJECTED",
            "Provide the full replacement function source with a real function body, not only the signature or a stub.",
        )
    if normalized.startswith("MUTATION_INVALID_FUNCTION_SHAPE async_mismatch"):
        return (
            "MUTATION_INVALID_FUNCTION_SHAPE",
            "Preserve async def when replacing an async method.",
        )
    if normalized.startswith("MUTATION_INVALID_FUNCTION_SHAPE method_receiver_mismatch"):
        return (
            "MUTATION_INVALID_FUNCTION_SHAPE",
            "Preserve the method receiver argument when replacing a class method.",
        )
    if normalized.startswith("MUTATION_INVALID_FUNCTION_SHAPE symbol_name_mismatch"):
        return (
            "MUTATION_INVALID_FUNCTION_SHAPE",
            "Preserve the exact grounded function name when emitting replace_function replacement text.",
        )
    if normalized.startswith("SQL_PRESERVATION_VIOLATED"):
        return (
            "SQL_PRESERVATION_VIOLATED",
            "Preserve the existing SQL blocks and only add the requested additive condition or parameter threading.",
        )
    return ("REPLACE_IN_FUNCTION_VALIDATION_FAILED", normalized or "replace_in_function validation failed")


def _dry_run_replace_in_function(
    *,
    qualified_name: str,
    replacement: str,
    base_indent: int,
    original_source: str,
    relative_path: str | None = None,
) -> list[dict[str, str]]:
    try:
        _normalize_function_replacement(
            replacement=replacement,
            base_indent=base_indent,
            original_source=original_source,
            qualified_name=qualified_name,
            relative_path=relative_path,
        )
    except ValueError as exc:
        failure_code, suggested_fix = _replace_in_function_suggested_fix(str(exc))
        return [
            _mutation_failure_payload(
                tool="replace_in_function",
                symbol=qualified_name,
                failure_code=failure_code,
                detail=str(exc),
                suggested_fix=suggested_fix,
            )
        ]
    return []


def _parse_sql_block_payload(raw_value: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(str(raw_value or "").strip())
    except Exception as exc:
        raise ValueError(f"invalid_json: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    top_level_conditions = payload.get("conditions")
    if top_level_conditions is not None:
        if not isinstance(top_level_conditions, list) or not top_level_conditions:
            raise ValueError("top-level conditions must be a non-empty list")
        top_level_parsed_conditions: list[dict[str, str]] = []
        for condition in top_level_conditions:
            if not isinstance(condition, dict):
                raise ValueError("each condition must be an object")
            column = condition.get("column")
            operator = condition.get("operator", "=")
            param_variable = condition.get("param_variable")
            if not isinstance(column, str) or not column.strip():
                raise ValueError("each condition column must be a non-empty string")
            if not isinstance(operator, str) or not operator.strip():
                raise ValueError("each condition operator must be a non-empty string")
            if not isinstance(param_variable, str) or not param_variable.strip():
                raise ValueError("each condition param_variable must be a non-empty string")
            top_level_parsed_conditions.append(
                {
                    "column": column.strip(),
                    "operator": operator.strip(),
                    "param_variable": param_variable.strip(),
                }
            )
        return [{"index": "all", "conditions": top_level_parsed_conditions}]
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("blocks array is required")
    parsed: list[dict[str, Any]] = []
    for item in blocks:
        if not isinstance(item, dict):
            raise ValueError("each block entry must be an object")
        index = item.get("index")
        sql = item.get("sql")
        predicate = item.get("predicate")
        argument = item.get("argument", "relation_type")
        conditions = item.get("conditions")
        if not isinstance(index, int) or index < 1:
            raise ValueError("each block index must be a positive integer")
        if isinstance(sql, str) and sql.strip():
            parsed.append({"index": index, "sql": sql})
            continue
        if conditions is not None:
            if not isinstance(conditions, list) or not conditions:
                raise ValueError("each block conditions must be a non-empty list")
            block_parsed_conditions: list[dict[str, str]] = []
            for condition in conditions:
                if not isinstance(condition, dict):
                    raise ValueError("each condition must be an object")
                column = condition.get("column")
                operator = condition.get("operator", "=")
                param_variable = condition.get("param_variable")
                if not isinstance(column, str) or not column.strip():
                    raise ValueError("each condition column must be a non-empty string")
                if not isinstance(operator, str) or not operator.strip():
                    raise ValueError("each condition operator must be a non-empty string")
                if not isinstance(param_variable, str) or not param_variable.strip():
                    raise ValueError("each condition param_variable must be a non-empty string")
                block_parsed_conditions.append(
                    {
                        "column": column.strip(),
                        "operator": operator.strip(),
                        "param_variable": param_variable.strip(),
                    }
                )
            parsed.append({"index": index, "conditions": block_parsed_conditions})
            continue
        if not isinstance(predicate, str) or not predicate.strip():
            raise ValueError("each block must provide either non-empty sql or non-empty predicate")
        if not isinstance(argument, str) or not argument.strip():
            raise ValueError("each block argument must be a non-empty string")
        parsed.append({"index": index, "predicate": predicate, "argument": argument})
    return parsed


def _renumber_asyncpg_placeholders(sql: str, *, insertion_index: int) -> str:
    def _replace(match: re.Match[str]) -> str:
        current = int(match.group(1))
        return f"${current + 1}" if current >= insertion_index else match.group(0)

    return re.sub(r"\$(\d+)", _replace, sql)


def _infer_sql_predicate_insertion_index(*, original_sql: str) -> int:
    limit_match = re.search(r"\bLIMIT\s+\$(\d+)", original_sql, flags=re.IGNORECASE)
    if limit_match:
        return int(limit_match.group(1))
    placeholders = [int(match) for match in re.findall(r"\$(\d+)", original_sql)]
    return (max(placeholders) + 1) if placeholders else 1


def _materialize_sql_conditions(
    *,
    conditions: list[dict[str, str]],
    insertion_index: int,
) -> tuple[list[str], list[str]]:
    predicates: list[str] = []
    arguments: list[str] = []
    next_index = insertion_index
    for condition in conditions:
        predicates.append(f"{condition['column']} {condition['operator']} ${next_index}")
        arguments.append(condition["param_variable"])
        next_index += 1
    return predicates, arguments


def _extract_sql_metadata(sql: str) -> dict[str, object]:
    normalized = " ".join(sql.split())
    table_names = re.findall(r"\b[a-z_]+\.[a-z_]+\b", normalized, flags=re.IGNORECASE)
    projection = _sql_projection(normalized)
    order_match = re.search(r"\bORDER\s+BY\b.*?(?=\bLIMIT\b|$)", normalized, flags=re.IGNORECASE)
    order_by = " ".join(order_match.group(0).split()) if order_match else None
    placeholders = [int(match) for match in re.findall(r"\$(\d+)", normalized)]
    return {
        "table_names": table_names,
        "projection": projection,
        "order_by": order_by,
        "placeholders": placeholders,
    }


def _validate_mutated_sql_block(
    *,
    original_sql: str,
    mutated_sql: str,
    symbol: str,
    expected_predicate_sql: str | list[str] | None = None,
    expected_max_param: int | None = None,
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    metadata = _extract_sql_metadata(original_sql)
    mutated_metadata = _extract_sql_metadata(mutated_sql)
    table_names = [str(item) for item in cast(list[object], metadata["table_names"])]
    original_projection = str(metadata["projection"]) if metadata["projection"] is not None else None
    mutated_projection = str(mutated_metadata["projection"]) if mutated_metadata["projection"] is not None else None
    order_by = str(metadata["order_by"]) if metadata["order_by"] is not None else None
    actual_placeholders = [int(str(item)) for item in cast(list[object], mutated_metadata["placeholders"])]
    if "${" in mutated_sql:
        failures.append(
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_PRESERVATION_VIOLATED",
                "detail": "invalid_template_placeholder",
                "suggested_fix": "Let the harness materialize asyncpg placeholders; do not emit template placeholders like ${}.",
            }
        )
    if "AND AND" in mutated_sql.upper():
        failures.append(
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_PRESERVATION_VIOLATED",
                "detail": "duplicate_and_connector",
                "suggested_fix": "Return only the bare predicate; the harness owns connector insertion.",
            }
        )
    for table_name in table_names:
        if table_name not in mutated_sql:
            failures.append(
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "sql_blocks",
                    "failure": "SQL_PRESERVATION_VIOLATED",
                    "detail": f"missing_table_name:{table_name}",
                    "suggested_fix": "Preserve schema-qualified table names from the original SQL block.",
                }
            )
            break
    if original_projection and original_projection != mutated_projection:
        failures.append(
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_PRESERVATION_VIOLATED",
                "detail": "projection_changed",
                "suggested_fix": "Preserve the existing SELECT column list exactly.",
            }
        )
    if order_by and order_by not in " ".join(mutated_sql.split()):
        failures.append(
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_PRESERVATION_VIOLATED",
                "detail": "order_by_missing",
                "suggested_fix": "Preserve the existing ORDER BY clause verbatim.",
            }
        )
    if expected_predicate_sql:
        expected_predicates = (
            [expected_predicate_sql] if isinstance(expected_predicate_sql, str) else list(expected_predicate_sql)
        )
        for predicate_sql in expected_predicates:
            if predicate_sql not in mutated_sql:
                failures.append(
                    {
                        "tool": "edit_symbol",
                        "symbol": symbol,
                        "mode": "sql_blocks",
                        "failure": "SQL_PRESERVATION_VIOLATED",
                        "detail": f"predicate_missing:{predicate_sql}",
                        "suggested_fix": "Add only the requested predicate to the existing WHERE clause.",
                    }
                )
    if expected_max_param is not None:
        actual_max_param = max(actual_placeholders) if actual_placeholders else 0
        if actual_max_param != expected_max_param:
            failures.append(
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "sql_blocks",
                    "failure": "SQL_PRESERVATION_VIOLATED",
                    "detail": f"param_count_mismatch:expected${expected_max_param}:got${actual_max_param}",
                    "suggested_fix": "Preserve placeholder continuity and insert the new asyncpg parameter at the next available index.",
                }
            )
    return failures


def _inject_sql_predicate(*, original_sql: str, predicate: str, insertion_index: int) -> str:
    clean_predicate = str(predicate or "").strip()
    if not clean_predicate:
        raise ValueError("empty_predicate")
    if re.search(r"\bSELECT\b|\bFROM\b", clean_predicate, flags=re.IGNORECASE):
        raise ValueError("predicate_must_not_contain_select_or_from")
    clean_predicate = re.sub(r"^(AND|OR)\s+", "", clean_predicate, flags=re.IGNORECASE)
    predicate_sql = clean_predicate.replace("?", f"${insertion_index}")
    renumbered_sql = _renumber_asyncpg_placeholders(original_sql, insertion_index=insertion_index)
    where_match = re.search(
        r"(?is)\bWHERE\b(?P<body>.*?)(?P<suffix>\bORDER\s+BY\b.*|\bLIMIT\b.*|$)",
        renumbered_sql,
    )
    if not where_match:
        raise ValueError("missing_where_clause")
    body = where_match.group("body")
    trailing_ws = body[len(body.rstrip()) :]
    indent_matches = re.findall(r"\n([ \t]*)\S", body)
    connector_indent = indent_matches[-1] if indent_matches else " "
    spliced_body = body.rstrip() + f"\n{connector_indent}AND {predicate_sql}" + trailing_ws
    return renumbered_sql[: where_match.start("body")] + spliced_body + renumbered_sql[where_match.end("body") :]


def _validate_single_sql_block_replacement(
    *, original_sql: str, replacement_sql: str, symbol: str
) -> list[dict[str, str]]:
    if "$$" in replacement_sql:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_PRESERVATION_VIOLATED",
                "detail": "invalid_placeholder_style",
                "suggested_fix": "Use asyncpg positional placeholders like $1, $2; do not emit $$ placeholder syntax.",
            }
        ]
    for table_name in re.findall(r"\b[a-z_]+\.[a-z_]+\b", original_sql, flags=re.IGNORECASE):
        if table_name not in replacement_sql:
            return [
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "sql_blocks",
                    "failure": "SQL_PRESERVATION_VIOLATED",
                    "detail": f"missing_table_name:{table_name}",
                    "suggested_fix": "Preserve schema-qualified table names from the original SQL block.",
                }
            ]
        if (
            _sql_projection(original_sql)
            and _sql_projection(replacement_sql)
            and _sql_projection(original_sql) != _sql_projection(replacement_sql)
        ):
            return [
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "sql_blocks",
                    "failure": "SQL_PRESERVATION_VIOLATED",
                    "detail": "projection_changed",
                    "suggested_fix": "Preserve the existing SELECT column list; do not broaden to SELECT * or change the curated projection.",
                }
            ]
    missing_identifiers = sorted(_sql_where_identifiers(original_sql) - _sql_where_identifiers(replacement_sql))
    if missing_identifiers:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_PRESERVATION_VIOLATED",
                "detail": f"where_identifiers_missing:{', '.join(missing_identifiers)}",
                "suggested_fix": "Preserve existing WHERE-clause identifiers and add new filter conditions without renaming existing columns.",
            }
        ]
    return []


def _validate_sql_block_predicate(*, predicate: str, argument: str, symbol: str) -> list[dict[str, str]]:
    if re.search(r"\bSELECT\b|\bFROM\b", str(predicate or ""), flags=re.IGNORECASE):
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "INVALID_SQL_PREDICATE",
                "detail": "predicate_contains_select_or_from",
                "suggested_fix": "Return only the additive WHERE-clause predicate, for example relation_type = ?.",
            }
        ]
    if "$$" in str(predicate or ""):
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "INVALID_SQL_PREDICATE",
                "detail": "invalid_placeholder_style",
                "suggested_fix": "Use ? as the placeholder marker in sql_blocks predicate payloads; the harness will assign asyncpg indexes.",
            }
        ]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\.]*", str(argument or "").strip()):
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "INVALID_SQL_ARGUMENT",
                "detail": "argument must be a simple identifier or dotted attribute path",
                "suggested_fix": "Use a simple argument like relation_type.",
            }
        ]
    return []


def _validate_sql_block_conditions(*, conditions: list[dict[str, str]], symbol: str) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    allowed_operators = {"=", "!=", "<>", ">", ">=", "<", "<="}
    for condition in conditions:
        column = str(condition.get("column") or "").strip()
        operator = str(condition.get("operator") or "").strip()
        param_variable = str(condition.get("param_variable") or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\.]*", column):
            failures.append(
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "sql_blocks",
                    "failure": "INVALID_SQL_CONDITION",
                    "detail": f"invalid_column:{column}",
                    "suggested_fix": "Use a simple SQL column identifier such as relation_type.",
                }
            )
        if operator not in allowed_operators:
            failures.append(
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "sql_blocks",
                    "failure": "INVALID_SQL_CONDITION",
                    "detail": f"invalid_operator:{operator}",
                    "suggested_fix": "Use a supported SQL operator such as =.",
                }
            )
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\.]*", param_variable):
            failures.append(
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "sql_blocks",
                    "failure": "INVALID_SQL_ARGUMENT",
                    "detail": "param_variable must be a simple identifier or dotted attribute path",
                    "suggested_fix": "Use a simple argument like relation_type.",
                }
            )
    return failures


def _validate_sql_block_edit(*, target: Path, symbol: str, new_code: str) -> list[dict[str, str]]:
    try:
        blocks = _parse_sql_block_payload(new_code)
    except ValueError as exc:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "INVALID_SQL_BLOCK_PAYLOAD",
                "detail": str(exc),
                "suggested_fix": 'Provide JSON like {"blocks":[{"index":1,"sql":"SELECT ..."}]}.',
            }
        ]
    original_symbol = resolve_python_symbol_range(file_path=target, symbol_name=symbol)
    original_sql = _extract_sql_literals_from_function_source(original_symbol.symbol_source)
    if not original_sql:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_BLOCKS_NOT_FOUND",
                "detail": "no SQL blocks were extracted from the resolved symbol",
                "suggested_fix": "Use sql_blocks only for SQL-heavy methods with extracted SQL literals.",
            }
        ]
    failures: list[dict[str, str]] = []
    seen_indexes: set[int] = set()
    applies_to_all = False
    has_semantic_conditions = False
    for block in blocks:
        if block["index"] == "all":
            applies_to_all = True
            indexes = list(range(1, len(original_sql) + 1))
        else:
            indexes = [int(block["index"])]
        for index in indexes:
            if index in seen_indexes:
                failures.append(
                    {
                        "tool": "edit_symbol",
                        "symbol": symbol,
                        "mode": "sql_blocks",
                        "failure": "DUPLICATE_SQL_BLOCK_INDEX",
                        "detail": str(index),
                        "suggested_fix": "Provide each SQL block index at most once.",
                    }
                )
                continue
            seen_indexes.add(index)
            if index > len(original_sql):
                failures.append(
                    {
                        "tool": "edit_symbol",
                        "symbol": symbol,
                        "mode": "sql_blocks",
                        "failure": "SQL_BLOCK_INDEX_OUT_OF_RANGE",
                        "detail": str(index),
                        "suggested_fix": "Use only extracted SQL block indexes from the current symbol context.",
                    }
                )
                continue
            if "sql" in block:
                failures.extend(
                    _validate_single_sql_block_replacement(
                        original_sql=original_sql[index - 1],
                        replacement_sql=str(block["sql"]),
                        symbol=symbol,
                    )
                )
            else:
                original_block = original_sql[index - 1]
                inferred_index = _infer_sql_predicate_insertion_index(original_sql=original_block)
                if "conditions" in block:
                    has_semantic_conditions = True
                    predicates_sql, _arguments = _materialize_sql_conditions(
                        conditions=list(block["conditions"]),
                        insertion_index=inferred_index,
                    )
                    failures.extend(
                        _validate_sql_block_conditions(
                            conditions=list(block["conditions"]),
                            symbol=symbol,
                        )
                    )
                    predicate_input = " AND ".join(predicates_sql)
                    expected_predicates: str | list[str] = predicates_sql
                else:
                    predicate_sql = str(block["predicate"]).strip().replace("?", f"${inferred_index}")
                    predicates_sql = [predicate_sql]
                    predicate_input = str(block["predicate"])
                    expected_predicates = predicate_sql
                    failures.extend(
                        _validate_sql_block_predicate(
                            predicate=str(block["predicate"]),
                            argument=str(block["argument"]),
                            symbol=symbol,
                        )
                    )
                try:
                    mutated_sql = _inject_sql_predicate(
                        original_sql=original_block,
                        predicate=predicate_input,
                        insertion_index=inferred_index,
                    )
                except ValueError as exc:
                    failures.append(
                        {
                            "tool": "edit_symbol",
                            "symbol": symbol,
                            "mode": "sql_blocks",
                            "failure": "INVALID_SQL_PREDICATE",
                            "detail": str(exc),
                            "suggested_fix": "Return only a bare additive WHERE predicate and let the harness own connector insertion.",
                        }
                    )
                    continue
                    mutated_placeholders = cast(list[int], _extract_sql_metadata(mutated_sql)["placeholders"])
                    failures.extend(
                        _validate_mutated_sql_block(
                            original_sql=original_block,
                            mutated_sql=mutated_sql,
                            symbol=symbol,
                            expected_predicate_sql=expected_predicates,
                            expected_max_param=(max(mutated_placeholders) if mutated_placeholders else None),
                        )
                    )
    if has_semantic_conditions and not applies_to_all and len(seen_indexes) != len(original_sql):
        failures.append(
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_BLOCKS_INCOMPLETE",
                "detail": f"provided={len(seen_indexes)} extracted={len(original_sql)}",
                "suggested_fix": "Provide semantic SQL conditions for every extracted SQL block in the target symbol.",
            }
        )
    return failures


def _validate_signature_edit(*, target: Path, symbol: str, new_signature: str) -> list[dict[str, str]]:
    header = str(new_signature or "").strip()
    if not header:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "signature",
                "failure": "SIGNATURE_REQUIRED",
                "detail": "new_signature is required",
                "suggested_fix": "Provide a full function signature header beginning with def or async def.",
            }
        ]
    if not header.startswith(("def ", "async def ")):
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "signature",
                "failure": "INVALID_SIGNATURE_FORMAT",
                "detail": "new_signature must begin with def or async def",
                "suggested_fix": "Provide the full signature line, e.g. async def list_items(...)->dict",
            }
        ]
    signature_stub = header if header.endswith(":") else f"{header}:"
    try:
        tree = ast.parse(f"{signature_stub}\n    pass\n")
    except SyntaxError as exc:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "signature",
                "failure": "INVALID_SIGNATURE_SYNTAX",
                "detail": exc.msg,
                "suggested_fix": "Fix parameter ordering and ensure the signature is valid Python syntax.",
            }
        ]
    func = next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    if func is None:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "signature",
                "failure": "INVALID_SIGNATURE_FORMAT",
                "detail": "signature did not parse into a function definition",
                "suggested_fix": "Provide exactly one function signature line.",
            }
        ]
    expected_name = str(symbol).split(".")[-1]
    if func.name != expected_name:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "signature",
                "failure": "SIGNATURE_NAME_MISMATCH",
                "detail": f"expected function name {expected_name!r}, got {func.name!r}",
                "suggested_fix": "Keep the existing function name and only change parameters or return annotation.",
            }
        ]
    original_symbol = resolve_python_symbol_range(file_path=target, symbol_name=symbol)
    original_tree = ast.parse(textwrap.dedent(original_symbol.symbol_source))
    original_func = next(
        (node for node in original_tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
        None,
    )
    if original_func is not None:
        if isinstance(original_func, ast.AsyncFunctionDef) != isinstance(func, ast.AsyncFunctionDef):
            return [
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "signature",
                    "failure": "MUTATION_INVALID_FUNCTION_SHAPE",
                    "detail": "async_mismatch",
                    "suggested_fix": "Preserve async def when editing an async method signature.",
                }
            ]
        if "." in symbol:
            original_arg = original_func.args.args[0].arg if original_func.args.args else None
            replacement_arg = func.args.args[0].arg if func.args.args else None
            if original_arg == "self" and replacement_arg != "self":
                return [
                    {
                        "tool": "edit_symbol",
                        "symbol": symbol,
                        "mode": "signature",
                        "failure": "MUTATION_INVALID_FUNCTION_SHAPE",
                        "detail": "method_receiver_mismatch",
                        "suggested_fix": "Preserve the method receiver argument when editing a class method signature.",
                    }
                ]
    return []


def _validate_body_insert_edit(
    *,
    target: Path,
    symbol: str,
    anchor_pattern: str,
    position: str,
    new_code: str,
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    if position not in {"before", "after"}:
        failures.append(
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "body_insert",
                "failure": "INVALID_POSITION",
                "detail": f"unsupported position={position!r}",
                "suggested_fix": "Use position='before' or position='after'.",
            }
        )
    if not anchor_pattern.strip():
        failures.append(
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "body_insert",
                "failure": "ANCHOR_REQUIRED",
                "detail": "anchor_pattern is required",
                "suggested_fix": "Provide an anchor_pattern that exists inside the target function body.",
            }
        )
    if not str(new_code or "").strip():
        failures.append(
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "body_insert",
                "failure": "NEW_CODE_REQUIRED",
                "detail": "new_code is required",
                "suggested_fix": "Provide one or more valid Python statements to insert.",
            }
        )
    if failures:
        return failures
    try:
        _parse_insert_statements(new_code)
    except Exception as exc:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "body_insert",
                "failure": "INVALID_NEW_CODE_SYNTAX",
                "detail": str(exc),
                "suggested_fix": "Provide valid Python statements only.",
            }
        ]
    if not _function_body_contains_anchor(target=target, qualified_name=symbol, anchor_pattern=anchor_pattern):
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "body_insert",
                "failure": "ANCHOR_NOT_FOUND",
                "detail": f"anchor_pattern {anchor_pattern!r} was not found in the resolved symbol body",
                "suggested_fix": (
                    "body_insert with anchor_pattern is a legacy compatibility shim. "
                    "Do not retry with a different anchor. "
                    "Instead: use replace_in_function with args.function_name and the complete updated "
                    "args.replacement function body, or use write_file with the full updated file content. "
                    "Read the current function body first with read_range or read_file."
                ),
                "shim_event": "anchor_not_found_compat",
            }
        ]
    unresolved_names = _find_unresolved_insert_names(target=target, symbol=symbol, new_code=new_code)
    if unresolved_names:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "body_insert",
                "failure": "UNDEFINED_LOCAL_REFERENCE",
                "detail": ", ".join(unresolved_names),
                "suggested_fix": "Reference only existing parameters/locals, or define new locals within the inserted code block before using them.",
            }
        ]
    return []


def _validate_full_symbol_edit(*, target: Path, symbol: str, replacement: str) -> list[dict[str, str]]:
    candidate = str(replacement or "").strip()
    if not candidate:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "full",
                "failure": "FULL_REPLACEMENT_REQUIRED",
                "detail": "new_code is required for mode='full'",
                "suggested_fix": "Provide the full updated function or method source in new_code.",
            }
        ]
    if not candidate.startswith(("def ", "async def ")):
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "full",
                "failure": "INVALID_FULL_REPLACEMENT_FORMAT",
                "detail": "replacement must begin with def or async def",
                "suggested_fix": "Provide the full replacement function source beginning with def or async def.",
            }
        ]
    original_symbol = resolve_python_symbol_range(file_path=target, symbol_name=symbol)
    try:
        _normalize_function_replacement(
            replacement=candidate,
            base_indent=original_symbol.def_col,
            original_source=original_symbol.symbol_source,
            qualified_name=original_symbol.qualified_name,
        )
    except ValueError as exc:
        detail = str(exc).replace("MUTATION_INVALID_FUNCTION_REPLACEMENT ", "", 1).strip()
        failure = "MUTATION_INVALID_FUNCTION_REPLACEMENT"
        if detail.startswith("MUTATION_INVALID_FUNCTION_SHAPE"):
            failure = "MUTATION_INVALID_FUNCTION_SHAPE"
            detail = detail.replace("MUTATION_INVALID_FUNCTION_SHAPE", "", 1).strip() or "invalid_shape"
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "full",
                "failure": failure,
                "detail": detail or "invalid_replacement",
                "suggested_fix": "Preserve the original async/method shape and provide a complete valid replacement body.",
            }
        ]
    sql_failures = _validate_sql_preservation(
        original_source=original_symbol.symbol_source,
        replacement_source=textwrap.dedent(candidate),
        symbol=symbol,
    )
    if sql_failures:
        return sql_failures
    scope_failures = _validate_full_symbol_scope_preservation(
        original_source=original_symbol.symbol_source,
        replacement_source=textwrap.dedent(candidate),
        symbol=symbol,
    )
    if scope_failures:
        return scope_failures
    return []


def _parse_refactor_symbol_payload(new_code: str) -> dict[str, Any]:
    try:
        payload = json.loads(str(new_code or "").strip())
    except Exception as exc:
        raise ValueError(f"invalid_json:{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("payload_must_be_object")
    return payload


def _build_refactor_symbol_replacement(*, target: Path, symbol: str, new_code: str) -> str:
    payload = _parse_refactor_symbol_payload(new_code)
    pattern = str(payload.get("pattern") or "").strip()
    if pattern != "optional_filter_asyncpg_query":
        raise ValueError("unsupported_pattern")
    parameter_name = str(payload.get("parameter_name") or "").strip()
    column_name = str(payload.get("column_name") or "").strip()
    if not parameter_name or not column_name:
        raise ValueError("parameter_name_and_column_name_required")
    parameter_type = str(payload.get("parameter_type") or "str | None").strip() or "str | None"
    default_value = str(payload.get("default") or "None").strip() or "None"
    sql_variables = [str(item).strip() for item in list(payload.get("sql_variable_names") or []) if str(item).strip()]
    params_variables = [
        str(item).strip() for item in list(payload.get("params_variable_names") or []) if str(item).strip()
    ]
    if len(sql_variables) < 1 or len(params_variables) < 1:
        raise ValueError("sql_variable_names_and_params_variable_names_required")

    symbol_range = resolve_python_symbol_range(file_path=target, symbol_name=symbol)
    original_source = textwrap.dedent(symbol_range.symbol_source).rstrip() + "\n"
    tree = ast.parse(original_source)
    func = next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    if func is None:
        raise ValueError("target_function_missing")
    if not isinstance(func, ast.AsyncFunctionDef):
        raise ValueError("async_function_required")
    if "." in symbol and (not func.args.args or func.args.args[0].arg != "self"):
        raise ValueError("method_receiver_required")
    if any(arg.arg == parameter_name for arg in func.args.args):
        raise ValueError("parameter_already_present")

    lines = original_source.splitlines()
    signature_index = next(
        (idx for idx, line in enumerate(lines) if line.lstrip().startswith(("def ", "async def "))), None
    )
    if signature_index is None:
        raise ValueError("signature_line_missing")
    signature_line = lines[signature_index]
    if ") ->" in signature_line:
        replacement_signature = signature_line.replace(
            ") ->", f", {parameter_name}: {parameter_type} = {default_value}) ->", 1
        )
    elif signature_line.rstrip().endswith("):"):
        replacement_signature = (
            signature_line.rstrip()[:-2] + f", {parameter_name}: {parameter_type} = {default_value}):"
        )
    else:
        raise ValueError("unsupported_signature_layout")
    lines[signature_index] = replacement_signature

    assignment_indexes = [
        idx
        for idx, line in enumerate(lines)
        if any(re.match(rf"^\s*{re.escape(name)}\s*=", line) for name in [*sql_variables, *params_variables])
    ]
    if not assignment_indexes:
        raise ValueError("assignment_targets_missing")
    insert_after = max(assignment_indexes)
    body_indent_width = len(lines[insert_after]) - len(lines[insert_after].lstrip(" "))
    indent = " " * body_indent_width
    inner = indent + " " * 4
    refactor_lines = [
        f"{indent}if {parameter_name} is not None:",
    ]
    for sql_var, params_var in zip(sql_variables, params_variables, strict=False):
        refactor_lines.extend(
            [
                f'{inner}{sql_var} = {sql_var} + f" AND {column_name} = ${{len({params_var}) + 1}}"',
                f"{inner}{params_var}.append({parameter_name})",
            ]
        )
    lines[insert_after + 1 : insert_after + 1] = refactor_lines
    replacement = "\n".join(lines) + "\n"
    return _normalize_function_replacement(
        replacement=replacement,
        base_indent=symbol_range.def_col,
        original_source=symbol_range.symbol_source,
        qualified_name=symbol_range.qualified_name,
    )


def _validate_refactor_symbol_edit(*, target: Path, symbol: str, new_code: str) -> list[dict[str, str]]:
    if not str(new_code or "").strip():
        return [
            _mutation_failure_payload(
                tool="edit_symbol",
                symbol=symbol,
                mode="refactor_symbol",
                failure_code="NEW_CODE_REQUIRED",
                detail="new_code payload is required",
                suggested_fix="Provide a JSON payload describing the supported refactor pattern.",
            )
        ]
    try:
        replacement = _build_refactor_symbol_replacement(target=target, symbol=symbol, new_code=new_code)
    except ValueError as exc:
        return [
            _mutation_failure_payload(
                tool="edit_symbol",
                symbol=symbol,
                mode="refactor_symbol",
                failure_code="INVALID_REFACTOR_SYMBOL_PAYLOAD",
                detail=str(exc),
                suggested_fix="Use pattern='optional_filter_asyncpg_query' with parameter_name, column_name, sql_variable_names, and params_variable_names.",
            )
        ]
    return _validate_full_symbol_edit(target=target, symbol=symbol, replacement=replacement)


def _extract_sql_literals_from_function_source(source: str) -> list[str]:
    try:
        tree = ast.parse(textwrap.dedent(str(source or "").strip()))
    except SyntaxError:
        return []
    sql_blocks: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = str(node.value)
            normalized = " ".join(text.split())
            upper = normalized.upper()
            if "SELECT " in upper and " FROM " in upper:
                sql_blocks.append(normalized)
    return sql_blocks


def _render_simple_string_like(*, original_literal: str, new_value: str) -> cst.SimpleString:
    match = re.match(r"^(?P<prefix>[rRuUbBfF]*)(?P<quote>'''|\"\"\"|'|\")", original_literal)
    if not match:
        return cst.SimpleString(repr(new_value))
    prefix = match.group("prefix")
    quote = match.group("quote")
    if len(quote) == 3:
        rendered = new_value.replace("\\", "\\\\").replace(quote, "\\" + quote)
        return cst.SimpleString(f"{prefix}{quote}{rendered}{quote}")
    rendered = new_value.replace("\\", "\\\\").replace(quote, "\\" + quote).replace("\n", "\\n")
    return cst.SimpleString(f"{prefix}{quote}{rendered}{quote}")


def _sql_projection(sql: str) -> str | None:
    match = re.search(r"SELECT\s+(.*?)\s+FROM\s", sql, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return " ".join(match.group(1).split())


def _sql_where_identifiers(sql: str) -> set[str]:
    match = re.search(r"\bWHERE\b(.*?)(?:\bORDER\s+BY\b|\bLIMIT\b|$)", sql, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return set()
    where_text = match.group(1)
    return {
        token
        for token in re.findall(r"\b[a-z_][a-z0-9_]*\b", where_text, flags=re.IGNORECASE)
        if token.lower() not in {"where", "and", "or", "not", "null", "is", "desc", "asc"}
    }


def _validate_sql_preservation(*, original_source: str, replacement_source: str, symbol: str) -> list[dict[str, str]]:
    original_sql = _extract_sql_literals_from_function_source(original_source)
    if not original_sql:
        return []
    replacement_sql = _extract_sql_literals_from_function_source(replacement_source)
    if len(replacement_sql) < len(original_sql):
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "full",
                "failure": "SQL_PRESERVATION_VIOLATED",
                "detail": "missing_sql_blocks",
                "suggested_fix": "Preserve the existing SQL blocks verbatim and only add the requested filter condition and parameter threading.",
            }
        ]
    for index, original_block in enumerate(original_sql):
        replacement_block = replacement_sql[index]
        if "$$" in replacement_block:
            return [
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "full",
                    "failure": "SQL_PRESERVATION_VIOLATED",
                    "detail": "invalid_placeholder_style",
                    "suggested_fix": "Use asyncpg positional placeholders like $1, $2; do not emit $$ placeholder syntax.",
                }
            ]
        for table_name in re.findall(r"\b[a-z_]+\.[a-z_]+\b", original_block, flags=re.IGNORECASE):
            if table_name not in replacement_block:
                return [
                    {
                        "tool": "edit_symbol",
                        "symbol": symbol,
                        "mode": "full",
                        "failure": "SQL_PRESERVATION_VIOLATED",
                        "detail": f"missing_table_name:{table_name}",
                        "suggested_fix": "Preserve schema-qualified table names from the original SQL block.",
                    }
                ]
        original_projection = _sql_projection(original_block)
        replacement_projection = _sql_projection(replacement_block)
        if original_projection and replacement_projection and original_projection != replacement_projection:
            return [
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "full",
                    "failure": "SQL_PRESERVATION_VIOLATED",
                    "detail": "projection_changed",
                    "suggested_fix": "Preserve the existing SELECT column list; do not broaden to SELECT * or change the curated projection.",
                }
            ]
        missing_identifiers = sorted(_sql_where_identifiers(original_block) - _sql_where_identifiers(replacement_block))
        if missing_identifiers:
            return [
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "full",
                    "failure": "SQL_PRESERVATION_VIOLATED",
                    "detail": f"where_identifiers_missing:{', '.join(missing_identifiers)}",
                    "suggested_fix": "Preserve existing WHERE-clause identifiers and add new filter conditions without renaming existing columns.",
                }
            ]
    return []


def _find_unresolved_insert_names(*, target: Path, symbol: str, new_code: str) -> list[str]:
    original_symbol = resolve_python_symbol_range(file_path=target, symbol_name=symbol)
    original_tree = ast.parse(textwrap.dedent(original_symbol.symbol_source))
    original_func = next(
        (node for node in original_tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
        None,
    )
    if original_func is None:
        return []

    inserted_tree = ast.parse(textwrap.dedent(str(new_code or "").strip()))
    builtins_allowed = set(dir(builtins)) | {"True", "False", "None"}

    def _collect_original_allowed(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
        allowed: set[str] = {arg.arg for arg in func.args.args}
        allowed.update(arg.arg for arg in func.args.kwonlyargs)
        if func.args.vararg is not None:
            allowed.add(func.args.vararg.arg)
        if func.args.kwarg is not None:
            allowed.add(func.args.kwarg.arg)

        class _OriginalAssignedNameCollector(ast.NodeVisitor):
            def visit_Name(self, node: ast.Name) -> None:
                if isinstance(node.ctx, ast.Store):
                    allowed.add(node.id)

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                allowed.add(node.name)
                self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                allowed.add(node.name)
                self.generic_visit(node)

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                allowed.add(node.name)
                self.generic_visit(node)

        _OriginalAssignedNameCollector().visit(func)
        return allowed

    class _StmtNameCollector(ast.NodeVisitor):
        def __init__(self) -> None:
            self.loads: set[str] = set()
            self.stores: set[str] = set()

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Load):
                self.loads.add(node.id)
            elif isinstance(node.ctx, ast.Store):
                self.stores.add(node.id)

        def visit_arg(self, node: ast.arg) -> None:
            self.stores.add(node.arg)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stores.add(node.name)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.stores.add(node.name)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.stores.add(node.name)

    allowed = _collect_original_allowed(original_func) | builtins_allowed
    unresolved: list[str] = []
    for stmt in inserted_tree.body:
        collector = _StmtNameCollector()
        collector.visit(stmt)
        for name in sorted(collector.loads):
            if name not in allowed and name not in unresolved:
                unresolved.append(name)
        allowed.update(collector.stores)
    return unresolved


def _extract_function_node(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    try:
        tree = ast.parse(textwrap.dedent(str(source or "").strip()))
    except SyntaxError:
        return None
    return next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None)


def _function_parameter_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    names = [arg.arg for arg in func.args.posonlyargs]
    names.extend(arg.arg for arg in func.args.args)
    if func.args.vararg is not None:
        names.append(f"*{func.args.vararg.arg}")
    names.extend(arg.arg for arg in func.args.kwonlyargs)
    if func.args.kwarg is not None:
        names.append(f"**{func.args.kwarg.arg}")
    return names


def _collect_local_assigned_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    assigned: set[str] = set()

    class _AssignedNameCollector(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Store):
                assigned.add(node.id)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            assigned.add(node.name)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            assigned.add(node.name)
            self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            assigned.add(node.name)
            self.generic_visit(node)

    _AssignedNameCollector().visit(func)
    for arg_name in _function_parameter_names(func):
        assigned.discard(arg_name.lstrip("*"))
    assigned.discard(func.name)
    return assigned


def _control_flow_shape_counts(func: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, int]:
    counts = {
        "try": 0,
        "finally": 0,
        "except": 0,
        "with": 0,
        "for": 0,
        "while": 0,
        "if": 0,
    }
    for node in ast.walk(func):
        if isinstance(node, ast.Try):
            counts["try"] += 1
            if node.finalbody:
                counts["finally"] += 1
            counts["except"] += len(node.handlers)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            counts["with"] += 1
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            counts["for"] += 1
        elif isinstance(node, ast.While):
            counts["while"] += 1
        elif isinstance(node, ast.If):
            counts["if"] += 1
    return counts


def _validate_full_symbol_scope_preservation(
    *, original_source: str, replacement_source: str, symbol: str
) -> list[dict[str, str]]:
    expected_name = str(symbol or "").split(".")[-1]
    original_func = _extract_function_node(original_source)
    replacement_func = _extract_function_node(replacement_source)
    if original_func is None or replacement_func is None:
        return []
    if replacement_func.name != expected_name:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "full",
                "failure": "OUT_OF_SCOPE_MUTATION",
                "detail": f"function_name_changed:{replacement_func.name}",
                "suggested_fix": "Keep the existing function name and only mutate the confirmed symbol body.",
            }
        ]
    original_parameters = _function_parameter_names(original_func)
    replacement_parameters = _function_parameter_names(replacement_func)
    if replacement_parameters[: len(original_parameters)] != original_parameters:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "full",
                "failure": "OUT_OF_SCOPE_MUTATION",
                "detail": "parameter_order_changed",
                "suggested_fix": "Preserve the original parameter list order and only append explicitly requested parameters.",
            }
        ]
    missing_locals = sorted(
        _collect_local_assigned_names(original_func) - _collect_local_assigned_names(replacement_func)
    )
    if missing_locals:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "full",
                "failure": "OUT_OF_SCOPE_MUTATION",
                "detail": f"local_names_missing:{', '.join(missing_locals)}",
                "suggested_fix": "Preserve existing local variable names inside the confirmed symbol and apply additive edits instead of renaming them.",
            }
        ]
    original_shape = _control_flow_shape_counts(original_func)
    replacement_shape = _control_flow_shape_counts(replacement_func)
    missing_shape = [
        name for name, count in original_shape.items() if count > 0 and replacement_shape.get(name, 0) < count
    ]
    if missing_shape:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "full",
                "failure": "OUT_OF_SCOPE_MUTATION",
                "detail": f"control_flow_missing:{', '.join(missing_shape)}",
                "suggested_fix": "Preserve existing try/finally and other control-flow scaffolding; only make additive edits within that structure.",
            }
        ]
    return []


def _callable_name(node: cst.BaseExpression) -> str:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        base = _callable_name(node.value)
        return f"{base}.{node.attr.value}" if base else node.attr.value
    return ""


def _callee_matches(node: cst.BaseExpression, expected: str) -> bool:
    actual = _callable_name(node).strip()
    normalized_actual = actual.replace(" ", "")
    normalized_expected = str(expected or "").strip().replace(" ", "")
    if not normalized_actual or not normalized_expected:
        return False
    if normalized_actual == normalized_expected:
        return True
    return normalized_actual.split(".")[-1] == normalized_expected.split(".")[-1]


def _validate_update_call_kwarg(
    *,
    target: Path,
    symbol: str,
    callee: str,
    kwarg: str,
    new_value: str,
) -> list[dict[str, str]]:
    normalized_callee = str(callee or "").strip()
    normalized_kwarg = str(kwarg or "").strip()
    normalized_value = str(new_value or "").strip()
    failures: list[dict[str, str]] = []
    if not normalized_callee:
        failures.append(
            {
                "tool": "update_call_kwarg",
                "symbol": symbol,
                "failure": "CALLEE_REQUIRED",
                "detail": "callee is required",
                "suggested_fix": "Provide the target call expression, for example self.list_entities.",
            }
        )
    if not normalized_kwarg:
        failures.append(
            {
                "tool": "update_call_kwarg",
                "symbol": symbol,
                "failure": "KWARG_REQUIRED",
                "detail": "kwarg is required",
                "suggested_fix": "Provide the keyword argument name to update.",
            }
        )
    elif not normalized_kwarg.isidentifier():
        failures.append(
            {
                "tool": "update_call_kwarg",
                "symbol": symbol,
                "failure": "INVALID_KWARG_NAME",
                "detail": f"kwarg={normalized_kwarg!r}",
                "suggested_fix": "Use a valid Python identifier for the kwarg name.",
            }
        )
    if not normalized_value:
        failures.append(
            {
                "tool": "update_call_kwarg",
                "symbol": symbol,
                "failure": "NEW_VALUE_REQUIRED",
                "detail": "new_value is required",
                "suggested_fix": "Provide the replacement Python expression for the kwarg value.",
            }
        )
    else:
        try:
            cst.parse_expression(normalized_value)
        except Exception as exc:
            failures.append(
                {
                    "tool": "update_call_kwarg",
                    "symbol": symbol,
                    "failure": "INVALID_NEW_VALUE_SYNTAX",
                    "detail": str(exc),
                    "suggested_fix": "Provide a valid Python expression for new_value.",
                }
            )
    if failures:
        return failures

    module = cst.parse_module(target.read_text(encoding="utf-8"))
    found_callee = False
    found_kwarg = False

    class _Visitor(cst.CSTVisitor):
        def __init__(self) -> None:
            self._class_stack: list[str] = []
            self._inside_target = False

        def visit_ClassDef(self, node: cst.ClassDef) -> bool:
            self._class_stack.append(node.name.value)
            return True

        def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
            self._class_stack.pop()

        def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
            current_name = ".".join([*self._class_stack, node.name.value]).strip(".")
            self._inside_target = current_name == symbol
            return True

        def leave_FunctionDef(self, original_node: cst.FunctionDef) -> None:
            self._inside_target = False

        def visit_Call(self, node: cst.Call) -> bool:
            nonlocal found_callee, found_kwarg
            if not self._inside_target or found_kwarg:
                return True
            if not _callee_matches(node.func, normalized_callee):
                return True
            found_callee = True
            for arg in node.args:
                if arg.keyword and arg.keyword.value == normalized_kwarg:
                    found_kwarg = True
                    break
            return True

    module.visit(_Visitor())
    if not found_callee:
        return [
            {
                "tool": "update_call_kwarg",
                "symbol": symbol,
                "failure": "CALL_NOT_FOUND",
                "detail": f"callee={normalized_callee!r}",
                "suggested_fix": "Confirm the callee exists inside the grounded symbol body.",
            }
        ]
    if not found_kwarg:
        return [
            {
                "tool": "update_call_kwarg",
                "symbol": symbol,
                "failure": "CALL_KWARG_NOT_FOUND",
                "detail": f"callee={normalized_callee!r} kwarg={normalized_kwarg!r}",
                "suggested_fix": "Confirm the matching call already has the target keyword argument.",
            }
        ]
    return []


def _dry_run_update_call_kwarg(
    *,
    target: Path,
    symbol: str,
    callee: str,
    kwarg: str,
    new_value: str,
) -> list[dict[str, str]]:
    return _validate_update_call_kwarg(
        target=target,
        symbol=symbol,
        callee=callee,
        kwarg=kwarg,
        new_value=new_value,
    )


def _normalize_signature_parameter_kind(value: str | None) -> str:
    normalized = str(value or "").strip().lower() or "auto"
    if normalized == "optional":
        normalized = "keyword_only"
    elif normalized == "keyword":
        normalized = "keyword_only"
    if normalized not in {"auto", "keyword_only", "positional_or_keyword"}:
        raise ValueError(f"unsupported_parameter_kind:{normalized}")
    return normalized


def _parse_signature_extension_stub(
    *,
    parameter_name: str,
    parameter_type: str,
    default_value: str,
    parameter_kind: str,
) -> tuple[cst.Param, cst.ParamStar | cst.Param | cst.MaybeSentinel]:
    normalized_kind = _normalize_signature_parameter_kind(parameter_kind)
    if normalized_kind == "keyword_only":
        parsed = cst.parse_module(
            "def _signature_stub(\n"
            "    *,\n"
            f"    {parameter_name}: {parameter_type} = {default_value},\n"
            "):\n"
            "    pass\n"
        )
        func = parsed.body[0]
        assert isinstance(func, cst.FunctionDef)
        return func.params.kwonly_params[0], func.params.star_arg
    parsed = cst.parse_module(f"def _signature_stub({parameter_name}: {parameter_type} = {default_value}):\n    pass\n")
    func = parsed.body[0]
    assert isinstance(func, cst.FunctionDef)
    return func.params.params[0], cst.MaybeSentinel.DEFAULT


def _collect_cst_parameter_names(params: cst.Parameters) -> set[str]:
    names: set[str] = set()
    for collection in (params.posonly_params, params.params, params.kwonly_params):
        for param in collection:
            names.add(param.name.value)
    if isinstance(params.star_arg, cst.Param):
        names.add(params.star_arg.name.value)
    if params.star_kwarg is not None:
        names.add(params.star_kwarg.name.value)
    return names


def _validate_extend_python_signature(
    *,
    target: Path,
    symbol: str,
    parameter_name: str,
    parameter_type: str,
    default_value: str,
    parameter_kind: str,
) -> list[dict[str, str]]:
    normalized_name = str(parameter_name or "").strip()
    normalized_type = str(parameter_type or "").strip()
    normalized_default = str(default_value or "").strip()
    failures: list[dict[str, str]] = []
    try:
        normalized_kind = _normalize_signature_parameter_kind(parameter_kind)
    except ValueError as exc:
        return [
            _mutation_failure_payload(
                tool="extend_python_signature",
                symbol=symbol,
                failure_code="INVALID_PARAMETER_KIND",
                detail=str(exc),
                suggested_fix="Use parameter_kind='auto', 'keyword_only', or 'positional_or_keyword'.",
            )
        ]
    if not normalized_name:
        failures.append(
            _mutation_failure_payload(
                tool="extend_python_signature",
                symbol=symbol,
                failure_code="PARAMETER_NAME_REQUIRED",
                detail="parameter_name is required",
                suggested_fix="Provide the new optional parameter name.",
            )
        )
    elif not normalized_name.isidentifier():
        failures.append(
            _mutation_failure_payload(
                tool="extend_python_signature",
                symbol=symbol,
                failure_code="INVALID_PARAMETER_NAME",
                detail=f"parameter_name={normalized_name!r}",
                suggested_fix="Use a valid Python identifier for parameter_name.",
            )
        )
    if not normalized_type:
        failures.append(
            _mutation_failure_payload(
                tool="extend_python_signature",
                symbol=symbol,
                failure_code="PARAMETER_TYPE_REQUIRED",
                detail="parameter_type is required",
                suggested_fix="Provide a non-empty Python annotation string for parameter_type.",
            )
        )
    if not normalized_default:
        failures.append(
            _mutation_failure_payload(
                tool="extend_python_signature",
                symbol=symbol,
                failure_code="DEFAULT_VALUE_REQUIRED",
                detail="default_value is required",
                suggested_fix="Provide a valid Python expression for default_value.",
            )
        )
    if failures:
        return failures
    try:
        param_node, _ = _parse_signature_extension_stub(
            parameter_name=normalized_name,
            parameter_type=normalized_type,
            default_value=normalized_default,
            parameter_kind=normalized_kind,
        )
    except Exception as exc:
        return [
            _mutation_failure_payload(
                tool="extend_python_signature",
                symbol=symbol,
                failure_code="INVALID_SIGNATURE_EXTENSION_SYNTAX",
                detail=str(exc),
                suggested_fix="Provide a valid parameter_type and default_value expression for the new optional parameter.",
            )
        ]
    module = cst.parse_module(target.read_text(encoding="utf-8"))
    target_params: cst.Parameters | None = None

    class _Visitor(cst.CSTVisitor):
        def __init__(self) -> None:
            self._class_stack: list[str] = []

        def visit_ClassDef(self, node: cst.ClassDef) -> bool:
            self._class_stack.append(node.name.value)
            return True

        def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
            self._class_stack.pop()

        def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
            nonlocal target_params
            current_name = ".".join([*self._class_stack, node.name.value]).strip(".")
            if current_name == symbol:
                target_params = node.params
            return True

    module.visit(_Visitor())
    if target_params is None:
        return [
            _mutation_failure_payload(
                tool="extend_python_signature",
                symbol=symbol,
                failure_code="PYTHON_SYMBOL_NOT_FOUND",
                detail=f"symbol={symbol}",
                suggested_fix="Confirm the fully qualified symbol name with confirm_anchor before extending the signature.",
            )
        ]
    if param_node.name.value in _collect_cst_parameter_names(target_params):
        return [
            _mutation_failure_payload(
                tool="extend_python_signature",
                symbol=symbol,
                failure_code="PARAMETER_ALREADY_PRESENT",
                detail=f"parameter_name={param_node.name.value}",
                suggested_fix="Append only a new optional parameter that does not already exist on the target function.",
            )
        ]
    return []


def _dry_run_extend_python_signature(
    *,
    target: Path,
    symbol: str,
    parameter_name: str,
    parameter_type: str,
    default_value: str,
    parameter_kind: str,
) -> list[dict[str, str]]:
    return _validate_extend_python_signature(
        target=target,
        symbol=symbol,
        parameter_name=parameter_name,
        parameter_type=parameter_type,
        default_value=default_value,
        parameter_kind=parameter_kind,
    )


def _normalize_function_replacement(
    *,
    replacement: str,
    base_indent: int,
    original_source: str,
    qualified_name: str,
    relative_path: str | None = None,
) -> str:
    candidate = textwrap.dedent(str(replacement or "")).strip()
    if not candidate.startswith(("def ", "async def ")):
        raise ValueError("MUTATION_INVALID_FUNCTION_REPLACEMENT missing_function_signature")
    _tree, func = _single_top_level_function(candidate=candidate, qualified_name=qualified_name)
    if not func.body or all(
        isinstance(node, ast.Pass)
        or (
            isinstance(node, ast.Expr)
            and isinstance(getattr(node, "value", None), ast.Constant)
            and getattr(node.value, "value", None) is Ellipsis
        )
        for node in func.body
    ):
        raise ValueError("MUTATION_FRAGMENT_REJECTED function body required")
    original_tree = ast.parse(textwrap.dedent(str(original_source or "")).strip())
    original_func = next(
        (node for node in original_tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None
    )
    if original_func is not None:
        if isinstance(original_func, ast.AsyncFunctionDef) != isinstance(func, ast.AsyncFunctionDef):
            _log_replace_function_shape_violation(
                relative_path=relative_path,
                qualified_name=qualified_name,
                original_source=original_source,
                replacement=replacement,
                original_func=original_func,
                replacement_func=func,
                failure_detail="MUTATION_INVALID_FUNCTION_SHAPE async_mismatch",
            )
            raise ValueError("MUTATION_INVALID_FUNCTION_SHAPE async_mismatch")
        if "." in qualified_name:
            original_arg = original_func.args.args[0].arg if original_func.args.args else None
            replacement_arg = func.args.args[0].arg if func.args.args else None
            if original_arg != replacement_arg:
                raise ValueError("MUTATION_INVALID_FUNCTION_SHAPE method_receiver_mismatch")
    prefix = " " * max(0, int(base_indent))
    lines = candidate.splitlines()
    rendered = [(f"{prefix}{line}" if line.strip() else line) for line in lines]
    return "\n".join(rendered) + "\n"


def _parse_signature_function_node(signature_text: str) -> cst.FunctionDef:
    header = str(signature_text or "").strip()
    stub = header if header.endswith(":") else f"{header}:"
    parsed = cst.parse_module(f"{stub}\n    pass\n")
    for statement in parsed.body:
        if isinstance(statement, cst.FunctionDef):
            return statement
    raise ValueError("INVALID_SIGNATURE_FORMAT")


def _extend_python_signature_with_libcst(
    *,
    target: Path,
    qualified_name: str,
    parameter_name: str,
    parameter_type: str,
    default_value: str,
    parameter_kind: str,
) -> None:
    module = cst.parse_module(target.read_text(encoding="utf-8"))
    normalized_kind = _normalize_signature_parameter_kind(parameter_kind)
    explicit_kind = "keyword_only" if normalized_kind == "keyword_only" else "positional_or_keyword"
    parsed_param, parsed_star_arg = _parse_signature_extension_stub(
        parameter_name=parameter_name,
        parameter_type=parameter_type,
        default_value=default_value,
        parameter_kind=explicit_kind,
    )

    class _Transformer(cst.CSTTransformer):
        def __init__(self) -> None:
            self._class_stack: list[str] = []
            self.replaced = False

        def visit_ClassDef(self, node: cst.ClassDef) -> bool:
            self._class_stack.append(node.name.value)
            return True

        def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.CSTNode:
            self._class_stack.pop()
            return updated_node

        def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.CSTNode:
            current_name = ".".join([*self._class_stack, original_node.name.value]).strip(".")
            if current_name != qualified_name:
                return updated_node
            params = updated_node.params
            existing_names = _collect_cst_parameter_names(params)
            if parameter_name in existing_names:
                raise ValueError(f"PARAMETER_ALREADY_PRESENT parameter_name={parameter_name}")
            target_kind = normalized_kind
            if target_kind == "auto":
                target_kind = (
                    "keyword_only"
                    if params.kwonly_params
                    or params.star_kwarg is not None
                    or params.star_arg is not cst.MaybeSentinel.DEFAULT
                    else "positional_or_keyword"
                )
            if target_kind == "keyword_only":
                star_arg = params.star_arg
                if star_arg is cst.MaybeSentinel.DEFAULT:
                    star_arg = parsed_star_arg
                new_params = params.with_changes(
                    star_arg=star_arg,
                    kwonly_params=[*params.kwonly_params, parsed_param],
                )
            else:
                new_params = params.with_changes(params=[*params.params, parsed_param])
            self.replaced = True
            return updated_node.with_changes(params=new_params)

    transformer = _Transformer()
    updated = module.visit(transformer)
    if not transformer.replaced:
        raise LookupError(f"PYTHON_SYMBOL_NOT_FOUND symbol={qualified_name}")
    target.write_text(updated.code, encoding="utf-8")


def _parse_insert_statements(new_code: str) -> list[cst.BaseStatement]:
    parsed = cst.parse_module(textwrap.dedent(str(new_code or "")).strip() + "\n")
    statements = list(parsed.body)
    if not statements:
        raise ValueError("NEW_CODE_REQUIRED")
    return statements


def _function_body_contains_anchor(*, target: Path, qualified_name: str, anchor_pattern: str) -> bool:
    module = cst.parse_module(target.read_text(encoding="utf-8"))
    renderer = cst.Module(body=[])
    normalized_anchor = " ".join(str(anchor_pattern or "").split())
    found = False

    class _AnchorVisitor(cst.CSTVisitor):
        def __init__(self) -> None:
            self._class_stack: list[str] = []

        def visit_ClassDef(self, node: cst.ClassDef) -> bool:
            self._class_stack.append(node.name.value)
            return True

        def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
            self._class_stack.pop()

        def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
            nonlocal found
            current_name = ".".join([*self._class_stack, node.name.value]).strip(".")
            if current_name != qualified_name:
                return True
            block = node.body
            if isinstance(block, cst.IndentedBlock):
                for statement in block.body:
                    rendered = " ".join(renderer.code_for_node(statement).split())
                    if normalized_anchor and normalized_anchor in rendered:
                        found = True
                        break
            return True

    module.visit(_AnchorVisitor())
    return found


def _edit_symbol_with_libcst(
    *,
    target: Path,
    qualified_name: str,
    mode: str,
    new_signature: str,
    anchor_pattern: str,
    position: str,
    new_code: str,
) -> None:
    module = cst.parse_module(target.read_text(encoding="utf-8"))
    renderer = cst.Module(body=[])
    normalized_mode = _normalize_edit_symbol_mode(mode)
    signature_node = _parse_signature_function_node(new_signature) if normalized_mode == "signature" else None
    replacement_node = (
        _parse_replacement_function_node(
            _build_refactor_symbol_replacement(target=target, symbol=qualified_name, new_code=new_code)
            if normalized_mode == "refactor_symbol"
            else new_code
        )
        if normalized_mode in {"full", "refactor_symbol"}
        else None
    )
    insert_nodes = _parse_insert_statements(new_code) if normalized_mode == "body_insert" else []
    sql_replacement_items = _parse_sql_block_payload(new_code) if normalized_mode == "sql_blocks" else []
    sql_replacement_map = (
        {int(item["index"]): dict(item) for item in sql_replacement_items if item.get("index") != "all"}
        if normalized_mode == "sql_blocks"
        else {}
    )
    sql_apply_all_payload = next(
        (dict(item) for item in sql_replacement_items if item.get("index") == "all"),
        None,
    )
    normalized_anchor = " ".join(str(anchor_pattern or "").split())

    def _insert_into_block(block: cst.IndentedBlock) -> tuple[cst.IndentedBlock, bool]:
        statements = list(block.body)
        for idx, statement in enumerate(statements):
            updated_statement, replaced = _insert_into_statement(statement)
            statements[idx] = updated_statement
            if replaced:
                return block.with_changes(body=statements), True
            rendered = " ".join(renderer.code_for_node(updated_statement).split())
            if normalized_anchor and normalized_anchor in rendered:
                insertion_index = idx if position == "before" else idx + 1
                new_body = [*statements[:insertion_index], *insert_nodes, *statements[insertion_index:]]
                return block.with_changes(body=new_body), True
        return block, False

    def _insert_into_else(else_node: cst.Else) -> tuple[cst.Else, bool]:
        if isinstance(else_node.body, cst.IndentedBlock):
            new_body, replaced = _insert_into_block(else_node.body)
            if replaced:
                return else_node.with_changes(body=new_body), True
        return else_node, False

    def _insert_into_statement(statement: cst.BaseStatement) -> tuple[cst.BaseStatement, bool]:
        if isinstance(statement, cst.If):
            if isinstance(statement.body, cst.IndentedBlock):
                new_body, replaced = _insert_into_block(statement.body)
                if replaced:
                    return statement.with_changes(body=new_body), True
            if isinstance(statement.orelse, cst.Else):
                new_else, replaced = _insert_into_else(statement.orelse)
                if replaced:
                    return statement.with_changes(orelse=new_else), True
            elif isinstance(statement.orelse, cst.If):
                new_elif, replaced = _insert_into_statement(statement.orelse)
                if replaced:
                    return statement.with_changes(orelse=new_elif), True
        elif isinstance(statement, cst.Try):
            if isinstance(statement.body, cst.IndentedBlock):
                new_body, replaced = _insert_into_block(statement.body)
                if replaced:
                    return statement.with_changes(body=new_body), True
            new_handlers: list[cst.ExceptHandler] = []
            for handler in statement.handlers:
                if isinstance(handler.body, cst.IndentedBlock):
                    new_body, replaced = _insert_into_block(handler.body)
                    if replaced:
                        return (
                            statement.with_changes(
                                handlers=(
                                    *new_handlers,
                                    handler.with_changes(body=new_body),
                                    *statement.handlers[len(new_handlers) + 1 :],
                                )
                            ),
                            True,
                        )
                new_handlers.append(handler)
            if isinstance(statement.orelse, cst.Else):
                new_else, replaced = _insert_into_else(statement.orelse)
                if replaced:
                    return statement.with_changes(orelse=new_else), True
            if statement.finalbody is not None and isinstance(statement.finalbody.body, cst.IndentedBlock):
                new_final, replaced = _insert_into_block(statement.finalbody.body)
                if replaced:
                    return statement.with_changes(finalbody=statement.finalbody.with_changes(body=new_final)), True
        elif isinstance(statement, (cst.For, cst.While, cst.With)):
            if isinstance(statement.body, cst.IndentedBlock):
                new_body, replaced = _insert_into_block(statement.body)
                if replaced:
                    return statement.with_changes(body=new_body), True
            if isinstance(statement.orelse, cst.Else):
                new_else, replaced = _insert_into_else(statement.orelse)
                if replaced:
                    return statement.with_changes(orelse=new_else), True
        return statement, False

    class _Transformer(cst.CSTTransformer):
        def __init__(self) -> None:
            self._class_stack: list[str] = []
            self.replaced = False
            self._inside_target = False
            self._sql_index = 0
            self._sql_literal_index = 0

        def visit_ClassDef(self, node: cst.ClassDef) -> bool:
            self._class_stack.append(node.name.value)
            return True

        def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.CSTNode:
            self._class_stack.pop()
            return updated_node

        def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
            current_name = ".".join([*self._class_stack, node.name.value]).strip(".")
            if normalized_mode == "sql_blocks" and current_name == qualified_name:
                self._inside_target = True
                self._sql_index = 0
                self._sql_literal_index = 0
            return True

        def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.CSTNode:
            current_name = ".".join([*self._class_stack, original_node.name.value]).strip(".")
            if current_name != qualified_name:
                return updated_node
            if normalized_mode == "signature":
                assert signature_node is not None
                self.replaced = True
                return updated_node.with_changes(
                    params=signature_node.params,
                    returns=signature_node.returns,
                    asynchronous=signature_node.asynchronous,
                    whitespace_after_def=signature_node.whitespace_after_def,
                )
            if normalized_mode == "body_insert":
                body = updated_node.body
                if not isinstance(body, cst.IndentedBlock):
                    raise ValueError("MUTATION_INVALID_BODY_INSERT unexpected_function_body")
                new_body, replaced = _insert_into_block(body)
                if not replaced:
                    raise ValueError(f"ANCHOR_NOT_FOUND anchor={anchor_pattern!r}")
                self.replaced = True
                return updated_node.with_changes(body=new_body)
            if normalized_mode in {"full", "refactor_symbol"}:
                assert replacement_node is not None
                self.replaced = True
                return replacement_node.with_changes(
                    leading_lines=original_node.leading_lines,
                    decorators=original_node.decorators,
                )
            if normalized_mode == "sql_blocks":
                self._inside_target = False
                self.replaced = True
                return updated_node
            raise ValueError(f"INVALID_MODE mode={normalized_mode!r}")

        def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.CSTNode:
            if normalized_mode != "sql_blocks" or not self._inside_target:
                return updated_node
            if not updated_node.args:
                return updated_node
            first_arg = updated_node.args[0].value
            if not isinstance(first_arg, cst.SimpleString):
                return updated_node
            try:
                original_sql = ast.literal_eval(first_arg.value)
            except Exception:
                return updated_node
            if not isinstance(original_sql, str):
                return updated_node
            normalized = " ".join(original_sql.split())
            upper = normalized.upper()
            if "SELECT " not in upper or " FROM " not in upper:
                return updated_node
            self._sql_index += 1
            payload = sql_replacement_map.get(self._sql_index)
            if payload is None and sql_apply_all_payload is not None:
                payload = sql_apply_all_payload
            if payload is None:
                return updated_node
            if "sql" in payload:
                return updated_node.with_changes(
                    args=[
                        cst.Arg(
                            value=_render_simple_string_like(
                                original_literal=updated_node.args[0].value.value,
                                new_value=str(payload["sql"]),
                            )
                        ),
                        *list(updated_node.args[1:]),
                    ]
                )
            if "conditions" in payload:
                predicates_sql, arguments = _materialize_sql_conditions(
                    conditions=list(payload["conditions"]),
                    insertion_index=_infer_sql_predicate_insertion_index(original_sql=original_sql),
                )
                predicate_text = " AND ".join(predicates_sql)
                argument_values = arguments
            else:
                argument_values = [str(payload.get("argument") or "relation_type").strip()]
                predicate_text = str(payload.get("predicate") or "").strip()
            method_name = (
                updated_node.func.attr.value
                if isinstance(updated_node.func, cst.Attribute) and isinstance(updated_node.func.attr, cst.Name)
                else ""
            )
            args_after_sql = list(updated_node.args[1:])
            insert_at = len(args_after_sql)
            if method_name == "fetch" and len(args_after_sql) >= 2:
                trailing = args_after_sql[-2:]
                trailing_names = [item.value.value if isinstance(item.value, cst.Name) else "" for item in trailing]
                if trailing_names == ["limit", "offset"]:
                    insert_at = len(args_after_sql) - 2
            insertion_index = insert_at + 1
            if "conditions" in payload:
                predicates_sql, argument_values = _materialize_sql_conditions(
                    conditions=list(payload["conditions"]),
                    insertion_index=insertion_index,
                )
                predicate_text = " AND ".join(predicates_sql)
            new_sql = _inject_sql_predicate(
                original_sql=original_sql,
                predicate=predicate_text,
                insertion_index=insertion_index,
            )
            new_args = [updated_node.args[0]]
            new_args.extend(args_after_sql[:insert_at])
            existing_values = {"".join(renderer.code_for_node(arg.value).split()) for arg in args_after_sql}
            for argument_text in argument_values:
                if "".join(argument_text.split()) not in existing_values:
                    new_args.append(cst.Arg(value=cst.parse_expression(argument_text)))
            new_args.extend(args_after_sql[insert_at:])
            return updated_node.with_changes(
                args=[
                    cst.Arg(
                        value=_render_simple_string_like(
                            original_literal=updated_node.args[0].value.value,
                            new_value=new_sql,
                        )
                    ),
                    *new_args[1:],
                ]
            )

        def leave_SimpleString(self, original_node: cst.SimpleString, updated_node: cst.SimpleString) -> cst.CSTNode:
            if normalized_mode != "sql_blocks" or not self._inside_target:
                return updated_node
            try:
                value = ast.literal_eval(original_node.value)
            except Exception:
                return updated_node
            if not isinstance(value, str):
                return updated_node
            normalized = " ".join(value.split())
            upper = normalized.upper()
            if "SELECT " not in upper or " FROM " not in upper:
                return updated_node
            self._sql_literal_index += 1
            replacement = sql_replacement_map.get(self._sql_literal_index)
            if replacement is None or "sql" not in replacement:
                return updated_node
            return _render_simple_string_like(
                original_literal=original_node.value,
                new_value=str(replacement["sql"]),
            )

    transformer = _Transformer()
    updated = module.visit(transformer)
    if not transformer.replaced:
        raise LookupError(f"PYTHON_SYMBOL_NOT_FOUND symbol={qualified_name}")
    target.write_text(updated.code, encoding="utf-8")


def _update_call_kwarg_with_libcst(
    *,
    target: Path,
    qualified_name: str,
    callee: str,
    kwarg: str,
    new_value: str,
) -> None:
    module = cst.parse_module(target.read_text(encoding="utf-8"))
    replacement_value = cst.parse_expression(new_value)

    class _Transformer(cst.CSTTransformer):
        def __init__(self) -> None:
            self._class_stack: list[str] = []
            self._inside_target = False
            self.replaced = False

        def visit_ClassDef(self, node: cst.ClassDef) -> bool:
            self._class_stack.append(node.name.value)
            return True

        def leave_ClassDef(
            self, original_node: cst.ClassDef, updated_node: cst.ClassDef
        ) -> cst.BaseStatement | cst.FlattenSentinel[cst.BaseStatement] | cst.RemovalSentinel:
            self._class_stack.pop()
            return updated_node

        def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
            current_name = ".".join([*self._class_stack, node.name.value]).strip(".")
            self._inside_target = current_name == qualified_name
            return True

        def leave_FunctionDef(
            self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
        ) -> cst.BaseStatement | cst.FlattenSentinel[cst.BaseStatement] | cst.RemovalSentinel:
            self._inside_target = False
            return updated_node

        def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.BaseExpression:
            if not self._inside_target or self.replaced:
                return updated_node
            if not _callee_matches(updated_node.func, callee):
                return updated_node
            new_args: list[cst.Arg] = []
            replaced_here = False
            for arg in updated_node.args:
                if arg.keyword and arg.keyword.value == kwarg:
                    new_args.append(arg.with_changes(value=replacement_value))
                    replaced_here = True
                else:
                    new_args.append(arg)
            if not replaced_here:
                return updated_node
            self.replaced = True
            return updated_node.with_changes(args=new_args)

    transformer = _Transformer()
    updated = module.visit(transformer)
    if not transformer.replaced:
        raise LookupError(f"CALL_KWARG_NOT_FOUND callee={callee!r} kwarg={kwarg!r}")
    target.write_text(updated.code, encoding="utf-8")


def _replace_function_with_libcst(*, target: Path, qualified_name: str, replacement: str) -> None:
    module = cst.parse_module(target.read_text(encoding="utf-8"))
    replacement_node = _parse_replacement_function_node(replacement)

    class _Transformer(cst.CSTTransformer):
        def __init__(self) -> None:
            self._class_stack: list[str] = []
            self.replaced = False

        def visit_ClassDef(self, node: cst.ClassDef) -> bool:
            self._class_stack.append(node.name.value)
            return True

        def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.CSTNode:
            self._class_stack.pop()
            return updated_node

        def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.CSTNode:
            current_name = ".".join([*self._class_stack, original_node.name.value]).strip(".")
            if current_name != qualified_name:
                return updated_node
            self.replaced = True
            return replacement_node.with_changes(
                leading_lines=original_node.leading_lines,
                decorators=original_node.decorators,
            )

    transformer = _Transformer()
    updated = module.visit(transformer)
    if not transformer.replaced:
        raise LookupError(f"PYTHON_SYMBOL_NOT_FOUND symbol={qualified_name}")
    target.write_text(updated.code, encoding="utf-8")


def _parse_replacement_function_node(replacement: str) -> cst.FunctionDef:
    parsed = cst.parse_module(textwrap.dedent(str(replacement or "")).strip() + "\n")
    for statement in parsed.body:
        if isinstance(statement, cst.FunctionDef):
            return statement
    raise ValueError("MUTATION_INVALID_FUNCTION_REPLACEMENT missing_function_node")


def _run_optional_formatters(*, target: Path, enabled: bool | dict[str, Any]) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False, "executed": [], "errors": []}
    if target.suffix.lower() != ".py":
        return {"enabled": True, "executed": [], "errors": [], "reason": "non_python_file"}

    cfg = enabled if isinstance(enabled, dict) else {}
    run_black = bool(cfg.get("black", True))
    run_ruff = bool(cfg.get("ruff", True))
    timeout_seconds = float(cfg.get("timeout_seconds", 12.0))
    executed: list[str] = []
    errors: list[str] = []

    def call(name: str, cmd: list[str]) -> None:
        started = datetime.now(UTC).isoformat()
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
        if proc.returncode == 0:
            executed.append(name)
            return
        errors.append(
            f"{name}_failed rc={proc.returncode} started_at={started} "
            f"stderr={(proc.stderr or '').strip()} stdout={(proc.stdout or '').strip()}"
        )

    if run_black:
        call("black", [sys.executable, "-m", "black", str(target)])
    if run_ruff:
        call("ruff_format", [sys.executable, "-m", "ruff", "format", str(target)])
    return {"enabled": True, "executed": executed, "errors": errors}


def _replace_lines_in_file(*, target: Path, start_line: int, end_line: int, replacement: str) -> None:
    text = target.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    if start_line < 1:
        raise ValueError("start_line must be >= 1")
    if end_line > len(lines):
        raise ValueError(f"end_line out of range: {end_line} > {len(lines)}")
    if start_line > len(lines) + 1:
        raise ValueError(f"start_line out of range: {start_line} > {len(lines) + 1}")
    replacement_lines = replacement.splitlines(keepends=True)
    if replacement and not replacement.endswith(("\n", "\r")) and replacement_lines:
        replacement_lines[-1] = replacement_lines[-1] + "\n"
    if end_line < start_line:
        new_lines = lines[: start_line - 1] + replacement_lines + lines[start_line - 1 :]
    else:
        new_lines = lines[: start_line - 1] + replacement_lines + lines[end_line:]
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
            newline="",
        ) as handle:
            handle.write("".join(new_lines))
            tmp_path = Path(handle.name)
        os.replace(str(tmp_path), str(target))
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _last_return_line(*, file_path: Path, function_name: str) -> int:
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(file_path))
    target_name = function_name.split(".", 1)[-1].strip()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target_name:
            return_nodes = [child for child in ast.walk(node) if isinstance(child, ast.Return)]
            if not return_nodes:
                raise ValueError(f"FUNCTION_RETURN_NOT_FOUND function={function_name}")
            return max(int(child.lineno) for child in return_nodes)
    raise LookupError(f"PYTHON_SYMBOL_NOT_FOUND symbol={function_name}")


__all__ = [
    "WorkspaceSecurityError",
    "apply_patch",
    "apply_staged_delivery_slice",
    "edit_file",
    "edit_symbol",
    "extend_python_signature",
    "insert_at_anchor",
    "insert_after_symbol",
    "insert_before_return",
    "replace_function",
    "replace_in_function",
    "replace_range",
    "validate_replace_function_candidate",
    "update_call_kwarg",
    "write_file",
]
