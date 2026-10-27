from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.models.planner_slice_packet_models import PlannerSlicePacketCurrent
from backend.services.aix_replace_function_rewrite_service import (
    extract_replace_function_rewrite_spec,
    inspect_observed_function_source,
    invoke_replace_function_rewrite,
    observed_source_sha256,
    parse_observed_function_shape,
    persist_replace_function_rewrite_trace,
    placeholder_markers_in_text,
)
from backend.services.aix_v2_live_file_grounding_service import (
    V2LiveFileGroundingError,
    is_v2_planner_packet,
    validate_v2_edit_against_live_file,
)
from backend.services.devexecutor_content_validator_service import validate_step_content
from backend.services.devexecutor_mutation_tool_taxonomy_service import (
    is_code_mutation_tool,
)
from backend.services.devexecutor_mutation_tools_service import (
    validate_replace_function_candidate,
)
from backend.services.devexecutor_planner_bridge_service import (
    PLANNER_MODE_GENERIC,
    PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION,
    PlannerBridgeError,
    normalize_edit_file_args,
)
from backend.services.devexecutor_planner_bridge_service import (
    _canonicalize_edit_file_blocks as _bridge_canonicalize_edit_file_blocks,
)
from backend.services.devexecutor_planner_bridge_service import (
    _extract_canonical_search_replace_pairs as _bridge_extract_canonical_search_replace_pairs,
)
from backend.services.devexecutor_planner_bridge_service import (
    _extract_edit_file_blocks as _bridge_extract_edit_file_blocks,
)
from backend.services.devexecutor_planner_bridge_service import (
    _extract_semantic_search_replace_pair as _bridge_extract_semantic_search_replace_pair,
)
from backend.services.devexecutor_python_symbol_service import (
    resolve_python_symbol_range,
)
from backend.services.devexecutor_tool_contract_registry_service import (
    get_code_change_allowed_mutation_tools,
)

LOGGER = logging.getLogger(__name__)


def _extract_edit_file_search_replace_pairs(text: str) -> list[tuple[str, str]] | None:
    return _bridge_extract_canonical_search_replace_pairs(text)


def _normalize_edit_file_search_replace_blocks(payload: Any) -> str | None:
    return _bridge_canonicalize_edit_file_blocks(payload)


def _structured_search_replace_blocks_to_canonical(payload: Any) -> str | None:
    return _bridge_canonicalize_edit_file_blocks(payload)


def _extract_structured_search_replace_pair_from_item(item: dict[str, Any]) -> tuple[str, str] | None:
    return _bridge_extract_semantic_search_replace_pair(item)


def _extract_structured_search_replace_pairs(payload: Any) -> list[tuple[str, str]] | None:
    return _bridge_extract_edit_file_blocks(payload, depth=0)


def _step_relative_path(step: dict[str, Any] | None) -> str:
    if not isinstance(step, dict):
        return ""
    args = step.get("args")
    args_dict = dict(args) if isinstance(args, dict) else {}
    for key in ("relative_path", "path", "file", "filename"):
        value = str(args_dict.get(key) or step.get(key) or "").strip()
        if value:
            return value.replace("\\", "/")
    return ""


def _decode_validation_error(error: str | None) -> tuple[str, dict[str, Any]]:
    text = str(error or "").strip()
    if not text:
        return "", {}
    if ":" in text:
        code, _, rest = text.partition(":")
        detail = rest.strip()
        if detail.startswith("{"):
            try:
                parsed = json.loads(detail)
                if isinstance(parsed, dict):
                    return code.strip(), parsed
            except json.JSONDecodeError:
                pass
        return code.strip(), {"detail": detail} if detail else {}
    return text, {}


def _resolve_prompt_target_path(
    *,
    workspace_root: str | Path,
    relative_path: str,
    metadata: dict[str, Any] | None = None,
    instruction_contract: dict[str, Any] | None = None,
) -> Path | None:
    from backend.services.aix_target_file_resolution_service import resolve_target_file

    resolution = resolve_target_file(
        workspace_root=workspace_root,
        input_path=str(relative_path or ""),
        metadata=metadata,
        instruction_contract=instruction_contract,
    )
    absolute = resolution.resolved_absolute_path
    if not absolute:
        return None
    return Path(absolute)


def _emit_runtime_stage(stage: str, message: str, context: dict[str, Any] | None = None) -> None:
    LOGGER.info(
        "planner_validation_stage",
        extra={"stage": stage, "message_text": message, "context": dict(context or {})},
    )


