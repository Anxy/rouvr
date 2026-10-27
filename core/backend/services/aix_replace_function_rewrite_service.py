from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from backend.services.aix_prompt_builder_service import RewritePromptBuilder
from backend.services.devexecutor_planner_bridge_service import request_planner_json_via_llm
from backend.services.devexecutor_runlog_resolver_service import get_rewrite_trace_root

PLACEHOLDER_BODY_MARKERS: tuple[str, ...] = (
    "# ...",
    "...",
    "omitted",
    "unchanged code omitted",
    "existing function body",
    "<updated body>",
)


@dataclass(frozen=True, slots=True)
class ReplaceFunctionRewriteSpec:
    relative_path: str
    qualified_symbol: str
    edit_intent: str
    preserve_constraints: tuple[str, ...]
    line_targets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObservedFunctionShape:
    qualified_symbol: str
    function_name: str
    header: str
    is_async: bool
    receiver_name: str | None
    line_count: int


@dataclass(frozen=True, slots=True)
class ReplaceFunctionRewriteResponse:
    qualified_symbol: str
    replacement: str
    notes: tuple[str, ...] = ()


class ReplaceFunctionRewriteServiceError(ValueError):
    def __init__(self, code: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = str(code or "").strip() or "REWRITE_SERVICE_ERROR"
        self.context = dict(context or {})


@dataclass(frozen=True, slots=True)
class ReplaceFunctionRewriteTraceRef:
    trace_id: str
    trace_ref: str


def inspect_observed_function_source(*, observed_source: str, qualified_symbol: str) -> dict[str, Any]:
    source = str(observed_source or "")
    stripped = source.strip()
    expected_name = str(qualified_symbol or "").split(".")[-1].strip()
    diagnostics: dict[str, Any] = {
        "qualified_symbol": qualified_symbol,
        "expected_symbol_name": expected_name or None,
        "line_count": max(1, len(stripped.splitlines())) if stripped else 0,
        "contains_exactly_one_function": False,
        "contains_sibling_defs_or_classes": False,
        "contains_wrapping_text": False,
        "contains_truncation_markers": bool(placeholder_markers_in_text(stripped)),
        "function_count": 0,
        "class_count": 0,
        "resolved_symbol_identity": None,
        "parsed_replacement_symbol_name": None,
        "line_range_used": None,
        "raw_observed_source_payload": source,
        "payload_preview_first_20_lines": "\n".join(stripped.splitlines()[:20]),
    }
    if not stripped:
        diagnostics["failure_reason"] = "empty_observed_source"
        return diagnostics
    try:
        tree = ast.parse(stripped)
    except SyntaxError as exc:
        diagnostics["failure_reason"] = f"syntax={exc.msg}"
        return diagnostics
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    diagnostics["function_count"] = len(functions)
    diagnostics["class_count"] = len(classes)
    diagnostics["contains_exactly_one_function"] = len(functions) == 1 and len(tree.body) == 1
    diagnostics["contains_sibling_defs_or_classes"] = len(functions) + len(classes) > 1
    diagnostics["contains_wrapping_text"] = any(
        not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in tree.body
    )
    if len(functions) == 1:
        func = functions[0]
        diagnostics["resolved_symbol_identity"] = func.name
        diagnostics["parsed_replacement_symbol_name"] = func.name
        diagnostics["line_range_used"] = {
            "start_line": int(getattr(func, "lineno", 0) or 0) or None,
            "end_line": int(getattr(func, "end_lineno", 0) or 0) or None,
        }
        if expected_name and func.name != expected_name:
            diagnostics["failure_reason"] = f"symbol_name_mismatch expected={expected_name} got={func.name}"
            return diagnostics
    if not diagnostics["contains_exactly_one_function"]:
        diagnostics["failure_reason"] = "exactly_one_function_required"
        return diagnostics
    return diagnostics


def extract_replace_function_rewrite_spec(step: dict[str, Any] | None) -> ReplaceFunctionRewriteSpec | None:
    if not isinstance(step, dict):
        return None
    args = dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}
    relative_path = (
        str(args.get("relative_path") or step.get("file") or step.get("relative_path") or "").strip().replace("\\", "/")
    )
    qualified_symbol = str(
        args.get("qualified_symbol")
        or args.get("function_name")
        or step.get("qualified_symbol")
        or step.get("target_symbol")
        or args.get("target_symbol")
        or ""
    ).strip()
    edit_intent = str(args.get("edit_intent") or step.get("edit_intent") or "").strip()
    preserve_constraints = _normalize_string_list(
        args.get("preserve_constraints") or step.get("preserve_constraints") or []
    )
    line_targets = _normalize_string_list(args.get("line_targets") or step.get("line_targets") or [])
    if not (relative_path and qualified_symbol and edit_intent):
        return None
    return ReplaceFunctionRewriteSpec(
        relative_path=relative_path,
        qualified_symbol=qualified_symbol,
        edit_intent=edit_intent,
        preserve_constraints=tuple(preserve_constraints),
        line_targets=tuple(line_targets),
    )


