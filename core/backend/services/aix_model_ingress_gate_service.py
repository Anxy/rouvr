"""Final AIX model-ingress compiler and guard for compact packet discipline."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODEL_INGRESS_SCHEMA = "aix.model_ingress.current"
MODEL_REQUEST_SCHEMA = "aix.model_request.current"
UTF_TASK_SCHEMA = "aix.utf_task.current"
PLANNER_INGRESS_SCHEMA = "aix.planner_ingress.current"
CLI_EXECUTOR_INGRESS_SCHEMA = "aix.cli_executor_ingress.current"
REPAIR_INGRESS_SCHEMA = "aix.repair_ingress.current"
VERIFIER_INGRESS_SCHEMA = "aix.verifier_ingress.current"
CURRENT_INGRESS_SCHEMA_CATALOG = (
    UTF_TASK_SCHEMA,
    PLANNER_INGRESS_SCHEMA,
    CLI_EXECUTOR_INGRESS_SCHEMA,
    REPAIR_INGRESS_SCHEMA,
    VERIFIER_INGRESS_SCHEMA,
    MODEL_REQUEST_SCHEMA,
)
MODEL_INGRESS_TARGET_PROMPT_TOKENS = 1200
MODEL_INGRESS_MAX_PROMPT_TOKENS = 2000
CURRENT_SCHEMA_VERSION = "current"
LEGACY_DOT_SCHEMA_RE = re.compile(r"(^|[.])v[12]($|[.@:/_-])", re.IGNORECASE)
LEGACY_SUFFIX_SCHEMA_RE = re.compile(r"(^|[A-Za-z0-9])_v[12]$", re.IGNORECASE)
LEGACY_CAMEL_SCHEMA_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*V[12]\b")
PATHLIKE_SUFFIXES = (
    ".py",
    ".pyi",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".md",
    ".txt",
    ".toml",
    ".patch",
    ".diff",
)
FORBIDDEN_ACTIVE_KEYS = {
    "history",
    "writeback",
    "verification_blob",
    "full_metadata",
    "provider_messages",
    "quality_gate",
    "attestation",
}
INGRESS_SCHEMAS = {
    "planner": PLANNER_INGRESS_SCHEMA,
    "cli_executor": CLI_EXECUTOR_INGRESS_SCHEMA,
    "repair": REPAIR_INGRESS_SCHEMA,
    "verifier": VERIFIER_INGRESS_SCHEMA,
    "utf_task": UTF_TASK_SCHEMA,
}


def _resolve_schema_dir(*, start_file: str | Path | None = None, extra_roots: tuple[Path, ...] = ()) -> Path:
    start = Path(start_file or __file__).resolve()
    for root in (start, *start.parents):
        candidate = root / "schemas" / "aix" / "model_ingress"
        if candidate.is_dir():
            return candidate
    for root in (*extra_roots, Path("/app"), Path.cwd()):
        candidate = root / "schemas" / "aix" / "model_ingress"
        if candidate.is_dir():
            return candidate
    return start.parent / "schemas" / "aix" / "model_ingress"


SCHEMA_DIR = _resolve_schema_dir()


class ModelIngressGateError(RuntimeError):
    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = dict(diagnostics or {})


@dataclass(frozen=True)
class CompiledModelIngress:
    request_id: str
    schema_name: str
    schema_version: str
    fingerprint: str
    body: dict[str, Any]
    artifact: dict[str, Any]


def estimate_prompt_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    byte_count = len(str(text or "").encode("utf-8"))
    return max(1, byte_count // 4) if byte_count else 0


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def prompt_token_breakdown(messages: list[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    total = 0
    for index, message in enumerate(messages):
        role = str(message.get("role") or "unknown")
        content = message.get("content")
        tokens = estimate_prompt_tokens(content)
        total += tokens
        items.append(
            {
                "index": index,
                "role": role,
                "estimated_tokens": tokens,
                "content_bytes": len(str(content or "").encode("utf-8")),
            }
        )
    return {
        "schema": "aix.prompt_token_breakdown.current",
        "estimated_prompt_tokens": total,
        "target_prompt_tokens": MODEL_INGRESS_TARGET_PROMPT_TOKENS,
        "max_prompt_tokens": MODEL_INGRESS_MAX_PROMPT_TOKENS,
        "messages": items,
    }


def _as_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return []
    return [dict(item) for item in messages if isinstance(item, dict)]


def _walk(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for item in value.values():
            values.extend(_walk(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(_walk(item))
    return values


def _walk_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            keys.append(str(key))
            keys.extend(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_walk_keys(item))
    return keys


def _looks_like_path_or_symbol(value: str) -> bool:
    normalized = value.strip()
    lowered = normalized.casefold()
    if "/" in normalized or "\\" in normalized:
        return True
    if lowered.endswith(PATHLIKE_SUFFIXES):
        return True
    # Python import paths can legitimately contain modules with historical names,
    # e.g. backend.services.aix_v2_live_file_grounding_service.
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$", normalized))


def _is_legacy_schema_marker(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    lowered = candidate.casefold()
    if any(marker in lowered for marker in (".v1", ".v2")):
        return bool(LEGACY_DOT_SCHEMA_RE.search(candidate))
    if _looks_like_path_or_symbol(candidate):
        return False
    if LEGACY_SUFFIX_SCHEMA_RE.search(candidate):
        return True
    return bool(LEGACY_CAMEL_SCHEMA_RE.search(candidate))


def _prune_null_empty(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {
            str(key): _prune_null_empty(item)
            for key, item in value.items()
            if item is not None and item != {} and item != []
        }
        return {key: item for key, item in cleaned.items() if item is not None and item != {} and item != []}
    if isinstance(value, list):
        return [
            item
            for item in (_prune_null_empty(item) for item in value)
            if item is not None and item != {} and item != []
        ]
    return value


def _assert_no_legacy_or_bloat(value: Any, *, reject_null_empty: bool = True) -> None:
    legacy_hits: list[str] = []
    for item in _walk(value):
        if isinstance(item, str) and _is_legacy_schema_marker(item):
            legacy_hits.append(item[:120])
    forbidden_keys = sorted({key for key in _walk_keys(value) if key in FORBIDDEN_ACTIVE_KEYS})
    null_or_empty = reject_null_empty and any(item is None or item == {} or item == [] for item in _walk(value))
    violations: list[str] = []
    if legacy_hits:
        violations.append("LEGACY_SCHEMA_MARKER")
    if forbidden_keys:
        violations.append("FORBIDDEN_ACTIVE_KEY")
    if null_or_empty:
        violations.append("NULL_OR_EMPTY_BLOAT")
    if violations:
        raise ModelIngressGateError(
            "MODEL_INGRESS_SCHEMA_REJECTED violations=" + ",".join(violations),
            diagnostics={
                "violations": violations,
                "legacy_hits": legacy_hits[:5],
                "forbidden_keys": forbidden_keys,
            },
        )


def _load_schema(schema_name: str) -> dict[str, Any]:
    path = SCHEMA_DIR / f"{schema_name}.schema.json"
    if not path.exists():
        raise ModelIngressGateError(
            "MODEL_INGRESS_SCHEMA_MISSING", diagnostics={"schema": schema_name, "path": str(path)}
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_required(payload: dict[str, Any], schema: dict[str, Any]) -> None:
    missing = [key for key in list(schema.get("required") or []) if key not in payload]
    if missing:
        raise ModelIngressGateError("MODEL_INGRESS_SCHEMA_REQUIRED_MISSING", diagnostics={"missing": missing})
    properties = dict(schema.get("properties") or {})
    for key, property_schema in properties.items():
        if key not in payload:
            continue
        expected_const = property_schema.get("const")
        if expected_const is not None and payload.get(key) != expected_const:
            raise ModelIngressGateError(
                "MODEL_INGRESS_SCHEMA_CONST_MISMATCH",
                diagnostics={"field": key, "expected": expected_const, "actual": payload.get(key)},
            )
        enum_values = property_schema.get("enum")
        if enum_values and payload.get(key) not in enum_values:
            raise ModelIngressGateError(
                "MODEL_INGRESS_SCHEMA_ENUM_MISMATCH",
                diagnostics={"field": key, "allowed": enum_values, "actual": payload.get(key)},
            )
    if schema.get("additionalProperties") is False:
        extra = sorted(set(payload) - set(properties))
        if extra:
            raise ModelIngressGateError("MODEL_INGRESS_SCHEMA_EXTRA_FIELDS", diagnostics={"extra": extra})


def validate_current_ingress_payload(payload: dict[str, Any], *, schema_name: str | None = None) -> dict[str, Any]:
    candidate = dict(payload or {})
    name = str(schema_name or candidate.get("schema_name") or candidate.get("schema") or "").strip()
    if not name:
        raise ModelIngressGateError("MODEL_INGRESS_SCHEMA_NAME_REQUIRED")
    if _is_legacy_schema_marker(name):
        raise ModelIngressGateError("MODEL_INGRESS_LEGACY_SCHEMA_REJECTED", diagnostics={"schema": name})
    schema = _load_schema(name)
    _validate_required(candidate, schema)
    _assert_no_legacy_or_bloat(candidate)
    return candidate


def compile_model_ingress_request(
    *,
    kind: str,
    task_id: str,
    task_type: str,
    params: dict[str, Any],
    actions: list[str],
    context: dict[str, Any] | None = None,
    provider: str = "openrouter",
    model: str | None = None,
    system_instruction: str = "Return compact JSON only.",
    max_tokens: int = 4096,
    temperature: float | None = 0,
    allow_tool_calls: bool = False,
    metadata: dict[str, Any] | None = None,
) -> CompiledModelIngress:
    ingress_schema = INGRESS_SCHEMAS.get(kind)
    if not ingress_schema:
        raise ModelIngressGateError("MODEL_INGRESS_KIND_UNSUPPORTED", diagnostics={"kind": kind})
    utf_task = validate_current_ingress_payload(
        {
            "schema_name": UTF_TASK_SCHEMA,
            "schema_version": CURRENT_SCHEMA_VERSION,
            "task_id": str(task_id),
            "task_type": str(task_type),
            "params": dict(params or {}),
            "actions": list(actions or []),
        },
        schema_name=UTF_TASK_SCHEMA,
    )
    ingress_payload = validate_current_ingress_payload(
        {
            "schema_name": ingress_schema,
            "schema_version": CURRENT_SCHEMA_VERSION,
            "utf_task": utf_task,
            "context": dict(context or {"scope": "minimal"}),
        },
        schema_name=ingress_schema,
    )
    user_content = _stable_json(ingress_payload)
    chat_turns = (
        {"role": "system", "content": str(system_instruction).strip() or "Return compact JSON only."},
        {"role": "user", "content": user_content},
    )
    request_id = hashlib.sha256(f"{kind}:{task_id}:{_stable_json(ingress_payload)}".encode()).hexdigest()[:32]
    request_metadata = {
        "schema_name": MODEL_REQUEST_SCHEMA,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "request_id": request_id,
        "model_ingress_compiler": MODEL_INGRESS_SCHEMA,
        "ingress_schema": ingress_schema,
        "planner_request_shape": UTF_TASK_SCHEMA if kind == "planner" else ingress_schema,
        **dict(metadata or {}),
    }
    body: dict[str, Any] = {
        "provider": provider,
        "messages": list(chat_turns),
        "max_tokens": max_tokens,
        "metadata": request_metadata,
    }
    if model:
        body["model"] = model
    if temperature is not None:
        body["temperature"] = temperature
    guarded = enforce_model_ingress_gate(body, source=f"aix.{kind}", allow_tool_calls=allow_tool_calls)
    fingerprint = _fingerprint(
        {
            "schema_name": MODEL_REQUEST_SCHEMA,
            "schema_version": CURRENT_SCHEMA_VERSION,
            "messages": guarded["messages"],
            "metadata": guarded["metadata"],
        }
    )
    guarded["metadata"]["fingerprint"] = fingerprint
    artifact = validate_current_ingress_payload(
        {
            "schema_name": MODEL_REQUEST_SCHEMA,
            "schema_version": CURRENT_SCHEMA_VERSION,
            "request_id": request_id,
            "fingerprint": fingerprint,
            "provider": provider,
            "model": str(model or "default"),
            "ingress": ingress_payload,
            "prompt_token_breakdown": guarded["metadata"]["prompt_token_breakdown"],
        },
        schema_name=MODEL_REQUEST_SCHEMA,
    )
    return CompiledModelIngress(
        request_id=request_id,
        schema_name=MODEL_REQUEST_SCHEMA,
        schema_version=CURRENT_SCHEMA_VERSION,
        fingerprint=fingerprint,
        body=guarded,
        artifact=artifact,
    )


def _compact_schema_present(body: dict[str, Any], metadata: dict[str, Any]) -> bool:
    shape = str(metadata.get("planner_request_shape") or "").strip()
    if shape in set(INGRESS_SCHEMAS.values()) | {UTF_TASK_SCHEMA, MODEL_REQUEST_SCHEMA}:
        return True
    if str(metadata.get("planner_request_shape") or "") == "generic_prompt":
        return True
    for message in _as_messages(body):
        content = str(message.get("content") or "")
        if (
            UTF_TASK_SCHEMA in content
            or MODEL_REQUEST_SCHEMA in content
            or any(schema in content for schema in INGRESS_SCHEMAS.values())
        ):
            return True
    return False


def enforce_model_ingress_gate(
    body: dict[str, Any],
    *,
    source: str,
    allow_tool_calls: bool = False,
) -> dict[str, Any]:
    guarded = dict(body or {})
    metadata = dict(guarded.get("metadata") or {})
    messages = _as_messages(guarded)
    if not messages:
        raise ModelIngressGateError("MODEL_INGRESS_MESSAGES_REQUIRED")
    breakdown = prompt_token_breakdown(messages)
    estimated_tokens = int(breakdown["estimated_prompt_tokens"])
    has_tools = bool(guarded.get("tools") or guarded.get("tool_choice") or guarded.get("parallel_tool_calls"))
    compact_schema_present = _compact_schema_present(guarded, metadata)
    gate = {
        "schema": MODEL_INGRESS_SCHEMA,
        "source": source,
        "compact_schema_present": compact_schema_present,
        "estimated_prompt_tokens": estimated_tokens,
        "target_prompt_tokens": MODEL_INGRESS_TARGET_PROMPT_TOKENS,
        "max_prompt_tokens": MODEL_INGRESS_MAX_PROMPT_TOKENS,
        "tool_calls_allowed": bool(allow_tool_calls),
        "routing_on_breach": "reroute_to_slavx_or_reject",
    }
    metadata["prompt_token_breakdown"] = breakdown
    metadata["model_ingress_gate"] = gate
    guarded["metadata"] = metadata
    violations: list[str] = []
    if estimated_tokens > MODEL_INGRESS_MAX_PROMPT_TOKENS:
        violations.append("PROMPT_TOKEN_CAP_EXCEEDED")
    if has_tools and not allow_tool_calls:
        violations.append("TOOL_CALLS_NOT_ALLOWED_BY_COMPACT_SCHEMA")
    if not compact_schema_present and source.startswith("aix."):
        violations.append("COMPACT_UTF_SCHEMA_MISSING")
    if violations:
        raise ModelIngressGateError(
            "MODEL_INGRESS_GATE_REJECTED violations=" + ",".join(violations),
            diagnostics={
                "violations": violations,
                "reroute_provider": "slavx",
                "prompt_token_breakdown": breakdown,
                "model_ingress_gate": gate,
            },
        )
    _assert_no_legacy_or_bloat({"messages": messages, "metadata": metadata}, reject_null_empty=False)
    return guarded


__all__ = [
    "CLI_EXECUTOR_INGRESS_SCHEMA",
    "CompiledModelIngress",
    "CURRENT_INGRESS_SCHEMA_CATALOG",
    "CURRENT_SCHEMA_VERSION",
    "PLANNER_INGRESS_SCHEMA",
    "REPAIR_INGRESS_SCHEMA",
    "UTF_TASK_SCHEMA",
    "VERIFIER_INGRESS_SCHEMA",
    "MODEL_INGRESS_MAX_PROMPT_TOKENS",
    "MODEL_INGRESS_SCHEMA",
    "MODEL_INGRESS_TARGET_PROMPT_TOKENS",
    "MODEL_REQUEST_SCHEMA",
    "ModelIngressGateError",
    "compile_model_ingress_request",
    "enforce_model_ingress_gate",
    "estimate_prompt_tokens",
    "prompt_token_breakdown",
    "validate_current_ingress_payload",
]