def _simulate_last_read_buffer_for_plan_step(
    *,
    workspace_root: str | None,
    relative_path: str,
    read_tool: str,
    read_args: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    if not workspace_root or read_tool not in {"read_file", "read_range"}:
        return None
    try:
        resolved = _resolve_prompt_target_path(
            workspace_root=workspace_root,
            relative_path=relative_path,
            metadata=metadata,
            instruction_contract={},
        )
    except Exception:
        LOGGER.exception("planner_validation_simulate_read_failed")
        return None
    if resolved is None or not resolved.exists():
        return None
    return {"content": resolved.read_text(encoding="utf-8", errors="replace")}


def _is_v2_structured_edit_file_args(args: dict[str, Any]) -> bool:
    payload = dict(args or {})
    if str(payload.get("grounding_source") or "").strip() not in {"read_range", "search_range"}:
        return False
    has_range = isinstance(payload.get("search_range"), dict) or isinstance(
        payload.get("edit_range_within_read"), dict
    )
    has_replacement = isinstance(payload.get("replacement_text"), str)
    return bool(has_range and has_replacement)


def _normalize_planner_step_dialects(steps: list[Any]) -> list[Any]:
    normalized: list[Any] = []
    for step in list(steps or []):
        normalized.append(dict(step) if isinstance(step, dict) else step)
    return normalized


class RetryPromptContractCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_prompt: str
    rejection_codes: list[str] = Field(min_length=1)
    violated_invariants: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    retry_rules: list[str] = Field(default_factory=list)
    positive_partial: str | None = None
    compressed: bool = False

    @field_validator("base_prompt")
    @classmethod
    def _validate_base_prompt(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("base_prompt is required")
        return text

    @field_validator("rejection_codes", "violated_invariants", "observations", "retry_rules")
    @classmethod
    def _normalize_string_list(cls, value: list[str]) -> list[str]:
        normalized = [str(item or "").strip() for item in list(value or []) if str(item or "").strip()]
        if normalized != list(value or []):
            if normalized:
                return normalized
            raise ValueError("list contains no non-empty values")
        return normalized

    @field_validator("positive_partial")
    @classmethod
    def _normalize_positive_partial(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class PlannerSemanticError(RuntimeError):
    def __init__(self, code: str, context: dict[str, Any]) -> None:
        self.code = str(code or "").strip() or "PLANNER_SEMANTIC_ERROR"
        self.context = dict(context or {})
        super().__init__(f"{self.code}: {self.context}")


SEMANTIC_ERROR_TO_FAILURE_CODE: dict[str, str] = {
    "PLANNER_SKIPPED_ANCHOR_WINDOW_READ": "PLANNER_SKIPPED_ANCHOR_WINDOW_READ",
    "PLANNER_SKIPPED_DISCOVERY": "PLANNER_SKIPPED_DISCOVERY",
    "EDIT_FILE_SEARCH_NOT_FOUND": "PLANNER_EDIT_SEARCH_NOT_FOUND",
    "EDIT_FILE_SEARCH_NOT_FROM_READ": "PLANNER_EDIT_SEARCH_NOT_FROM_READ",
    "EDIT_FILE_SEARCH_AMBIGUOUS": "PLANNER_EDIT_SEARCH_AMBIGUOUS",
    "EDIT_FILE_SEARCH_INSUFFICIENT_CONTEXT": "PLANNER_INSUFFICIENT_CONTEXT",
    "EDIT_FILE_WRONG_TARGET": "PLANNER_EDIT_WRONG_TARGET",
    "EDIT_FILE_WITHOUT_READ": "PLANNER_EDIT_WITHOUT_READ",
    "EDIT_FILE_SYNTAX_ERROR": "PLANNER_EDIT_SYNTAX_ERROR",
    "EDIT_FILE_NO_EFFECT": "PLANNER_EDIT_NO_EFFECT",
    "EDIT_FILE_EMPTY": "PLANNER_EDIT_EMPTY",
    "EDIT_FILE_MALFORMED_BLOCK": "PLANNER_EDIT_MALFORMED",
    "EDIT_FILE_EMPTY_SEARCH": "PLANNER_EDIT_EMPTY_SEARCH",
    "MUTATION_REQUIRED_BUT_ABSENT": "MUTATION_REQUIRED_BUT_ABSENT",
    "NO_OP_EDIT_DETECTED": "PLANNER_NO_OP_EDIT",
}


def validate_prompt_symbol_targets(
    *,
    prompt_symbol_targets: list[dict[str, Any]] | None,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    targets = [dict(item) for item in prompt_symbol_targets or [] if isinstance(item, dict)]
    issues: list[dict[str, Any]] = []
    normalized_targets: list[dict[str, str]] = []
    root = Path(workspace_root) if workspace_root else None
    for item in targets:
        relative_path = str(item.get("relative_path") or "").strip().replace("\\", "/").strip("/")
        symbol = str(item.get("symbol") or "").strip()
        if not relative_path or not symbol:
            issues.append(
                {"relative_path": relative_path, "symbol": symbol, "error": "PROMPT_SYMBOL_TARGET_INCOMPLETE"}
            )
            continue
        resolved_path = root / relative_path if root else None
        if resolved_path is not None and not resolved_path.exists():
            issues.append(
                {"relative_path": relative_path, "symbol": symbol, "error": "PROMPT_SYMBOL_TARGET_PATH_MISSING"}
            )
            continue
        normalized_targets.append({"relative_path": relative_path, "symbol": symbol})
    return {"ok": not issues, "count": len(normalized_targets), "issues": issues, "targets": normalized_targets}


def _planner_packet_value(packet: PlannerSlicePacketCurrent | dict[str, Any] | None, key: str) -> Any:
    if isinstance(packet, PlannerSlicePacketCurrent):
        return getattr(packet, key, None)
    if isinstance(packet, dict):
        return packet.get(key)
    return None


def _planner_packet_tool_policy_value(
    packet: PlannerSlicePacketCurrent | dict[str, Any] | None,
    key: str,
) -> Any:
    tool_policy = _planner_packet_value(packet, "tool_policy")
    if isinstance(tool_policy, dict):
        value = tool_policy.get(key)
        if key == "allowed_mutation_tools":
            if isinstance(value, list) and value:
                return value
        elif value not in (None, "", [], {}):
            return value
    if key == "execution_lane":
        if _is_codeintel_325_form_a_only_lane(packet):
            return "mutation"
        if _packet_bound_replace_function_rewrite_lane(packet):
            return "mutation"
        return None
    if key == "allowed_mutation_tools":
        if _is_codeintel_325_form_a_only_lane(packet):
            return ["replace_function", "cmd.run"]
        if _packet_bound_replace_function_rewrite_lane(packet):
            return ["replace_function", "cmd.run"]
        return None
    return None


def _resolve_planner_mode(
    packet: PlannerSlicePacketCurrent | dict[str, Any] | None,
    *,
    planner_mode: str | None = None,
) -> str:
    explicit = str(planner_mode or "").strip()
    if explicit in {PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION, PLANNER_MODE_GENERIC}:
        return explicit
    packet_mode = str(_planner_packet_value(packet, "planner_mode") or "").strip()
    if packet_mode in {PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION, PLANNER_MODE_GENERIC}:
        return packet_mode
    return PLANNER_MODE_GENERIC


def _planner_packet_compact_anchor_snippet(packet: PlannerSlicePacketCurrent | dict[str, Any] | None) -> str:
    allowed_surface = _planner_packet_value(packet, "allowed_surface")
    anchor = getattr(allowed_surface, "anchor", None) if allowed_surface is not None else None
    if hasattr(anchor, "compact_snippet"):
        return str(getattr(anchor, "compact_snippet", None) or "").strip()
    if isinstance(anchor, dict):
        return str(anchor.get("compact_snippet") or "").strip()
    if isinstance(allowed_surface, dict):
        anchor_dict = dict(allowed_surface.get("anchor") or {})
        snippet = str(anchor_dict.get("compact_snippet") or "").strip()
        if snippet:
            return snippet
    return str(_planner_packet_value(packet, "grounded_symbol_source") or "").strip()


def _planner_packet_codeintel_symbol(packet: PlannerSlicePacketCurrent | dict[str, Any] | None) -> str:
    allowed_surface = _planner_packet_value(packet, "allowed_surface")
    anchor = getattr(allowed_surface, "anchor", None) if allowed_surface is not None else None
    if hasattr(anchor, "symbol"):
        return str(getattr(anchor, "symbol", None) or "").strip()
    if isinstance(anchor, dict):
        return str(anchor.get("symbol") or "").strip()
    if isinstance(allowed_surface, dict):
        anchor_dict = dict(allowed_surface.get("anchor") or {})
        return str(anchor_dict.get("symbol") or "").strip()
    codeintel_anchor = _planner_packet_value(packet, "codeintel_anchor")
    if hasattr(codeintel_anchor, "symbol"):
        return str(getattr(codeintel_anchor, "symbol", None) or "").strip()
    if isinstance(codeintel_anchor, dict):
        symbol = str(codeintel_anchor.get("symbol") or "").strip()
        if symbol:
            return symbol
    return str(_planner_packet_value(packet, "target_symbol") or "").strip()


def _planner_packet_codeintel_anchor_window(
    packet: PlannerSlicePacketCurrent | dict[str, Any] | None,
) -> tuple[int, int] | None:
    codeintel_anchor = _planner_packet_value(packet, "codeintel_anchor")
    if hasattr(codeintel_anchor, "start_line") and hasattr(codeintel_anchor, "end_line"):
        start_line = getattr(codeintel_anchor, "start_line", None)
        end_line = getattr(codeintel_anchor, "end_line", None)
    elif isinstance(codeintel_anchor, dict):
        start_line = codeintel_anchor.get("start_line")
        end_line = codeintel_anchor.get("end_line")
    else:
        start_line = None
        end_line = None
    try:
        normalized = (int(start_line), int(end_line))
    except (TypeError, ValueError):
        return None
    return normalized if normalized[0] > 0 and normalized[1] >= normalized[0] else None


def _is_codeintel_325_form_a_only_lane(packet: PlannerSlicePacketCurrent | dict[str, Any] | None) -> bool:
    packet_dict = packet if isinstance(packet, dict) else packet.model_dump() if packet else {}
    target_file = str(packet_dict.get("target_file") or "").strip().replace("\\", "/")
    task_number = packet_dict.get("task_number")
    validation_command = str(packet_dict.get("validation_command") or "").strip()
    return (
        target_file == "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py"
        and str(task_number or "").strip() == "325"
        and "skips_duplicate_test_prefix" in validation_command
    )


def _packet_bound_replace_function_rewrite_lane(packet: PlannerSlicePacketCurrent | dict[str, Any] | None) -> bool:
    if _is_codeintel_325_form_a_only_lane(packet):
        return False
    schema_name = str(_planner_packet_value(packet, "schema_name") or "").strip()
    planner_mode = str(_planner_packet_value(packet, "planner_mode") or "").strip()
    target_file = str(_planner_packet_value(packet, "target_file") or "").strip().replace("\\", "/").strip("/")
    target_symbol = str(_planner_packet_value(packet, "target_symbol") or "").strip()
    compact_snippet = _planner_packet_compact_anchor_snippet(packet)
    output_schema = _planner_packet_value(packet, "output_schema")
    output_type = ""
    if hasattr(output_schema, "type"):
        output_type = str(getattr(output_schema, "type", None) or "").strip()
    elif isinstance(output_schema, dict):
        output_type = str(output_schema.get("type") or "").strip()
    return bool(
        schema_name == "planner_slice.current"
        and planner_mode == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION
        and target_file
        and target_symbol
        and compact_snippet
        and output_type == "replace_function"
    )


def _planner_packet_codeintel_signature(packet: PlannerSlicePacketCurrent | dict[str, Any] | None) -> str:
    codeintel_anchor = _planner_packet_value(packet, "codeintel_anchor")
    if hasattr(codeintel_anchor, "signature"):
        return str(getattr(codeintel_anchor, "signature", None) or "").strip()
    if isinstance(codeintel_anchor, dict):
        return str(codeintel_anchor.get("signature") or "").strip()
    return ""


def _grounded_symbol_search_candidates(symbol: str) -> list[str]:
    normalized = str(symbol or "").strip()
    if not normalized:
        return []
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            candidates.append(text)

    _add(normalized)
    dot_parts = [part.strip() for part in normalized.split(".") if part.strip()]
    for index in range(len(dot_parts)):
        _add(".".join(dot_parts[index:]))
    terminal = re.split(r"[:#]", normalized)[-1].strip()
    if terminal:
        _add(terminal)
    return candidates


def _search_step_matches_grounded_symbol(
    *,
    args: dict[str, Any],
    grounded_symbol: str,
    target_file: str,
) -> bool:
    pattern = str((args or {}).get("pattern") or "").strip()
    if not pattern:
        return False
    search_relative_path = str((args or {}).get("relative_path") or "").strip().replace("\\", "/").strip("/")
    if search_relative_path and target_file and search_relative_path != target_file:
        return False
    normalized_pattern = pattern.casefold()
    for candidate in _grounded_symbol_search_candidates(grounded_symbol):
        candidate_folded = candidate.casefold()
        if candidate_folded and candidate_folded in normalized_pattern:
            return True
    return False


def _is_noop_edit(args: dict[str, Any]) -> bool:
    """Return True when edit_file contains no effective SEARCH/REPLACE mutation."""
    payload = (args or {}).get("search_replace_blocks")
    if payload in (None, "", []):
        return True
    text = _structured_search_replace_blocks_to_canonical(payload)
    if text is None and isinstance(payload, str):
        text = _normalize_edit_file_search_replace_blocks(payload) or payload
    pairs = _extract_edit_file_search_replace_pairs(text or "")
    if not pairs:
        return False
    return all(search == replace for search, replace in pairs)


def _get_target_file_content(
    *,
    workspace_root: str,
    relative_path: str,
    file_cache: dict[str, str],
) -> str:
    normalized_path = str(relative_path or "").strip().replace("\\", "/").strip("/")
    if not normalized_path:
        raise PlannerSemanticError(code="EDIT_FILE_WRONG_TARGET", context={"expected": None, "got": relative_path})
    cached = file_cache.get(normalized_path)
    if cached is not None:
        return cached
    resolved = _resolve_prompt_target_path(
        workspace_root=workspace_root,
        relative_path=normalized_path,
        metadata={"target_files": [normalized_path]},
        instruction_contract={},
    )
    if resolved is None or not resolved.exists():
        raise PlannerSemanticError(
            code="EDIT_FILE_TARGET_NOT_FOUND",
            context={"file": normalized_path},
        )
    content = resolved.read_text(encoding="utf-8", errors="replace")
    file_cache[normalized_path] = content
    return content


def _validate_search_block(
    *, search: str, file_content: str, relative_path: str, target_symbol: str | None = None
) -> None:
    normalized_search = str(search or "")
    if normalized_search not in file_content:
        raise PlannerSemanticError(
            code="EDIT_FILE_SEARCH_NOT_FOUND",
            context={
                "search": normalized_search[:100],
                "file": relative_path,
                "target_symbol": str(target_symbol or "").strip() or None,
            },
        )
    occurrences = file_content.count(normalized_search)
    if occurrences > 1:
        raise PlannerSemanticError(
            code="EDIT_FILE_SEARCH_AMBIGUOUS",
            context={
                "search": normalized_search[:100],
                "occurrences": occurrences,
                "file": relative_path,
            },
        )
    lines = [line for line in normalized_search.strip().splitlines() if line.strip()]
    normalized_target_symbol = str(target_symbol or "").strip()
    target_symbol_leaf = normalized_target_symbol.rsplit(".", 1)[-1] if normalized_target_symbol else ""
    if len(lines) < 3 and len(normalized_search) < 80:
        if len(lines) >= 2 and target_symbol_leaf and target_symbol_leaf in normalized_search:
            return
        raise PlannerSemanticError(
            code="EDIT_FILE_SEARCH_INSUFFICIENT_CONTEXT",
            context={
                "search": normalized_search[:100],
                "lines": len(lines),
                "chars": len(normalized_search),
                "file": relative_path,
            },
        )


def _validate_syntax_safe(code: str) -> tuple[bool, str]:
    """Return whether Python source parses cleanly and the syntax error when it does not."""
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as exc:
        return False, f"SyntaxError: {exc.msg} at line {exc.lineno}"


# Precompile regexes: avoids recompiling on every call
_RE_EMPTY_MUTATION = re.compile(r"(?is)^\s*SEARCH\s*\n\s*REPLACE(?:\s*\n)?\s*$")
_RE_EMPTY_SEARCH_BLOCK = re.compile(r"(?is)^\s*SEARCH\s*:?\s*\n\s*REPLACE\s*:?\s*\n")
_RE_EMPTY_MUTATION_FENCED = re.compile(r"(?is)^<<<<<<<\s*SEARCH\s*\n\s*=======\s*\n\s*>>>>>>>\s*REPLACE\s*$")

SEARCH_REPLACE_TEMPLATE = "<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE"


def _raise_error(code: str, relative_path: str, **extra_context) -> None:
    """Single point to raise PlannerSemanticError with consistent structure."""
    context = {"file": relative_path}
    context.update(extra_context)
    raise PlannerSemanticError(code=code, context=context)


def _validate_search_not_empty(search: str, relative_path: str, block_index: int) -> None:
    """Ensure SEARCH part has content, else raise."""
    if not str(search).strip():
        _raise_error("EDIT_FILE_EMPTY_SEARCH", relative_path, block_index=block_index)


def _validate_pairs(pairs: list[tuple[str, str]], relative_path: str, block_index: int) -> None:
    """Validate a list of (search, replace) pairs."""
    if not pairs:
        return
    for search, _ in pairs:
        _validate_search_not_empty(search, relative_path, block_index)


def _process_dict_block(item: dict, relative_path: str, index: int) -> str:
    """Handle structured dict block -> canonical string block."""
    pair = _extract_structured_search_replace_pair_from_item(item)
    if pair is None:
        _raise_error(
            "EDIT_FILE_MALFORMED_BLOCK",
            relative_path,
            reason="missing_search_or_replace",
            block_index=index,
            keys=sorted(str(key) for key in item)[:12],
        )

    search, replace = pair
    _validate_search_not_empty(search, relative_path, index)
    return SEARCH_REPLACE_TEMPLATE.format(search=search.rstrip(), replace=replace.rstrip())


def _process_str_block(block_text: str, relative_path: str, index: int) -> str:
    """Normalize and validate a single string block. Returns canonical block."""
    block_text = str(block_text or "")
    stripped = block_text.strip()

    if not stripped:
        _raise_error("EDIT_FILE_EMPTY", relative_path, reason="empty_block", block_index=index)

    if _RE_EMPTY_MUTATION.match(block_text):
        _raise_error(
            "MUTATION_REQUIRED_BUT_ABSENT",
            relative_path,
            block_index=index,
            reason="edit_file_missing_semantic_mutation",
            snippet=stripped[:80] or None,
        )

    if _RE_EMPTY_SEARCH_BLOCK.match(block_text):
        _raise_error("EDIT_FILE_EMPTY_SEARCH", relative_path, block_index=index)

    # Try direct extraction first: cheapest path
    pairs = _extract_edit_file_search_replace_pairs(block_text)
    if pairs:
        _validate_pairs(pairs, relative_path, index)
        return block_text.rstrip()

    # Try normalization
    normalized = _normalize_edit_file_search_replace_blocks(block_text)
    if normalized is not None:
        pairs = _extract_edit_file_search_replace_pairs(normalized)
        if not pairs:
            _raise_error(
                "EDIT_FILE_MALFORMED_BLOCK",
                relative_path,
                reason="missing_search_or_replace",
                block_index=index,
                snippet=normalized[:160],
            )
        _validate_pairs(pairs, relative_path, index)
        return normalized.rstrip()

    # Last resort: let normalize_edit_file_args try to recover it
    normalized_args = {
        "relative_path": relative_path,
        "search_replace_blocks": block_text,
    }
    normalize_edit_file_args(normalized_args)
    canonical_blocks = str(normalized_args.get("search_replace_blocks") or "").strip()

    canonical_pairs = _extract_edit_file_search_replace_pairs(canonical_blocks)
    if canonical_pairs:
        _validate_pairs(canonical_pairs, relative_path, index)
        return canonical_blocks.rstrip()

    # If unrecoverable, check if it was just empty SEARCH/REPLACE keywords
    parse_diag = normalized_args.get("__aix_edit_file_parse_diag") or {}
    parse_class = str(parse_diag.get("parse_class") or "").strip()

    if parse_class == "unrecoverable":
        reduced = re.sub(r"[\s:]+", "", stripped).upper()
        if reduced in {"SEARCHREPLACE", "SEARCH", "REPLACE", ""}:
            _raise_error(
                "MUTATION_REQUIRED_BUT_ABSENT",
                relative_path,
                block_index=index,
                reason="edit_file_missing_semantic_mutation",
                snippet=stripped[:80] or None,
            )

    _raise_error(
        "EDIT_FILE_MALFORMED_BLOCK",
        relative_path,
        reason="missing_search_or_replace",
        block_index=index,
        snippet=(str(parse_diag.get("payload_snippet") or "").strip()[:160] or block_text[:160]),
        parse_class=parse_class or None,
    )


def _validate_edit_file_search_replace_blocks(*, payload: Any, relative_path: str) -> str:
    """Validate and normalize edit file search/replace blocks into canonical string format.

    Accepts list[dict|str], structured payload, or raw string. Returns canonical
    <<<<<<< SEARCH... =======... >>>>>>> REPLACE blocks joined by newlines.
    """
    if payload in (None, "", []):
        _raise_error("EDIT_FILE_EMPTY", relative_path, reason="no_blocks")

    # Case 1: List of blocks
    if isinstance(payload, list):
        normalized_blocks: list[str] = []
        for index, item in enumerate(payload):
            if isinstance(item, dict):
                normalized_blocks.append(_process_dict_block(item, relative_path, index))
            else:
                normalized_blocks.append(_process_str_block(item, relative_path, index))

        if not normalized_blocks:
            _raise_error("EDIT_FILE_EMPTY", relative_path, reason="no_blocks")
        return "\n".join(normalized_blocks) + "\n"

    # Case 2: Structured payload that can be converted directly
    structured = _structured_search_replace_blocks_to_canonical(payload)
    if structured is not None:
        pairs = _extract_structured_search_replace_pairs(payload) or []
        for index, (search, _) in enumerate(pairs):
            _validate_search_not_empty(search, relative_path, index)
        return structured

    # Case 3: Raw string payload
    if not isinstance(payload, str):
        _raise_error(
            "EDIT_FILE_MALFORMED_BLOCK",
            relative_path,
            reason="unsupported_payload_type",
            payload_type=type(payload).__name__,
        )

    text = str(payload or "")
    if not text.strip():
        _raise_error("EDIT_FILE_EMPTY", relative_path, reason="empty_block", block_index=0)

    normalized = _normalize_edit_file_search_replace_blocks(text) or text
    pairs = _extract_edit_file_search_replace_pairs(normalized)

    if not pairs:
        if _RE_EMPTY_MUTATION.match(text) or _RE_EMPTY_MUTATION_FENCED.match(normalized.strip()):
            _raise_error("EDIT_FILE_EMPTY_SEARCH", relative_path, block_index=0)
        _raise_error(
            "EDIT_FILE_MALFORMED_BLOCK",
            relative_path,
            reason="missing_search_or_replace",
            block_index=0,
            snippet=normalized[:160],
        )

    for index, (search, _) in enumerate(pairs):
        _validate_search_not_empty(search, relative_path, index)

    return normalized


def _normalize_planner_grounding_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return ""
    normalized_lines = [line.rstrip(" \t") for line in normalized.split("\n")]
    normalized = "\n".join(normalized_lines)
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


_CONTRACT_VIOLATION_CODES = {
    "MUTATION_INVALID_FUNCTION_SHAPE",
    "MUTATION_INVALID_FUNCTION_REPLACEMENT",
    "MUTATION_FRAGMENT_REJECTED",
}


def _raise_replace_error(code: str, relative_path: str, **extra_context) -> None:
    ctx = {"file": relative_path}
    ctx.update({k: v for k, v in extra_context.items() if v is not None})
    raise PlannerSemanticError(code=code, context=ctx)


def _validate_replace_function_candidate_or_raise(
    *,
    relative_path: str,
    expected_symbol: str | None,
    actual_symbol: str,
    replacement_text: str,
    workspace_root: str | None,
) -> dict[str, Any]:
    content_error = validate_step_content(
        tool="replace_function",
        args={"relative_path": relative_path, "replacement": replacement_text},
        workspace_root=workspace_root,
    )
    if content_error:
        content_code, content_context = _decode_validation_error(content_error)
        target = expected_symbol or None
        if content_code == "PLANNER_INVALID_REPLACEMENT_CONTENT":
            _raise_replace_error(
                "PLANNER_INVALID_REPLACEMENT_CONTENT",
                relative_path,
                target_symbol=target,
                **dict(content_context or {}),
            )
        _raise_replace_error(
            "REPLACE_FUNCTION_INVALID",
            relative_path,
            reason=content_error,
            target_symbol=target,
        )

    qualified = actual_symbol or str(expected_symbol or "").strip()
    preflight = validate_replace_function_candidate(
        workspace_root=workspace_root,
        relative_path=relative_path,
        qualified_symbol=qualified,
        replacement=replacement_text,
    )

    if preflight.get("validated"):
        return preflight

    failures = list(preflight.get("dry_run_failures") or [])
    first_failure = dict(failures[0]) if failures else {}
    failure_code = str(first_failure.get("failure_code") or "REPLACE_FUNCTION_INVALID").strip()
    code = "REPLACE_FUNCTION_CONTRACT_VIOLATION" if failure_code in _CONTRACT_VIOLATION_CODES else failure_code

    _raise_replace_error(
        code,
        relative_path,
        target_symbol=expected_symbol or actual_symbol or None,
        reason=str(first_failure.get("detail") or "replace_function preflight failed"),
        suggested_fix=str(first_failure.get("suggested_fix") or "").strip() or None,
        shape_debug=dict(preflight.get("replacement_shape_debug") or {}),
    )


def _get_read_args(read_step: dict[str, Any] | None) -> dict[str, Any]:
    return (
        dict(read_step.get("args") or {})
        if isinstance(read_step, dict) and isinstance(read_step.get("args"), dict)
        else {}
    )


def _get_symbol_name(read_args: dict[str, Any], fallback: str) -> str:
    return str(
        read_args.get("symbol") or read_args.get("function_name") or read_args.get("symbol_name") or fallback or ""
    ).strip()


def _resolve_from_symbol_range(
    workspace_root: str, relative_path: str, symbol_name: str, diagnostics: dict[str, Any]
) -> str:
    try:
        symbol_range = resolve_python_symbol_range(
            file_path=Path(str(workspace_root)) / relative_path,
            symbol_name=symbol_name,
        )
    except Exception as exc:
        LOGGER.exception("planner_validation_symbol_range_failed")
        diagnostics["symbol_resolution_error"] = str(exc)
        return ""

    diagnostics["source_origin"] = "resolved_symbol_source"
    diagnostics["resolved_symbol_identity"] = str(symbol_range.qualified_name or symbol_range.symbol_name or "")
    diagnostics["line_range_used"] = {
        "start_line": int(symbol_range.start_line),
        "end_line": int(symbol_range.end_line),
    }
    return str(symbol_range.symbol_source or "").strip()


def _resolve_from_cache(
    read_files_content: dict[str, dict[str, Any]], relative_path: str, diagnostics: dict[str, Any]
) -> str:
    cached = dict(read_files_content.get(relative_path) or {})
    source = str(cached.get("text") or "")
    if source:
        diagnostics["source_origin"] = "cached_read_content"
        diagnostics["line_range_used"] = {
            "start_line": cached.get("start_line"),
            "end_line": cached.get("end_line"),
        }
    return source


def _resolve_replace_function_observed_source(
    *,
    workspace_root: str | None,
    relative_path: str,
    requested_symbol_name: str,
    read_step: dict[str, Any] | None,
    read_files_content: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    read_args = _get_read_args(read_step)
    observed_source = str((read_step or {}).get("output_content") or read_args.get("output_content") or "")

    diagnostics: dict[str, Any] = {
        "relative_path": relative_path,
        "requested_symbol_name": requested_symbol_name,
        "source_origin": "step_output_content" if observed_source else None,
    }

    if not observed_source and workspace_root:
        symbol_name = _get_symbol_name(read_args, requested_symbol_name)
        if symbol_name:
            observed_source = _resolve_from_symbol_range(workspace_root, relative_path, symbol_name, diagnostics)

    if not observed_source:
        observed_source = _resolve_from_cache(read_files_content, relative_path, diagnostics)

    diagnostics.update(
        inspect_observed_function_source(
            observed_source=observed_source,
            qualified_symbol=requested_symbol_name,
        )
    )
    return observed_source, diagnostics


def _validate_rewrite_preconditions(
    spec: Any,
    step: dict[str, Any],
    observed_symbol_source: str,
    tenant_id: str | None,
    project_id: str | None,
    correlation_id: str | None,
    provider: str | None,
    workspace_root: str | None,
) -> None:
    """Validate all preconditions before attempting rewrite."""
    if spec is None:
        raise PlannerSemanticError(
            code="PLANNER_REPLACE_FUNCTION_SPEC_REQUIRED",
            context={
                "file": _step_relative_path(step),
                "reason": "replace_function planner output must carry bounded rewrite spec fields only",
            },
        )
    if not str(observed_symbol_source or "").strip():
        raise PlannerSemanticError(
            code="REPLACE_FUNCTION_MISSING_PREREAD",
            context={
                "file": spec.relative_path,
                "target_symbol": spec.qualified_symbol,
                "reason": "replace_function rewrite requires same-file read_range(symbol) output_content",
            },
        )
    if not all([tenant_id, project_id, correlation_id, provider, workspace_root]):
        raise PlannerSemanticError(
            code="REPLACE_FUNCTION_REWRITE_CONTEXT_MISSING",
            context={"file": spec.relative_path, "target_symbol": spec.qualified_symbol},
        )


def _parse_observed_function_shape_or_raise(
    observed_symbol_source: str,
    spec: Any,
) -> Any:
    """Parse the observed function shape and raise on failure with diagnostics."""
    try:
        return parse_observed_function_shape(
            observed_source=observed_symbol_source,
            qualified_symbol=spec.qualified_symbol,
        )
    except ValueError as exc:
        observed_diagnostics = inspect_observed_function_source(
            observed_source=observed_symbol_source,
            qualified_symbol=spec.qualified_symbol,
        )
        LOGGER.warning(
            "replace_function_observed_source_invalid",
            extra={
                "relative_path": spec.relative_path,
                "requested_symbol_name": spec.qualified_symbol,
                "resolved_symbol_identity": observed_diagnostics.get("resolved_symbol_identity"),
                "raw_observed_source_payload": observed_diagnostics.get("raw_observed_source_payload"),
                "line_range_used": observed_diagnostics.get("line_range_used"),
                "contains_exactly_one_function": observed_diagnostics.get("contains_exactly_one_function"),
                "contains_sibling_defs_or_classes": observed_diagnostics.get("contains_sibling_defs_or_classes"),
                "contains_truncation_markers": observed_diagnostics.get("contains_truncation_markers"),
                "contains_wrapping_text": observed_diagnostics.get("contains_wrapping_text"),
                "payload_preview_first_20_lines": observed_diagnostics.get("payload_preview_first_20_lines"),
            },
        )
        raise PlannerSemanticError(
            code="REPLACE_FUNCTION_OBSERVED_SOURCE_INVALID",
            context={
                "file": spec.relative_path,
                "target_symbol": spec.qualified_symbol,
                "reason": str(exc),
                "observed_source_contract": {
                    "line_range_used": observed_diagnostics.get("line_range_used"),
                    "contains_exactly_one_function": observed_diagnostics.get("contains_exactly_one_function"),
                    "contains_sibling_defs_or_classes": observed_diagnostics.get("contains_sibling_defs_or_classes"),
                    "contains_truncation_markers": observed_diagnostics.get("contains_truncation_markers"),
                    "contains_wrapping_text": observed_diagnostics.get("contains_wrapping_text"),
                    "resolved_symbol_identity": observed_diagnostics.get("resolved_symbol_identity"),
                    "raw_observed_source_payload": observed_diagnostics.get("raw_observed_source_payload"),
                },
            },
        ) from exc


def _create_attempt_trace(
    attempt: int,
    spec: Any,
    observed_source_hash: str,
    rewrite_error: str | None = None,
) -> dict[str, Any]:
    """Create initial attempt trace for logging."""
    return {
        "attempt": attempt + 1,
        "qualified_symbol": spec.qualified_symbol,
        "observed_source_hash": observed_source_hash,
        "same_grounded_source_reused": True,
        "validation_error_input": rewrite_error,
    }


def _handle_bridge_error(
    exc: Exception,
    spec: Any,
    attempt_trace: dict[str, Any],
    rewrite_attempt_traces: list[dict[str, Any]],
) -> None:
    """Handle PlannerBridgeError during rewrite invocation."""
    raise PlannerSemanticError(
        code="REWRITE_MODEL_TRANSPORT_FAILURE",
        context={
            "file": spec.relative_path,
            "target_symbol": spec.qualified_symbol,
            "reason": str(exc),
            "rewrite_attempt_traces": rewrite_attempt_traces + [attempt_trace],
        },
    ) from exc


def _handle_response_validation_error(
    exc: Exception,
    spec: Any,
    attempt: int,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    observed_source_hash: str,
    attempt_trace: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Handle ValueError from response parsing and persist trace."""
    rewrite_error = str(exc)
    last_code = getattr(exc, "code", "REWRITE_RESPONSE_INVALID")
    last_context = dict(getattr(exc, "context", {}) or {})
    validation_error_payload = {
        "code": last_code,
        "context": dict(last_context),
    }
    trace_ref = persist_replace_function_rewrite_trace(
        tenant_id=str(tenant_id),
        project_id=str(project_id),
        correlation_id=f"{correlation_id}:replace-function-rewrite",
        attempt=attempt + 1,
        observed_source_hash=observed_source_hash,
        qualified_symbol=spec.qualified_symbol,
        validation_error_input=rewrite_error,
        raw_response=last_context.get("raw_response"),
        parsed_response=None,
        validation_error=validation_error_payload,
    )
    attempt_trace["trace_id"] = trace_ref.trace_id
    attempt_trace["trace_ref"] = trace_ref.trace_ref
    attempt_trace["validation_error_code"] = last_code
    attempt_trace["reason_summary"] = str(last_context.get("reason") or last_code)
    return rewrite_error, last_code


def _handle_placeholder_markers_error(
    spec: Any,
    replacement_text: str,
    attempt: int,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    observed_source_hash: str,
    rewrite_diagnostics: dict[str, Any],
    attempt_trace: dict[str, Any],
) -> str:
    """Handle placeholder markers in replacement text and persist trace."""
    placeholder_markers = placeholder_markers_in_text(replacement_text)
    rewrite_error = "placeholder_or_elided_replacement_body markers=" + json.dumps(
        placeholder_markers, ensure_ascii=True, sort_keys=True
    )
    last_code = "PLANNER_INVALID_REPLACEMENT_CONTENT"
    last_context = {
        "file": spec.relative_path,
        "target_symbol": spec.qualified_symbol,
        "reason": "rewrite output contained placeholder or elided body markers",
        "placeholder_markers": placeholder_markers,
    }
    validation_error_payload = {
        "code": last_code,
        "context": dict(last_context),
    }
    trace_ref = persist_replace_function_rewrite_trace(
        tenant_id=str(tenant_id),
        project_id=str(project_id),
        correlation_id=f"{correlation_id}:replace-function-rewrite",
        attempt=attempt + 1,
        observed_source_hash=observed_source_hash,
        qualified_symbol=spec.qualified_symbol,
        validation_error_input=rewrite_error,
        raw_response=rewrite_diagnostics.get("rewrite_response_raw"),
        parsed_response=rewrite_diagnostics.get("rewrite_response_parsed"),
        validation_error=validation_error_payload,
    )
    attempt_trace["trace_id"] = trace_ref.trace_id
    attempt_trace["trace_ref"] = trace_ref.trace_ref
    attempt_trace["validation_error_code"] = last_code
    attempt_trace["reason_summary"] = str(last_context.get("reason") or last_code)
    return rewrite_error


def _build_successful_rewrite_result(
    step: dict[str, Any],
    spec: Any,
    replacement_text: str,
    attempt: int,
    observed_source_hash: str,
    rewrite_response: Any,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    rewrite_diagnostics: dict[str, Any],
    rewrite_attempt_traces: list[dict[str, Any]],
    attempt_trace: dict[str, Any],
) -> dict[str, Any]:
    """Build and return successful rewrite step result."""
    trace_ref = persist_replace_function_rewrite_trace(
        tenant_id=str(tenant_id),
        project_id=str(project_id),
        correlation_id=f"{correlation_id}:replace-function-rewrite",
        attempt=attempt + 1,
        observed_source_hash=observed_source_hash,
        qualified_symbol=spec.qualified_symbol,
        validation_error_input=None,
        raw_response=rewrite_diagnostics.get("rewrite_response_raw"),
        parsed_response=rewrite_diagnostics.get("rewrite_response_parsed"),
        validation_error=None,
    )
    attempt_trace["trace_id"] = trace_ref.trace_id
    attempt_trace["trace_ref"] = trace_ref.trace_ref
    attempt_trace["validation_error_code"] = None
    attempt_trace["reason_summary"] = "rewrite_valid"

    step_args = {
        "relative_path": spec.relative_path,
        "qualified_symbol": spec.qualified_symbol,
        "replacement": replacement_text,
    }
    rewrite_generation = {
        "source": "grounded_symbol_rewrite",
        "attempts_used": attempt + 1,
        "qualified_symbol": spec.qualified_symbol,
        "observed_source_hash": observed_source_hash,
        "edit_intent": spec.edit_intent,
        "preserve_constraints": list(spec.preserve_constraints),
        "line_targets": list(spec.line_targets),
        "notes": list(rewrite_response.notes),
        "trace_id": trace_ref.trace_id,
        "trace_ref": trace_ref.trace_ref,
        "attempt_traces": rewrite_attempt_traces + [attempt_trace],
    }
    updated = dict(step)
    updated["args"] = step_args
    updated["rewrite_generation"] = rewrite_generation
    return updated


def _handle_validation_error(
    exc: Exception,
    spec: Any,
    attempt: int,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    observed_source_hash: str,
    rewrite_diagnostics: dict[str, Any],
    attempt_trace: dict[str, Any],
) -> str:
    """Handle validation error during candidate validation and persist trace."""
    rewrite_error = f"{exc.code} detail={json.dumps(exc.context, ensure_ascii=True, sort_keys=True)}"
    last_code = exc.code
    last_context = dict(exc.context)
    validation_error_payload = {
        "code": last_code,
        "context": dict(last_context),
    }
    trace_ref = persist_replace_function_rewrite_trace(
        tenant_id=str(tenant_id),
        project_id=str(project_id),
        correlation_id=f"{correlation_id}:replace-function-rewrite",
        attempt=attempt + 1,
        observed_source_hash=observed_source_hash,
        qualified_symbol=spec.qualified_symbol,
        validation_error_input=rewrite_error,
        raw_response=rewrite_diagnostics.get("rewrite_response_raw"),
        parsed_response=rewrite_diagnostics.get("rewrite_response_parsed"),
        validation_error=validation_error_payload,
    )
    attempt_trace["trace_id"] = trace_ref.trace_id
    attempt_trace["trace_ref"] = trace_ref.trace_ref
    attempt_trace["validation_error_code"] = last_code
    attempt_trace["reason_summary"] = str(last_context.get("reason") or last_code)
    return rewrite_error


def _materialize_replace_function_rewrite(
    *,
    step: dict[str, Any],
    packet: PlannerSlicePacketCurrent | dict[str, Any] | None,
    workspace_root: str | None,
    observed_symbol_source: str,
    tenant_id: str | None,
    project_id: str | None,
    correlation_id: str | None,
    provider: str | None,
    model: str | None,
    authorization: str | None,
) -> dict[str, Any]:
    # Validate preconditions
    spec = extract_replace_function_rewrite_spec(step)
    _validate_rewrite_preconditions(
        spec=spec,
        step=step,
        observed_symbol_source=observed_symbol_source,
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        provider=provider,
        workspace_root=workspace_root,
    )

    # Parse observed function shape
    observed_shape = _parse_observed_function_shape_or_raise(
        observed_symbol_source=observed_symbol_source,
        spec=spec,
    )

    # Initialize state for rewrite attempts
    rewrite_error: str | None = None
    last_context: dict[str, Any] | None = None
    last_code: str | None = None
    rewrite_attempt_traces: list[dict[str, Any]] = []
    observed_source_hash = observed_source_sha256(observed_source=observed_symbol_source)

    # Attempt rewrite twice
    for attempt in range(2):
        attempt_trace = _create_attempt_trace(
            attempt=attempt,
            spec=spec,
            observed_source_hash=observed_source_hash,
            rewrite_error=rewrite_error,
        )

        # Invoke rewrite service
        try:
            rewrite_response, rewrite_diagnostics = invoke_replace_function_rewrite(
                tenant_id=str(tenant_id),
                project_id=str(project_id),
                correlation_id=f"{correlation_id}:replace-function-rewrite:{attempt + 1}",
                workspace_root=str(workspace_root),
                observed_source=observed_symbol_source,
                spec=spec,
                shape=observed_shape,
                provider=str(provider),
                model=str(model or "").strip() or None,
                authorization=authorization,
                validation_error=rewrite_error,
            )
            replacement_text = str(rewrite_response.replacement or "").strip()
        except PlannerBridgeError as exc:
            _handle_bridge_error(exc, spec, attempt_trace, rewrite_attempt_traces)
        except ValueError as exc:
            rewrite_error, last_code = _handle_response_validation_error(
                exc=exc,
                spec=spec,
                attempt=attempt,
                tenant_id=str(tenant_id),
                project_id=str(project_id),
                correlation_id=str(correlation_id),
                observed_source_hash=observed_source_hash,
                attempt_trace=attempt_trace,
            )
            rewrite_attempt_traces.append(attempt_trace)
            continue

        # Check for placeholder markers
        if placeholder_markers_in_text(replacement_text):
            rewrite_error = _handle_placeholder_markers_error(
                spec=spec,
                replacement_text=replacement_text,
                attempt=attempt,
                tenant_id=str(tenant_id),
                project_id=str(project_id),
                correlation_id=str(correlation_id),
                observed_source_hash=observed_source_hash,
                rewrite_diagnostics=rewrite_diagnostics,
                attempt_trace=attempt_trace,
            )
            rewrite_attempt_traces.append(attempt_trace)
            continue

        # Validate replacement candidate
        try:
            _validate_replace_function_candidate_or_raise(
                relative_path=spec.relative_path,
                expected_symbol=spec.qualified_symbol,
                actual_symbol=spec.qualified_symbol,
                replacement_text=replacement_text,
                workspace_root=workspace_root,
            )
            # Success: build and return result
            return _build_successful_rewrite_result(
                step=step,
                spec=spec,
                replacement_text=replacement_text,
                attempt=attempt,
                observed_source_hash=observed_source_hash,
                rewrite_response=rewrite_response,
                tenant_id=str(tenant_id),
                project_id=str(project_id),
                correlation_id=str(correlation_id),
                rewrite_diagnostics=rewrite_diagnostics,
                rewrite_attempt_traces=rewrite_attempt_traces,
                attempt_trace=attempt_trace,
            )
        except PlannerSemanticError as exc:
            rewrite_error = _handle_validation_error(
                exc=exc,
                spec=spec,
                attempt=attempt,
                tenant_id=str(tenant_id),
                project_id=str(project_id),
                correlation_id=str(correlation_id),
                observed_source_hash=observed_source_hash,
                rewrite_diagnostics=rewrite_diagnostics,
                attempt_trace=attempt_trace,
            )
            last_code = exc.code
            last_context = dict(exc.context)
            rewrite_attempt_traces.append(attempt_trace)
            continue

    # All attempts failed
    failure_context = {
        "file": spec.relative_path,
        "target_symbol": spec.qualified_symbol,
        "reason": "grounded rewrite generator could not produce a valid full replacement body",
    }
    failure_context.update(dict(last_context or {}))
    failure_context["rewrite_attempt_traces"] = rewrite_attempt_traces
    failure_context["observed_source_hash"] = observed_source_hash
    raise PlannerSemanticError(
        code=last_code or "PLANNER_INVALID_REPLACEMENT_CONTENT",
        context=failure_context,
    )


def _resolve_execution_lane(packet: PlannerSlicePacketCurrent | dict[str, Any] | None) -> str:
    """Resolve execution lane from packet, inferring if necessary."""
    execution_lane = str(_planner_packet_tool_policy_value(packet, "execution_lane") or "").strip()
    if not execution_lane:
        inferred_target_file = str(_planner_packet_value(packet, "target_file") or "").strip()
        inferred_validation_command = str(_planner_packet_value(packet, "validation_command") or "").strip()
        inferred_mutation_kind = str(_planner_packet_value(packet, "mutation_kind") or "").strip()
        if inferred_target_file and (inferred_validation_command or inferred_mutation_kind):
            execution_lane = "mutation"
    return execution_lane


def _resolve_allowed_mutation_tools(
    packet: PlannerSlicePacketCurrent | dict[str, Any] | None, execution_lane: str
) -> set[str]:
    """Resolve allowed mutation tools from packet or defaults."""
    allowed_mutation_tools = {
        str(item).strip()
        for item in list(_planner_packet_tool_policy_value(packet, "allowed_mutation_tools") or [])
        if str(item).strip()
    }
    if execution_lane == "mutation" and not allowed_mutation_tools:
        allowed_mutation_tools = {
            str(item).strip() for item in list(get_code_change_allowed_mutation_tools()) if str(item).strip()
        }
    return allowed_mutation_tools


def _initialize_validation_state(
    packet: PlannerSlicePacketCurrent | dict[str, Any] | None,
    planner_mode: str | None,
) -> tuple[str, str, set[str], dict[str, Any], bool, bool, bool, bool, bool]:
    """Initialize state variables for planner validation."""
    resolved_planner_mode = _resolve_planner_mode(packet, planner_mode=planner_mode)
    execution_lane = _resolve_execution_lane(packet)
    allowed_mutation_tools = _resolve_allowed_mutation_tools(packet, execution_lane)

    expected_target = str(_planner_packet_value(packet, "target_file") or "").strip().replace("\\", "/").strip("/")
    grounded_symbol = _planner_packet_codeintel_symbol(packet)
    anchor_window = _planner_packet_codeintel_anchor_window(packet)
    grounded_symbol_source = _planner_packet_compact_anchor_snippet(packet)

    planner_packet_is_v2 = is_v2_planner_packet(
        packet if isinstance(packet, dict) else packet.model_dump() if packet else {}
    )
    form_a_only_lane = _is_codeintel_325_form_a_only_lane(packet)
    packet_bound_mode = resolved_planner_mode == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION

    requires_symbol_discovery = bool(
        packet_bound_mode and grounded_symbol and expected_target and anchor_window is None and not form_a_only_lane
    )
    requires_direct_anchor_read = bool(packet_bound_mode and expected_target and anchor_window is not None)

    return (
        resolved_planner_mode,
        execution_lane,
        allowed_mutation_tools,
        {
            "expected_target": expected_target,
            "grounded_symbol": grounded_symbol,
            "anchor_window": anchor_window,
            "anchor_signature": _planner_packet_codeintel_signature(packet),
            "grounded_symbol_source": grounded_symbol_source,
            "planner_packet_is_v2": planner_packet_is_v2,
            "form_a_only_lane": form_a_only_lane,
            "packet_bound_mode": packet_bound_mode,
            "requires_symbol_discovery": requires_symbol_discovery,
            "requires_direct_anchor_read": requires_direct_anchor_read,
        },
        packet_bound_mode,
        requires_symbol_discovery,
        requires_direct_anchor_read,
        planner_packet_is_v2,
        form_a_only_lane,
    )


def _initialize_read_buffers(
    expected_target: str, grounded_symbol_source: str, requires_direct_anchor_read: bool
) -> tuple[set[str], dict[str, dict[str, Any]]]:
    """Initialize read context tracking and content buffers."""
    read_context_paths: set[str] = set()
    read_files_content: dict[str, dict[str, Any]] = {}
    if expected_target and grounded_symbol_source and not requires_direct_anchor_read:
        read_context_paths.add(expected_target)
        read_files_content[expected_target] = {"text": grounded_symbol_source, "step_idx": 0}
    return read_context_paths, read_files_content


def _check_anchor_window_read_completed(
    step_index: int,
    step: dict[str, Any],
    step_relative_path: str,
    expected_target: str,
    tool: str,
    anchor_window: tuple[int, int] | None,
    requires_direct_anchor_read: bool,
) -> bool:
    """Check if anchor window read is completed for this step."""
    if (
        not requires_direct_anchor_read
        or step_relative_path != expected_target
        or tool != "read_range"
        or anchor_window is None
    ):
        return False
    args = dict(step.get("args") or {})
    step_start = args.get("start_line")
    step_end = args.get("end_line")
    try:
        normalized_step_window = (int(step_start), int(step_end))
    except (TypeError, ValueError):
        normalized_step_window = None
    return normalized_step_window == anchor_window


def _process_read_step(
    step: dict[str, Any],
    step_index: int,
    step_relative_path: str,
    tool: str,
    workspace_root: str | None,
    read_files_content: dict[str, dict[str, Any]],
) -> None:
    """Process read_file or read_range step and populate read_files_content."""
    if tool not in {"read_file", "read_range"} or not step_relative_path:
        return

    args = dict(step.get("args") or {})
    read_result = step.get("result")

    if isinstance(read_result, dict) and isinstance(read_result.get("content"), str):
        stamped_content = str(read_result.get("content") or "")
        read_files_content[step_relative_path] = {
            "text": stamped_content,
            "step_idx": int(step_index),
            "start_line": read_result.get("start_line"),
            "end_line": read_result.get("end_line"),
        }
        step["output_content"] = stamped_content
    else:
        simulated_read = _simulate_last_read_buffer_for_plan_step(
            workspace_root=workspace_root,
            relative_path=step_relative_path,
            read_tool=tool,
            read_args=args,
            metadata={},
        )
        if simulated_read is not None:
            stamped_content = str(simulated_read.get("content") or "")
            read_files_content[step_relative_path] = {
                "text": stamped_content,
                "step_idx": int(step_index),
                "start_line": simulated_read.get("start_line"),
                "end_line": simulated_read.get("end_line"),
            }
            step["output_content"] = stamped_content


def _validate_replace_function_step(
    step_index: int,
    step: dict[str, Any],
    args: dict[str, Any],
    step_relative_path: str,
    tool: str,
    allowed_mutation_tools: set[str],
    expected_target: str,
    grounded_symbol: str,
    packet: PlannerSlicePacketCurrent | dict[str, Any] | None,
    resolved_planner_mode: str,
    workspace_root: str | None,
    read_files_content: dict[str, dict[str, Any]],
    tenant_id: str | None,
    project_id: str | None,
    correlation_id: str | None,
    provider: str | None,
    model: str | None,
    authorization: str | None,
    steps: list[Any],
) -> tuple[bool, dict[str, Any]]:
    """Validate replace_function step and perform rewrite if in packet-bound mode. Returns (is_mutation, updated_step)."""
    if tool != "replace_function" or tool not in allowed_mutation_tools:
        return False, step

    relative_path = step_relative_path
    expected_symbol = str(_planner_packet_value(packet, "target_symbol") or "").strip() or grounded_symbol
    actual_symbol = str(
        args.get("function_name") or args.get("qualified_symbol") or args.get("target_symbol") or ""
    ).strip()
    replacement_text = str(args.get("replacement") or "")

    if not relative_path and expected_target:
        relative_path = expected_target
    if not actual_symbol and expected_symbol:
        actual_symbol = expected_symbol

    if relative_path != expected_target:
        raise PlannerSemanticError(
            code="EDIT_FILE_WRONG_TARGET",
            context={"expected": expected_target, "got": relative_path},
        )
    if expected_symbol and actual_symbol and actual_symbol != expected_symbol:
        raise PlannerSemanticError(
            code="REPLACE_FUNCTION_WRONG_SYMBOL",
            context={"expected": expected_symbol, "got": actual_symbol, "file": relative_path},
        )

    if resolved_planner_mode == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION:
        previous_step = steps[step_index - 1] if step_index > 0 and isinstance(steps[step_index - 1], dict) else None
        previous_tool = str(previous_step.get("tool") or "").strip() if isinstance(previous_step, dict) else ""
        previous_args = (
            dict(previous_step.get("args") or {})
            if isinstance(previous_step, dict) and isinstance(previous_step.get("args"), dict)
            else {}
        )
        previous_symbol = str(previous_args.get("symbol") or (previous_step or {}).get("symbol") or "").strip()

        if previous_tool != "read_range" or _step_relative_path(previous_step) != relative_path:
            raise PlannerSemanticError(
                code="REPLACE_FUNCTION_MISSING_PREREAD",
                context={
                    "file": relative_path,
                    "target_symbol": expected_symbol or actual_symbol or None,
                    "reason": "replace_function must be preceded by same-file read_range on the grounded symbol",
                },
            )
        if expected_symbol and previous_symbol and previous_symbol != expected_symbol:
            raise PlannerSemanticError(
                code="REPLACE_FUNCTION_MISSING_PREREAD",
                context={
                    "file": relative_path,
                    "target_symbol": expected_symbol,
                    "read_symbol": previous_symbol,
                    "reason": "replace_function preread must use the grounded target symbol",
                },
            )

        observed_symbol_source, observed_source_diagnostics = _resolve_replace_function_observed_source(
            workspace_root=workspace_root,
            relative_path=relative_path,
            requested_symbol_name=actual_symbol or expected_symbol,
            read_step=previous_step,
            read_files_content=read_files_content,
        )
        if not observed_symbol_source.strip():
            LOGGER.warning("replace_function_observed_source_missing", extra=observed_source_diagnostics)
        if replacement_text.strip():
            raise PlannerSemanticError(
                code="PLANNER_REPLACE_FUNCTION_DIRECT_BODY_FORBIDDEN",
                context={
                    "file": relative_path,
                    "target_symbol": expected_symbol or actual_symbol or None,
                    "reason": "planner must emit bounded replace_function rewrite spec only; task-runner materializes final replacement body",
                },
            )

        _emit_runtime_stage(
            "rewrite_stage_entered",
            "entered grounded replace_function rewrite stage",
            {
                "relative_path": relative_path,
                "target_symbol": actual_symbol or expected_symbol or None,
            },
        )
        if observed_symbol_source.strip():
            _emit_runtime_stage(
                "observed_source_present",
                "observed grounded symbol source is present for rewrite materialization",
                {
                    "relative_path": relative_path,
                    "target_symbol": actual_symbol or expected_symbol or None,
                },
            )

        step = _materialize_replace_function_rewrite(
            step=step,
            packet=packet,
            workspace_root=workspace_root,
            observed_symbol_source=observed_symbol_source,
            tenant_id=tenant_id,
            project_id=project_id,
            correlation_id=correlation_id,
            provider=provider,
            model=model,
            authorization=authorization,
        )

        _emit_runtime_stage(
            "runtime_payload_materialized",
            "materialized replace_function runtime payload from grounded rewrite stage",
            {
                "relative_path": relative_path,
                "target_symbol": actual_symbol or expected_symbol or None,
            },
        )

        args = dict(step.get("args") or {})
        actual_symbol = str(
            args.get("function_name") or args.get("qualified_symbol") or args.get("target_symbol") or ""
        ).strip()
        replacement_text = str(args.get("replacement") or "")

    _validate_replace_function_candidate_or_raise(
        relative_path=relative_path,
        expected_symbol=expected_symbol,
        actual_symbol=actual_symbol or expected_symbol,
        replacement_text=replacement_text,
        workspace_root=workspace_root,
    )

    return True, step


def _validate_edit_file_step(
    step_index: int,
    step: dict[str, Any],
    args: dict[str, Any],
    step_relative_path: str,
    tool: str,
    allowed_mutation_tools: set[str],
    expected_target: str,
    grounded_symbol: str,
    form_a_only_lane: bool,
    packet: PlannerSlicePacketCurrent | dict[str, Any] | None,
    planner_packet_is_v2: bool,
    read_context_paths: set[str],
    workspace_root: str | None,
    read_files_content: dict[str, dict[str, Any]],
    grounded_symbol_source: str,
    requires_direct_anchor_read: bool,
    file_cache: dict[str, str],
    received_tools: list[str],
    steps: list[Any],
) -> bool:
    """Validate edit_file step. Returns True if it is a real mutation."""
    if tool != "edit_file" or tool not in allowed_mutation_tools:
        return False

    relative_path = step_relative_path
    target_symbol = str(_planner_packet_value(packet, "target_symbol") or "").strip() or grounded_symbol

    if form_a_only_lane and str(args.get("grounding_source") or "").strip() == "read_range":
        raise PlannerSemanticError(
            code="FORM_B_DISABLED_FOR_PROVING_LANE",
            context={
                "file": relative_path,
                "task_number": 325,
                "reason": "codeintel #325 proving lane is temporarily restricted to Form A anchor-grounded edits only",
            },
        )

    if relative_path != expected_target:
        raise PlannerSemanticError(
            code="EDIT_FILE_WRONG_TARGET",
            context={"expected": expected_target, "got": relative_path},
        )

    if (
        not planner_packet_is_v2
        and bool(_planner_packet_value(packet, "enforce_read_before_write"))
        and relative_path not in read_context_paths
    ):
        raise PlannerSemanticError(
            code="EDIT_FILE_WITHOUT_READ",
            context={
                "file": relative_path,
                "reason": "read_before_write_enforced",
                "required_tools": ["read_file", "read_range"],
            },
        )

    v2_structured_edit = planner_packet_is_v2 and _is_v2_structured_edit_file_args(args)
    if v2_structured_edit:
        text = ""
    else:
        text = _validate_edit_file_search_replace_blocks(
            payload=args.get("search_replace_blocks"),
            relative_path=relative_path,
        )

    if not workspace_root:
        _codeintel_anchor = _planner_packet_value(packet, "codeintel_anchor")
        raise PlannerSemanticError(
            code="MUTATION_REQUIRED_BUT_ABSENT",
            context={
                "target_file": _planner_packet_value(packet, "target_file"),
                "target_symbol": (
                    _planner_packet_value(packet, "target_symbol")
                    or getattr(_codeintel_anchor, "symbol", None)
                    or (_codeintel_anchor.get("symbol") if isinstance(_codeintel_anchor, dict) else None)
                ),
                "validation_command": _planner_packet_value(packet, "validation_command"),
                "received_tools": received_tools,
            },
        )

    if not v2_structured_edit and _is_noop_edit(args):
        _codeintel_anchor = _planner_packet_value(packet, "codeintel_anchor")
        raise PlannerSemanticError(
            code="MUTATION_REQUIRED_BUT_ABSENT",
            context={
                "target_file": _planner_packet_value(packet, "target_file"),
                "target_symbol": (
                    _planner_packet_value(packet, "target_symbol")
                    or getattr(_codeintel_anchor, "symbol", None)
                    or (_codeintel_anchor.get("symbol") if isinstance(_codeintel_anchor, dict) else None)
                ),
                "validation_command": _planner_packet_value(packet, "validation_command"),
                "received_tools": received_tools,
                "candidate_steps": [dict(item) for item in steps if isinstance(item, dict)],
            },
        )

    try:
        file_content = _get_target_file_content(
            workspace_root=str(workspace_root),
            relative_path=relative_path,
            file_cache=file_cache,
        )
    except PlannerSemanticError as exc:
        if exc.code != "EDIT_FILE_TARGET_NOT_FOUND":
            raise
        file_content = ""

    pairs = [] if v2_structured_edit else _extract_edit_file_search_replace_pairs(text or "")
    if not pairs and not v2_structured_edit:
        raise PlannerSemanticError(code="EDIT_FILE_EMPTY", context={"file": relative_path})

    if planner_packet_is_v2 and workspace_root:
        try:
            grounding = validate_v2_edit_against_live_file(
                workspace_root=workspace_root,
                args=args,
                live_file_content=file_content if file_content else None,
                planner_packet=dict(packet or {}),
                read_buffers=read_files_content,
            )
        except V2LiveFileGroundingError as exc:
            detail = dict(exc.detail)
            detail.setdefault("file", relative_path)
            if target_symbol:
                detail.setdefault("target_symbol", target_symbol)
            raise PlannerSemanticError(code=exc.code, context=detail) from exc
        pairs = [(str(block.search or ""), str(block.replace or "")) for block in grounding.blocks]
    else:
        read_entry = dict(read_files_content.get(relative_path) or {})
        read_content = str(read_entry.get("text") or "")
        grounded_target_content = grounded_symbol_source if expected_target and relative_path == expected_target else ""
        read_source = dict(args.get("read_source") or {}) if isinstance(args.get("read_source"), dict) else {}
        read_source_step_idx = read_source.get("step_idx")
        if (
            not requires_direct_anchor_read
            and isinstance(read_source_step_idx, int)
            and read_source_step_idx == 0
            and grounded_target_content
        ):
            read_content = grounded_target_content
            read_files_content[relative_path] = {"text": grounded_target_content, "step_idx": 0}

        for search, _replace in pairs:
            normalized_search = _normalize_planner_grounding_text(str(search or ""))
            normalized_read_content = _normalize_planner_grounding_text(read_content)
            normalized_grounded_target_content = _normalize_planner_grounding_text(grounded_target_content)
            if normalized_read_content and normalized_search not in normalized_read_content:
                if normalized_grounded_target_content and normalized_search in normalized_grounded_target_content:
                    read_files_content[relative_path] = {"text": grounded_target_content, "step_idx": 0}
                    continue
                raise PlannerSemanticError(
                    code="EDIT_FILE_SEARCH_NOT_FROM_READ",
                    context={
                        "search": str(search or "")[:100],
                        "file": relative_path,
                        "reason": "SEARCH not found in preceding read_range output",
                        "target_symbol": target_symbol or None,
                    },
                )

    can_validate_against_file = bool(file_content)
    if can_validate_against_file:
        for block_index, (search, replace) in enumerate(pairs):
            _validate_search_block(
                search=search,
                file_content=file_content,
                relative_path=relative_path,
                target_symbol=target_symbol or None,
            )
            applied_content = file_content.replace(search, replace, 1) if can_validate_against_file else None
            if can_validate_against_file and relative_path.endswith(".py"):
                is_valid, syntax_err = _validate_syntax_safe(applied_content)
                if not is_valid:
                    raise PlannerSemanticError(
                        code="EDIT_FILE_SYNTAX_ERROR",
                        context={
                            "file": relative_path,
                            "syntax_error": syntax_err,
                            "block_index": block_index,
                        },
                    )
            if can_validate_against_file and file_content == applied_content:
                raise PlannerSemanticError(
                    code="EDIT_FILE_NO_EFFECT",
                    context={
                        "file": relative_path,
                        "block_index": block_index,
                        "reason": "REPLACE identical to existing content after SEARCH",
                    },
                )

    return True


def _validate_planner_output(
    planner_result: dict[str, Any],
    packet: PlannerSlicePacketCurrent | dict[str, Any] | None,
    *,
    planner_mode: str | None = None,
    workspace_root: str | None = None,
    tenant_id: str | None = None,
    project_id: str | None = None,
    correlation_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    authorization: str | None = None,
) -> dict[str, Any]:
    (
        resolved_planner_mode,
        execution_lane,
        allowed_mutation_tools,
        state_dict,
        _packet_bound_mode,
        requires_symbol_discovery,
        requires_direct_anchor_read,
        planner_packet_is_v2,
        form_a_only_lane,
    ) = _initialize_validation_state(packet, planner_mode)

    if execution_lane not in {"mutation", "planning_safe", "mutation_repair"}:
        return planner_result

    steps = _normalize_planner_step_dialects(list(planner_result.get("steps") or []))
    planner_result["steps"] = steps

    read_context_paths, read_files_content = _initialize_read_buffers(
        state_dict["expected_target"],
        state_dict["grounded_symbol_source"],
        requires_direct_anchor_read,
    )

    file_cache: dict[str, str] = {}
    has_real_mutation = False
    received_tools: list[str] = []

    anchor_read_completed = False
    symbol_discovery_completed = bool(state_dict["grounded_symbol_source"])

    for step_index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue

        step["step_idx"] = int(step_index)
        tool = str(step.get("tool") or "").strip()
        args = dict(step.get("args") or {})
        step_relative_path = str(args.get("relative_path") or "").strip().replace("\\", "/").strip("/")

        if tool:
            received_tools.append(tool)

        # Check anchor window completion
        if requires_direct_anchor_read and not anchor_read_completed and _check_anchor_window_read_completed(
            step_index,
            step,
            step_relative_path,
            state_dict["expected_target"],
            tool,
            state_dict["anchor_window"],
            requires_direct_anchor_read,
        ):
            anchor_read_completed = True

        # Check symbol discovery
        if requires_symbol_discovery and tool == "search" and _search_step_matches_grounded_symbol(
            args=args,
            grounded_symbol=state_dict["grounded_symbol"],
            target_file=state_dict["expected_target"],
        ):
            symbol_discovery_completed = True

        # Validate anchor window precondition
        if (
            requires_direct_anchor_read
            and not anchor_read_completed
            and step_relative_path == state_dict["expected_target"]
            and tool in {"read_file", "read_range", "edit_file"}
        ):
            raise PlannerSemanticError(
                code="PLANNER_SKIPPED_ANCHOR_WINDOW_READ",
                context={
                    "symbol": state_dict["grounded_symbol"],
                    "signature": state_dict["anchor_signature"],
                    "file": state_dict["expected_target"],
                    "anchor_start_line": state_dict["anchor_window"][0]
                    if state_dict["anchor_window"] is not None
                    else None,
                    "anchor_end_line": state_dict["anchor_window"][1]
                    if state_dict["anchor_window"] is not None
                    else None,
                    "reason": "direct read_range on the canonical anchor window is required before target-file read/edit",
                    "attempted_tool": tool,
                    "required_tool": "read_range",
                },
            )

        # Validate symbol discovery precondition
        if (
            requires_symbol_discovery
            and not symbol_discovery_completed
            and step_relative_path == state_dict["expected_target"]
            and tool in {"read_file", "read_range", "edit_file"}
        ):
            raise PlannerSemanticError(
                code="PLANNER_SKIPPED_DISCOVERY",
                context={
                    "symbol": state_dict["grounded_symbol"],
                    "signature": state_dict["anchor_signature"],
                    "file": state_dict["expected_target"],
                    "reason": "search required before symbol-scoped read/edit when codeintel_anchor.symbol provided and no anchor window is available",
                    "attempted_tool": tool,
                    "required_tool": "search",
                },
            )

        # Process read steps
        _process_read_step(step, step_index, step_relative_path, tool, workspace_root, read_files_content)
        read_context_paths.add(step_relative_path) if tool in {
            "read_file",
            "read_range",
        } and step_relative_path else None

        # Validate replace_function step
        is_replace_mutation, step = _validate_replace_function_step(
            step_index,
            step,
            args,
            step_relative_path,
            tool,
            allowed_mutation_tools,
            state_dict["expected_target"],
            state_dict["grounded_symbol"],
            packet,
            resolved_planner_mode,
            workspace_root,
            read_files_content,
            tenant_id,
            project_id,
            correlation_id,
            provider,
            model,
            authorization,
            steps,
        )
        if is_replace_mutation:
            steps[step_index] = step
            has_real_mutation = True
            break

        # Validate edit_file step
        if _validate_edit_file_step(
            step_index,
            step,
            args,
            step_relative_path,
            tool,
            allowed_mutation_tools,
            state_dict["expected_target"],
            state_dict["grounded_symbol"],
            form_a_only_lane,
            packet,
            planner_packet_is_v2,
            read_context_paths,
            workspace_root,
            read_files_content,
            state_dict["grounded_symbol_source"],
            requires_direct_anchor_read,
            file_cache,
            received_tools,
            steps,
        ):
            has_real_mutation = True
            break

        # Check other mutation tools
        if is_code_mutation_tool(tool):
            has_real_mutation = True
            break

    if not has_real_mutation:
        return planner_result

    return planner_result


_CODEINTEL_TARGET_FILE = "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py"

_BASE_CONTRACT_LINES = [
    (
        "Requirement: For mutation lanes return either (1) steps containing at least one real mutation "
        "within the grounded target surface, or (2) failure_analysis explaining why mutation is impossible."
    ),
    "No-op edits and empty mutations violate the contract.",
    (
        "Structured edit contract: emit typed JSON steps only. The only valid mutation shape is "
        "edit_file with fields file, grounding_source, search_range, replacement_text for Form A, or file, grounding_source, read_step_idx, edit_range_within_read, replacement_text for Form B. "
        "Do not emit SEARCH:/REPLACE: headers, bare SEARCH/REPLACE labels, args.search_replace_blocks, "
        "or legacy tool/args wrappers."
    ),
]

_ERROR_HANDLERS: dict[str, Any] = {}


def _register(code: str):
    def deco(fn):
        _ERROR_HANDLERS[code] = fn
        return fn

    return deco


def _truncate_preview(text: str, limit: int = 50) -> str:
    text = str(text or "").replace("\n", "\\n").strip()
    return f"{text[:limit]}..." if len(text) > limit else text


def _add_header(repair_lines: list[str], prompt: str, error: PlannerSemanticError) -> None:
    context_json = json.dumps(error.context, sort_keys=True, ensure_ascii=True, default=str)
    repair_lines.extend(
        [
            str(prompt or "").rstrip(),
            "",
            "[SYSTEM][SEMANTIC_VALIDATION_REPAIR]",
            f"code={error.code}",
            f"context={context_json}",
        ]
    )
    repair_lines.extend(_BASE_CONTRACT_LINES)


def _add_codeintel_lines(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    file_path = str(ctx.get("file") or "").strip().replace("\\", "/")
    target_symbol = str(ctx.get("target_symbol") or "").strip()
    if file_path != _CODEINTEL_TARGET_FILE or not target_symbol:
        return
    repair_lines.extend(
        [
            "For codeintel #325, the only valid mutation shape is read_range on the grounded target symbol using file plus symbol only, then replace_function with file, qualified_symbol, edit_intent, preserve_constraints, and optional line_targets, then run_validation_command. The grounded rewrite stage will produce the final replacement body.",
            "Preserve whether the grounded function is def or async def, preserve the exact function name, preserve the method receiver shape, and preserve unchanged lines unless intentionally edited.",
            "Before emitting replace_function, self-check that edit_intent is concrete, preserve_constraints keep exact async/def kind, exact symbol name, exact receiver, and unchanged lines unless intentionally edited, and line_targets refer only to observed grounded lines or behaviors.",
            "The preread symbol and replace_function qualified_symbol must both equal the exact fully qualified grounded target symbol. Do not emit a bare name, class-only name, or a line-window read in place of read_range(symbol).",
            "If that self-check fails, emit failure_analysis instead of guessing; the grounded rewrite stage will regenerate one full body from the observed symbol source.",
        ]
    )


@_register("REPLACE_FUNCTION_MISSING_PREREAD")
def _handle_missing_preread(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    repair_lines.extend(
        [
            "SEMANTIC_VALIDATION_FAILED: REPLACE_FUNCTION_MISSING_PREREAD.",
            "For codeintel #325, replace_function must be preceded by same-file read_range on the grounded target symbol using file plus symbol only.",
            "Legal sequence: read_range -> replace_function -> run_validation_command.",
            "The read_range symbol and replace_function qualified_symbol must both equal the exact fully qualified grounded target symbol.",
            "Copy the full observed function from that read output, modify only the requested logic, and keep the rest of the function concrete.",
        ]
    )


@_register("PLANNER_INVALID_REPLACEMENT_CONTENT")
def _handle_invalid_replacement(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    repair_lines.extend(
        [
            "SEMANTIC_VALIDATION_FAILED: PLANNER_INVALID_REPLACEMENT_CONTENT.",
            "replace_function must carry a bounded rewrite spec only: edit_intent plus preserve_constraints and optional grounded line_targets.",
            "The grounded rewrite stage will generate the full function from read_range(symbol) output. Regenerate the spec from grounded source; do not emit replacement bodies, '#...', '...', omitted unchanged code, prose, fences, helpers, or placeholders.",
            "Self-check before emitting: bounded edit_intent, exact grounded symbol name, exact async/def kind, exact receiver preservation, and grounded line_targets only.",
        ]
    )


@_register("REPLACE_FUNCTION_INVALID")
def _handle_replace_invalid(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    repair_lines.extend(
        [
            "SEMANTIC_VALIDATION_FAILED: REPLACE_FUNCTION_INVALID.",
            "replace_function must not carry a direct replacement body on this lane; emit a bounded rewrite spec grounded to the same-file preread symbol instead.",
            "Preserve async/def kind, exact function name, method receiver shape, and unchanged concrete logic via preserve_constraints. Do not emit placeholder comments, TODOs, or elided body markers.",
        ]
    )


@_register("REPLACE_FUNCTION_CONTRACT_VIOLATION")
def _handle_contract_violation(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    repair_lines.extend(
        [
            "SEMANTIC_VALIDATION_FAILED: REPLACE_FUNCTION_CONTRACT_VIOLATION.",
            "Emit exactly one complete replacement function definition for the grounded symbol and nothing else.",
            "Preserve async/def kind, exact grounded function name, exact method receiver, and concrete unchanged logic. Do not emit prose, fences, helpers, or placeholders.",
        ]
    )


@_register("FORM_B_DISABLED_FOR_PROVING_LANE")
def _handle_form_b_disabled(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    repair_lines.extend(
        [
            "SEMANTIC_VALIDATION_FAILED: FORM_B_DISABLED_FOR_PROVING_LANE.",
            "For codeintel #325, Form B is temporarily disabled.",
            "Requirement: emit Form A only: edit_file with file, grounding_source=anchor, explicit search_range inside the compact anchor window, and replacement_text.",
            "If the compact anchor is insufficient for a safe bounded edit, emit failure_analysis with reason_code=INSUFFICIENT_CONTEXT_FOR_MUTATION.",
            "Do not emit grounding_source=read_range, read_step_idx, edit_range_within_read, or any read-grounded edit path.",
        ]
    )


@_register("EDIT_FILE_SEARCH_NOT_FOUND")
def _handle_search_not_found(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    relative_path = str(ctx.get("file") or "").strip()
    target_symbol = str(ctx.get("target_symbol") or "").strip()
    search_preview = _truncate_preview(ctx.get("search"))
    msg1 = (
        f"Your SEARCH block '{search_preview}' was not found in {relative_path}."
        if relative_path
        else f"Your edit_file.search '{search_preview}' was not found in the target file."
    )
    if relative_path and target_symbol:
        msg2 = f"Requirement: You MUST first run read_range on {relative_path} around symbol '{target_symbol}'."
    elif relative_path:
        msg2 = f"Requirement: You MUST first run read_range on {relative_path} around the target symbol."
    else:
        msg2 = "Requirement: You MUST first run read_range on the target file around the target symbol."
    msg3 = (
        f"SEARCH must contain '{target_symbol}' and at least 2 lines of context above/below it."
        if target_symbol
        else "SEARCH must contain the target symbol and at least 2 lines of context above/below it."
    )
    msg4 = (
        f"If read_range shows symbol '{target_symbol}' not in file, return failure_analysis explaining why."
        if target_symbol
        else "If read_range shows the symbol is not in file, return failure_analysis explaining why."
    )
    repair_lines.extend(
        [
            "SEMANTIC_VALIDATION_FAILED: EDIT_FILE_SEARCH_NOT_FOUND.",
            msg1,
            msg2,
            msg3,
            "Then construct Form B using that same-file read_range plus read_step_idx and edit_range_within_read selecting the exact changed lines inside the observed read output.",
            msg4,
            "Do not guess or reconstruct SEARCH text from memory. Use compact anchor text or exact observed read output only.",
        ]
    )


@_register("EDIT_FILE_UNGROUNDED_SEARCH")
def _handle_ungrounded_search(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    relative_path = str(ctx.get("file") or "").strip()
    search_preview = str(ctx.get("search") or "").replace("\n", "\\n").strip()
    msg1 = (
        f"Your SEARCH block '{search_preview}' in {relative_path} was not copied from compact anchor text or explicit same-file read output."
        if relative_path and search_preview
        else "Your SEARCH block was not copied from compact anchor text or explicit same-file read output."
    )
    msg2 = (
        f"Otherwise run read_range on {relative_path}, then emit Form B with read_step_idx and edit_range_within_read on that same-file read output."
        if relative_path
        else "Otherwise run read_range on the target file, then emit Form B with read_step_idx and edit_range_within_read on that same-file read output."
    )
    anchor_shape = (
        f'Anchor-direct retry shape: {{"steps":[{{"type":"edit_file","file":"{relative_path}","search":"<EXACT TEXT FROM COMPACT ANCHOR>","replace":"<UPDATED TEXT>"}},{{"type":"run_validation_command","command":"<DECLARED VALIDATION COMMAND>"}}]}}'
        if relative_path
        else 'Anchor-direct retry shape: {"steps":[{"type":"edit_file","file":"<target file>","search":"<EXACT TEXT FROM COMPACT ANCHOR>","replace":"<UPDATED TEXT>"},{"type":"run_validation_command","command":"<DECLARED VALIDATION COMMAND>"}]}'
    )
    read_shape = (
        f'Read-then-edit retry shape: {{"steps":[{{"type":"read_range","file":"{relative_path}","start_line":<START>,"end_line":<END>}},{{"type":"edit_file","file":"{relative_path}","grounding_source":"read_range","read_step_idx":0,"edit_range_within_read":{{"start_line":<START_IN_READ>,"end_line":<END_IN_READ>}},"replace":"<UPDATED TEXT>"}},{{"type":"run_validation_command","command":"<DECLARED VALIDATION COMMAND>"}}]}}'
        if relative_path
        else 'Read-then-edit retry shape: {"steps":[{"type":"read_range","file":"<target file>","start_line":<START>,"end_line":<END>},{"type":"edit_file","file":"<target file>","grounding_source":"read_range","read_step_idx":0,"edit_range_within_read":{"start_line":<START_IN_READ>,"end_line":<END_IN_READ>},"replace":"<UPDATED TEXT>"},{"type":"run_validation_command","command":"<DECLARED VALIDATION COMMAND>"}]}'
    )
    repair_lines.extend(
        [
            "SEMANTIC_VALIDATION_FAILED: EDIT_FILE_UNGROUNDED_SEARCH.",
            msg1,
            "Requirement: If compact anchor text is sufficient, copy SEARCH verbatim from that anchor only.",
            msg2,
            anchor_shape,
            read_shape,
            "Do not invent, normalize, abbreviate, or paraphrase SEARCH text, and do not emit SEARCH at all for Form B.",
        ]
    )


@_register("EDIT_FILE_SEARCH_INSUFFICIENT_CONTEXT")
def _handle_insufficient_context(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    relative_path = str(ctx.get("file") or "").strip()
    search_preview = str(ctx.get("search") or "").replace("\n", "\\n").strip()
    target_symbol = str(ctx.get("target_symbol") or "").strip()
    line_count = ctx.get("lines")
    char_count = ctx.get("chars")
    parts: list[str] = []
    if line_count is not None:
        parts.append(f"{line_count} non-empty line(s)")
    if char_count is not None:
        parts.append(f"{char_count} char(s)")
    insufficiency_summary = (
        f"The rejected SEARCH only had {' and '.join(parts)}."
        if parts
        else "The rejected SEARCH was too small to safely ground the edit."
    )
    if relative_path and search_preview:
        msg1 = (
            f"Your SEARCH block '{search_preview}' in {relative_path} did not include enough behavior-bearing context."
        )
    elif search_preview:
        msg1 = f"Your SEARCH block '{search_preview}' did not include enough behavior-bearing context."
    else:
        msg1 = "Your SEARCH block did not include enough behavior-bearing context."
    msg2 = (
        f"Requirement: first run read_range on {relative_path} and copy a bounded multiline SEARCH block that includes the mutated logic, not only the signature."
        if relative_path
        else "Requirement: first run read_range on the target file and copy a bounded multiline SEARCH block that includes the mutated logic, not only the signature."
    )
    msg3 = (
        f"SEARCH must include '{target_symbol}' and at least one behavior-bearing body line copied verbatim from the same read_range output."
        if target_symbol
        else "SEARCH must include the grounded target and at least one behavior-bearing body line copied verbatim from the same read_range output."
    )
    repair_lines.extend(
        [
            "SEMANTIC_VALIDATION_FAILED: EDIT_FILE_SEARCH_INSUFFICIENT_CONTEXT.",
            msg1,
            insufficiency_summary,
            "Do not use a one-line signature-only SEARCH block such as an isolated def or async def line.",
            msg2,
            msg3,
            "If you use search_range, widen it only enough to cover the complete nested block being changed; do not emit search_range 1..1 for a function signature.",
            "Prefer the smallest exact multiline block that still includes the mutated logic and matches uniquely. Do not broaden to a whole-function rewrite.",
            "If you cannot ground a bounded behavior-bearing SEARCH block safely, return failure_analysis with reason_code=INSUFFICIENT_CONTEXT_FOR_MUTATION.",
        ]
    )


@_register("EDIT_FILE_SEARCH_NOT_FROM_READ")
def _handle_search_not_from_read(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    relative_path = str(ctx.get("file") or "").strip()
    target_symbol = str(ctx.get("target_symbol") or "").strip()
    msg1 = (
        f"Requirement: Copy-paste 3-5 consecutive lines VERBATIM from the read_range result for {relative_path}."
        if relative_path
        else "Requirement: Copy-paste 3-5 consecutive lines VERBATIM from the read_range result."
    )
    msg2 = (
        f"Form B must cite read_step_idx=0 and use edit_range_within_read to select the exact changed lines inside the preceding read_range output for '{target_symbol}'."
        if target_symbol
        else "Form B must cite read_step_idx=0 and use edit_range_within_read inside the preceding read_range output."
    )
    msg3 = (
        f"Plan shape is fixed: read_range on {relative_path}, then same-file self-contained edit_file immediately after, then the declared validation command."
        if relative_path
        else "Plan shape is fixed: read_range, then same-file self-contained edit_file immediately after, then the declared validation command."
    )
    repair_lines.extend(
        [
            "SEMANTIC_VALIDATION_FAILED: EDIT_FILE_SEARCH_NOT_FROM_READ.",
            "Your edit_file.search text was not found in the output of your read_range call.",
            msg1,
            msg2,
            msg3,
            "Do not emit edit_file without an exact match from the preceding read_range output.",
            "Do not retype, paraphrase, normalize, or abbreviate observed text, and do not emit SEARCH at all for Form B.",
            "If the symbol is not in read_range output, expand or narrow the read_range once or return failure_analysis. Do not emit Form B without a valid edit_range_within_read.",
        ]
    )


@_register("EDIT_FILE_WITHOUT_READ")
def _handle_without_read(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    relative_path = str(ctx.get("file") or "").strip()
    msg = (
        f"Requirement: You MUST run read_file or read_range on {relative_path} IMMEDIATELY before edit_file."
        if relative_path
        else "Requirement: You MUST run read_file or read_range on the target file IMMEDIATELY before edit_file."
    )
    repair_lines.extend(
        [
            "SEMANTIC_VALIDATION_FAILED: EDIT_FILE_WITHOUT_READ.",
            msg,
            "Then construct Form B using that same-file read plus read_step_idx and edit_range_within_read selecting the exact changed lines.",
            "If you cannot find the symbol or exact target text, return failure_analysis.",
        ]
    )


@_register("MUTATION_REQUIRED_BUT_ABSENT")
def _handle_mutation_absent(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    target_symbol = str(ctx.get("target_symbol") or ctx.get("symbol") or "").strip()
    grounded_symbol_source = str(ctx.get("grounded_symbol_source") or "").strip()
    evidence = ctx.get("evidence")
    rejected_search = ""
    if isinstance(evidence, dict):
        rejected_search = str(evidence.get("search") or "").strip()
    msg1 = (
        f"You have the exact source code for {target_symbol}: ```python\n{grounded_symbol_source}\n```"
        if target_symbol and grounded_symbol_source
        else (
            f"You have the exact source code for {target_symbol} in the grounded planner packet."
            if target_symbol
            else "You have the exact grounded source in the planner packet."
        )
    )
    msg2 = (
        f"The rejected unchanged block was:\n```python\n{rejected_search}\n```"
        if rejected_search
        else "The rejected block did not introduce any semantic delta."
    )
    repair_lines.extend(
        [
            "SEMANTIC_VALIDATION_FAILED: MUTATION_REQUIRED_BUT_ABSENT.",
            "Your last edit_file was rejected because search == replace. That is a no-op.",
            msg1,
            msg2,
            "You have two options and ONLY two options:",
            "1. Find a substring in that source that needs to change. Output a self-contained edit_file with file, search, replace, or file, search_range, replace, where replace is semantically different from search.",
            '2. If you cannot safely determine what to change, output failure_analysis with reason_code="INSUFFICIENT_CONTEXT_FOR_MUTATION".',
            "Do not target the same unchanged block again.",
            "Choose the smallest concrete substring that directly changes observable behavior; do not select a surrounding wrapper block when the behavior-bearing fields inside it remain unchanged.",
            "DO NOT output the same code twice. DO NOT output edit_file with identical search and replace.",
            "If you do that again, the task will terminate.",
        ]
    )


@_register("PLANNER_SKIPPED_ANCHOR_WINDOW_READ")
def _handle_skipped_anchor(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    relative_path = str(ctx.get("file") or "").strip()
    grounded_symbol = str(ctx.get("symbol") or "").strip()
    signature = str(ctx.get("signature") or "").strip()
    anchor_start_line = ctx.get("anchor_start_line")
    anchor_end_line = ctx.get("anchor_end_line")
    msg1 = (
        f"codeintel_anchor already grounded {grounded_symbol} in {relative_path} with a canonical line window."
        if grounded_symbol and relative_path
        else "codeintel_anchor already grounded the target file with a canonical line window."
    )
    msg2 = (
        f"Step 1 must be read_range on {relative_path} with start_line={anchor_start_line} and end_line={anchor_end_line}."
        if relative_path and anchor_start_line and anchor_end_line
        else "Step 1 must be read_range on the canonical anchor window before any target-file read/edit."
    )
    msg3 = (
        f"Preserve the grounded signature: {signature}."
        if signature
        else "Preserve the grounded signature and surrounding control flow."
    )
    shape = (
        f'Exact retry shape: {{"steps":[{{"tool":"read_range","args":{{"relative_path":"{relative_path}","start_line":{anchor_start_line},"end_line":{anchor_end_line}}}}},{{"type":"edit_file","file":"{relative_path}","search":"<EXACT TEXT FROM STEP 1>","replace":"<UPDATED TEXT>"}},{{"tool":"cmd.run","args":{{"command_text":"<DECLARED VALIDATION COMMAND>"}}}}]}}'
        if relative_path and anchor_start_line and anchor_end_line
        else 'Exact retry shape: {"steps":[{"tool":"read_range","args":{"relative_path":"<target file>","start_line":<anchor start>,"end_line":<anchor end>}},{"type":"edit_file","file":"<target file>","search":"<EXACT TEXT FROM STEP 1>","replace":"<UPDATED TEXT>"},{"tool":"cmd.run","args":{"command_text":"<DECLARED VALIDATION COMMAND>"}}]}'
    )
    repair_lines.extend(
        [
            "SEMANTIC_VALIDATION_FAILED: PLANNER_SKIPPED_ANCHOR_WINDOW_READ.",
            msg1,
            msg2,
            "Do not run search first when the authoritative anchor already includes start_line and end_line.",
            "No other actions are allowed before Step 1 completes successfully.",
            msg3,
            "Step 2 may be edit_file after the bounded read_range only when it declares grounding_source=read_range, read_step_idx=0, and edit_range_within_read on that same-file read output with no linkage fields.",
            "Never add or remove a trailing newline in edit_file.search; copy the exact contiguous substring from the Step 1 read_range output.",
            shape,
        ]
    )


@_register("PLANNER_SKIPPED_DISCOVERY")
def _handle_skipped_discovery(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    relative_path = str(ctx.get("file") or "").strip()
    grounded_symbol = str(ctx.get("symbol") or "").strip()
    msg1 = (
        f"codeintel_anchor.symbol='{grounded_symbol}' was grounded, but you attempted a target-file read/edit before running search."
        if grounded_symbol
        else "A grounded codeintel symbol was present, but you attempted a target-file read/edit before running search."
    )
    if relative_path and grounded_symbol:
        msg2 = (
            f"Requirement: Step 1 must be search on {relative_path} for '{grounded_symbol}' or its terminal identifier."
        )
    elif grounded_symbol:
        msg2 = f"Requirement: Step 1 must be search for '{grounded_symbol}' or its terminal identifier."
    else:
        msg2 = "Requirement: Step 1 must be search for the grounded symbol before any target-file read/edit."
    repair_lines.extend(
        [
            "SEMANTIC_VALIDATION_FAILED: PLANNER_SKIPPED_DISCOVERY.",
            msg1,
            msg2,
            "Step 2 must be read_range or read_file on the matching target file, and Step 3 may be edit_file.",
            "edit_file.search must be copied verbatim from the discovered read output, or edit_file.search_range must stay on that same file with no linkage fields.",
            "If search returns zero matches, return failure_analysis instead of edit_file.",
        ]
    )


@_register("EDIT_FILE_SYNTAX_ERROR")
def _handle_syntax_error(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    repair_lines.append(
        "SEMANTIC_VALIDATION_FAILED: EDIT_FILE_SYNTAX_ERROR. "
        f"Your REPLACE block would create invalid Python: {ctx.get('syntax_error')}. "
        "Requirement: Ensure REPLACE produces syntactically valid code. "
        "Check indentation, brackets, colons. If you cannot fix syntax, return failure_analysis."
    )


@_register("EDIT_FILE_NO_EFFECT")
def _handle_no_effect(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    repair_lines.append(
        "SEMANTIC_VALIDATION_FAILED: EDIT_FILE_NO_EFFECT. "
        "Your edit_file.search/edit_file.replace pair produced no change to file content. "
        "Requirement: edit_file.replace must differ from existing code to satisfy mutation lane. "
        "Never emit edit_file when search and replace normalize to the same content. "
        "If no semantic mutation is possible, return failure_analysis with "
        "reason_code=INSUFFICIENT_CONTEXT_FOR_MUTATION and explain the missing context."
    )


@_register("EDIT_FILE_MALFORMED_BLOCK")
def _handle_malformed_block(repair_lines: list[str], ctx: dict[str, Any]) -> None:
    reason = str(ctx.get("reason") or "malformed").strip() or "malformed"
    repair_lines.append(
        "SEMANTIC_VALIDATION_FAILED: EDIT_FILE_MALFORMED_BLOCK. "
        f"Block {ctx.get('block_index')} {reason}. "
        "Requirement: Output typed JSON only. The only valid mutation step is "
        '{"type":"edit_file","file":"<path>","grounding_source":"anchor","search_range":{"start_line":<n>,"end_line":<m>},"replacement_text":"<changed text>"} or '
        '{"type":"edit_file","file":"<path>","grounding_source":"read_range","read_step_idx":0,"edit_range_within_read":{"start_line":<n>,"end_line":<m>},"replacement_text":"<changed text>"}. '
        'If you cannot produce that shape safely, output {"type":"failure_analysis","reason_code":"INSUFFICIENT_CONTEXT_FOR_MUTATION","detail":{...}}. '
        "Do not emit search_replace_blocks, raw SEARCH/REPLACE markers, or legacy tool/args wrappers."
    )


def _append_error_specific_lines(repair_lines: list[str], error: PlannerSemanticError) -> None:
    handler = _ERROR_HANDLERS.get(error.code)
    if handler:
        handler(repair_lines, error.context)


def _append_planner_semantic_repair_prompt(*, prompt: str, error: PlannerSemanticError) -> str:
    repair_lines: list[str] = []
    _add_header(repair_lines, prompt, error)
    _add_codeintel_lines(repair_lines, error.context)
    _append_error_specific_lines(repair_lines, error)
    return "\n".join(repair_lines).strip()