def parse_observed_function_shape(*, observed_source: str, qualified_symbol: str) -> ObservedFunctionShape:
    source = str(observed_source or "").strip()
    diagnostics = inspect_observed_function_source(observed_source=source, qualified_symbol=qualified_symbol)
    failure_reason = str(diagnostics.get("failure_reason") or "").strip()
    if failure_reason:
        raise ValueError(f"invalid_observed_symbol_source {failure_reason}")
    tree = ast.parse(source)
    func = next(node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    header = ""
    for line in source.splitlines():
        if line.strip():
            header = line.rstrip()
            break
    receiver_name: str | None = None
    if func.args.args:
        receiver_name = str(func.args.args[0].arg or "").strip() or None
    return ObservedFunctionShape(
        qualified_symbol=qualified_symbol,
        function_name=func.name,
        header=header,
        is_async=isinstance(func, ast.AsyncFunctionDef),
        receiver_name=receiver_name,
        line_count=max(1, len(source.splitlines())),
    )


def build_rewrite_system_prompt() -> str:
    return RewritePromptBuilder.system_prompt()


def build_rewrite_user_prompt(
    *,
    observed_source: str,
    spec: ReplaceFunctionRewriteSpec,
    shape: ObservedFunctionShape,
    validation_error: str | None = None,
) -> str:
    observed_shape = {
        "header": shape.header,
        "function_name": shape.function_name,
        "is_async": shape.is_async,
        "receiver_name": shape.receiver_name,
        "line_count": shape.line_count,
    }
    payload = {
        "target_file": spec.relative_path,
        "qualified_symbol": spec.qualified_symbol,
        "edit_intent": spec.edit_intent,
        "preserve_constraints": list(spec.preserve_constraints),
        "line_targets": list(spec.line_targets),
        "observed_shape": observed_shape,
        "validation_error": str(validation_error or "").strip() or None,
    }
    return RewritePromptBuilder.user_prompt(
        target_file=spec.relative_path,
        qualified_symbol=spec.qualified_symbol,
        edit_intent=spec.edit_intent,
        preserve_constraints=list(spec.preserve_constraints),
        line_targets=list(spec.line_targets),
        observed_shape=observed_shape,
        observed_source=observed_source,
        validation_error=validation_error,
    )


def build_rewrite_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["qualified_symbol", "replacement", "notes"],
        "properties": {
            "qualified_symbol": {"type": "string", "minLength": 1},
            "replacement": {"type": "string", "minLength": 1},
            "notes": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
    }


def build_rewrite_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "replace_function_rewrite_response",
            "strict": True,
            "schema": build_rewrite_response_schema(),
        },
    }


def observed_source_sha256(*, observed_source: str) -> str:
    return hashlib.sha256(str(observed_source or "").encode("utf-8")).hexdigest()


def persist_replace_function_rewrite_trace(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    attempt: int,
    observed_source_hash: str,
    qualified_symbol: str,
    validation_error_input: str | None,
    raw_response: Any,
    parsed_response: Any,
    validation_error: dict[str, Any] | None,
) -> ReplaceFunctionRewriteTraceRef:
    trace_id = str(
        uuid5(
            NAMESPACE_URL,
            "|".join(
                [
                    str(tenant_id),
                    str(project_id),
                    str(correlation_id),
                    str(qualified_symbol),
                    str(observed_source_hash),
                    str(attempt),
                ]
            ),
        )
    )
    payload = {
        "trace_id": trace_id,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "correlation_id": correlation_id,
        "attempt": int(attempt),
        "qualified_symbol": qualified_symbol,
        "observed_source_hash": observed_source_hash,
        "validation_error_input": validation_error_input,
        "raw_response": raw_response,
        "parsed_response": parsed_response,
        "validation_error": dict(validation_error or {}) if validation_error else None,
    }
    trace_root = get_rewrite_trace_root()
    trace_root.mkdir(parents=True, exist_ok=True)
    out_path = trace_root / f"{trace_id}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    latest_path = trace_root / "latest.json"
    latest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return ReplaceFunctionRewriteTraceRef(trace_id=trace_id, trace_ref=str(out_path))


