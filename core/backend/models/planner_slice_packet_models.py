from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PLANNER_SLICE_PACKET_SCHEMA_NAME = "planner_slice.current"
PLANNER_SLICE_PACKET_SCHEMA_VERSION = "current"

PlannerMutationKind = Literal[
    "add_import",
    "symbol_body_edit",
    "bounded_method_protocol_extension",
    "signature_preserving_edit",
    "file_local_refactor",
    "test_addition",
    "test_fix",
]

PlannerInterfaceChangePolicy = Literal[
    "preserve",
    "additive_optional_params",
    "signature_change_allowed",
]

PlannerSliceInvariantKind = Literal[
    "signature_shape",
    "import_contract",
    "proof_surface",
    "return_contract",
    "call_contract",
    "call_signature_contract",
]


PLANNER_SLICE_PACKET_CURRENT_SCHEMA_NAME = "planner_slice.current"
PLANNER_SLICE_PACKET_CURRENT_SCHEMA_VERSION = "current"
PLANNER_SLICE_PACKET_CURRENT_MAX_BYTES = 8 * 1024
PLANNER_SLICE_PACKET_CURRENT_FORBIDDEN_FIELDS = {
    "implementation_hints",
    "mutation_instructions",
    "acceptance_criteria",
    "constraints",
    "grounded_symbol_source",
    "behavior_change",
    "mutation_kind",
    "interface_change_policy",
    "target_files",
    "bounded_edit_surface",
    "tool_policy",
    "stop_conditions",
    "provenance",
    "lineage",
    "capability_contract",
    "grounding_hints",
    "hard_invariants",
    "prior_evidence_refs",
    "capability_semantics",
    "execution_slice_contract",
    "canonical_task_contract",
    "operator_digest",
    "hotspots",
}


def _jsonable(payload: Any) -> Any:
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json", exclude_none=True)
    if isinstance(payload, dict):
        return {str(key): _jsonable(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_jsonable(item) for item in payload]
    return payload


def _json_size_bytes(payload: Any) -> int:
    payload = _jsonable(payload)
    return len(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


def _normalize_duplicate_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _canonical_target_path(value: Any) -> str:
    raw = str(value or "").strip()
    if "\\" in raw or raw.startswith("/") or raw.startswith("./"):
        raise ValueError("target_file must be a canonical relative path")
    text = raw.strip("/")
    parts = [part for part in text.split("/") if part]
    if not parts:
        raise ValueError("target_file is required")
    if any(part in {".", ".."} for part in parts):
        raise ValueError("target_file must be a canonical relative path")
    canonical = "/".join(parts)
    if canonical != text:
        raise ValueError("target_file must be a canonical relative path")
    return canonical


def audit_planner_slice_packet_current_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw = _jsonable(dict(payload or {}))
    allowed_surface = raw.get("allowed_surface")
    if isinstance(allowed_surface, BaseModel):
        allowed_surface = allowed_surface.model_dump(mode="json", exclude_none=True)
    allowed_surface_dict = allowed_surface if isinstance(allowed_surface, dict) else {}
    forbidden_fields = sorted(field for field in PLANNER_SLICE_PACKET_CURRENT_FORBIDDEN_FIELDS if field in raw)
    field_bytes = {str(key): _json_size_bytes(value) for key, value in raw.items() if str(key).strip()}
    duplicate_fields: list[dict[str, Any]] = []
    seen_values: dict[str, list[str]] = {}
    duplicate_candidates = {
        "objective": raw.get("objective"),
        "target_file": raw.get("target_file"),
        "target_symbol": raw.get("target_symbol"),
        "bounded_mutation_goal": raw.get("bounded_mutation_goal"),
        "validation_command": raw.get("validation_command"),
        "allowed_surface.anchor.compact_snippet": (allowed_surface_dict.get("anchor") or {}).get("compact_snippet"),
    }
    for field_name, value in duplicate_candidates.items():
        normalized = _normalize_duplicate_text(value)
        if not normalized:
            continue
        seen_values.setdefault(normalized, []).append(field_name)
    for normalized, fields in sorted(seen_values.items()):
        if len(fields) > 1:
            duplicate_fields.append({"value": normalized, "fields": fields})
    return {
        "total_bytes": _json_size_bytes(raw),
        "field_bytes": field_bytes,
        "forbidden_fields": forbidden_fields,
        "duplicate_fields": duplicate_fields,
    }


class PlannerAllowedSurfaceAnchorCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str | None = None
    compact_snippet: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _validate_compact_snippet(self) -> PlannerAllowedSurfaceAnchorCurrent:
        snippet = str(self.compact_snippet or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if not snippet.strip():
            raise ValueError("allowed_surface.anchor.compact_snippet is required")
        if len(snippet.encode("utf-8")) > 800:
            raise ValueError("allowed_surface.anchor.compact_snippet exceeds 800 bytes")
        if len(snippet.splitlines()) > 10:
            raise ValueError("allowed_surface.anchor.compact_snippet exceeds 10 lines")
        self.compact_snippet = snippet
        return self


class PlannerAllowedSurfaceCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor: PlannerAllowedSurfaceAnchorCurrent


class PlannerOutputSchemaCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["edit", "replace_function"] = "edit"
    fields: list[str] = Field(..., min_length=1, max_length=8)


PlannerModeCurrent = Literal["packet_bound_replace_function", "generic"]


class PlannerSlicePacketCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_name: Literal["planner_slice.current"] = "planner_slice.current"
    schema_version: Literal["current"] = "current"
    task_number: int = Field(..., ge=1)
    slice_id: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=1, max_length=240)
    target_file: str = Field(..., min_length=1)
    target_symbol: str | None = None
    bounded_mutation_goal: str = Field(..., min_length=1, max_length=240)
    allowed_surface: PlannerAllowedSurfaceCurrent
    validation_command: str = Field(..., min_length=1)
    planner_mode: PlannerModeCurrent = "generic"
    output_schema: PlannerOutputSchemaCurrent
    hard_failure_rules: list[str] = Field(..., min_length=1, max_length=4)

    @field_validator("target_file")
    @classmethod
    def _validate_target_file(cls, value: Any) -> str:
        return _canonical_target_path(value)

    @model_validator(mode="before")
    @classmethod
    def _audit_raw_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        audit = audit_planner_slice_packet_current_payload(value)
        if audit["forbidden_fields"]:
            raise ValueError(
                "planner packet contains forbidden fields: "
                + ", ".join(str(item) for item in audit["forbidden_fields"])
            )
        if audit["duplicate_fields"]:
            raise ValueError(
                "planner packet contains duplicate field values: "
                + json.dumps(audit["duplicate_fields"], ensure_ascii=True, sort_keys=True)
            )
        return value

    @model_validator(mode="after")
    def _validate_serialized_size(self) -> PlannerSlicePacketCurrent:
        total_bytes = self.serialized_bytes()
        if total_bytes > PLANNER_SLICE_PACKET_CURRENT_MAX_BYTES:
            raise ValueError(
                f"planner packet exceeds {PLANNER_SLICE_PACKET_CURRENT_MAX_BYTES} bytes (got {total_bytes})"
            )
        return self

    def serialized_bytes(self) -> int:
        return _json_size_bytes(self.model_dump(mode="json", exclude_none=True))

    def build_audit(self) -> dict[str, Any]:
        return audit_planner_slice_packet_current_payload(self.model_dump(mode="json", exclude_none=True))