def load_replace_function_rewrite_trace(*, trace_ref: str) -> dict[str, Any]:
    path = Path(str(trace_ref or "").strip())
    return json.loads(path.read_text(encoding="utf-8"))


def parse_replace_function_rewrite_response(
    payload: dict[str, Any] | None,
    *,
    expected_symbol: str,
) -> ReplaceFunctionRewriteResponse:
    if not isinstance(payload, dict):
        raise ReplaceFunctionRewriteServiceError(
            "REWRITE_RESPONSE_INVALID",
            {"reason": "payload_must_be_object", "raw_response": payload},
        )
    allowed_keys = {"qualified_symbol", "replacement", "notes"}
    extra_keys = sorted(str(key) for key in payload.keys() if str(key) not in allowed_keys)
    if extra_keys:
        raise ReplaceFunctionRewriteServiceError(
            "REWRITE_RESPONSE_CONTAMINATED",
            {"reason": "unexpected_fields", "extra_fields": extra_keys, "raw_response": dict(payload)},
        )
    qualified_symbol = str(payload.get("qualified_symbol") or "").strip()
    if not qualified_symbol:
        raise ReplaceFunctionRewriteServiceError(
            "REWRITE_RESPONSE_QUALIFIED_SYMBOL_REQUIRED",
            {"reason": "missing_qualified_symbol", "raw_response": dict(payload)},
        )
    if qualified_symbol != str(expected_symbol or "").strip():
        raise ReplaceFunctionRewriteServiceError(
            "REWRITE_RESPONSE_SYMBOL_MISMATCH",
            {
                "expected_symbol": expected_symbol,
                "actual_symbol": qualified_symbol,
                "raw_response": dict(payload),
            },
        )
    replacement = str(payload.get("replacement") or "").strip()
    if not replacement:
        raise ReplaceFunctionRewriteServiceError(
            "REWRITE_RESPONSE_REPLACEMENT_REQUIRED",
            {"qualified_symbol": qualified_symbol, "raw_response": dict(payload)},
        )
    if "```" in replacement:
        raise ReplaceFunctionRewriteServiceError(
            "REWRITE_RESPONSE_CONTAMINATED",
            {
                "reason": "fenced_replacement",
                "qualified_symbol": qualified_symbol,
                "raw_response": dict(payload),
            },
        )
    notes = _normalize_string_list(payload.get("notes") or [])
    return ReplaceFunctionRewriteResponse(
        qualified_symbol=qualified_symbol,
        replacement=replacement,
        notes=tuple(notes),
    )


def invoke_replace_function_rewrite(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    workspace_root: str,
    observed_source: str,
    spec: ReplaceFunctionRewriteSpec,
    shape: ObservedFunctionShape,
    provider: str,
    model: str | None,
    authorization: str | None,
    validation_error: str | None = None,
    request_json: Any = None,
) -> tuple[ReplaceFunctionRewriteResponse, dict[str, Any]]:
    request_fn = request_json or request_planner_json_via_llm
    raw_payload, diagnostics = request_fn(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        workspace_root=workspace_root,
        system_prompt=build_rewrite_system_prompt(),
        user_prompt=build_rewrite_user_prompt(
            observed_source=observed_source,
            spec=spec,
            shape=shape,
            validation_error=validation_error,
        ),
        provider=provider,
        model=str(model or "").strip() or None,
        authorization=authorization,
        timeout_seconds=30,
        max_retries=0,
        response_format=build_rewrite_response_format(),
    )
    parsed = parse_replace_function_rewrite_response(raw_payload, expected_symbol=spec.qualified_symbol)
    enriched_diagnostics = dict(diagnostics or {})
    enriched_diagnostics["rewrite_response_raw"] = (
        dict(raw_payload or {}) if isinstance(raw_payload, dict) else raw_payload
    )
    enriched_diagnostics["rewrite_response_parsed"] = {
        "qualified_symbol": parsed.qualified_symbol,
        "replacement": parsed.replacement,
        "notes": list(parsed.notes),
    }
    enriched_diagnostics["rewrite_response_notes"] = list(parsed.notes)
    enriched_diagnostics["rewrite_response_qualified_symbol"] = parsed.qualified_symbol
    enriched_diagnostics["observed_source_hash"] = observed_source_sha256(observed_source=observed_source)
    return parsed, enriched_diagnostics


def extract_rewrite_replacement(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("replacement") or "").strip()


def placeholder_markers_in_text(text: str) -> list[str]:
    lowered = str(text or "").lower()
    markers: list[str] = []
    for marker in PLACEHOLDER_BODY_MARKERS:
        if marker.lower() in lowered and marker not in markers:
            markers.append(marker)
    return markers


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            normalized.append(text)
    return normalized
