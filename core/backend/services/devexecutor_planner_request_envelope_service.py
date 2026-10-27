from __future__ import annotations

import json
from typing import Any

from backend.models.planner_request_envelope_models import PlannerRequestEnvelopeCurrent
from backend.models.planner_slice_packet_models import (
    PLANNER_SLICE_PACKET_CURRENT_MAX_BYTES,
    PlannerSlicePacketCurrent,
)


def _json_size_bytes(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


def _is_strict_packet_bound_replace_function_lane(planner_packet: dict[str, Any]) -> bool:
    packet = dict(planner_packet or {})
    return (
        str(packet.get("schema_name") or "").strip() == "planner_slice.current"
        and str(packet.get("planner_mode") or "").strip() == "packet_bound_replace_function"
    )


def _build_generic_current_request_envelope(
    *,
    planner_packet: dict[str, Any],
    task_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = json.loads(json.dumps(planner_packet, ensure_ascii=True))
    grounded_symbol_source = str(raw.get("grounded_symbol_source") or "").strip()
    if grounded_symbol_source:
        raise ValueError("planner packet contains forbidden fields: grounded_symbol_source")

    codeintel_anchor = dict(raw.get("codeintel_anchor") or {}) if isinstance(raw.get("codeintel_anchor"), dict) else {}
    provenance = dict(raw.get("provenance") or {}) if isinstance(raw.get("provenance"), dict) else {}
    lineage = dict(raw.get("lineage") or {}) if isinstance(raw.get("lineage"), dict) else {}
    tool_policy = dict(raw.get("tool_policy") or {}) if isinstance(raw.get("tool_policy"), dict) else {}

    planner_packet_payload = dict(raw)
    planner_packet_payload.pop("implementation_hints", None)
    planner_packet_payload.pop("grounded_symbol_source", None)
    planner_packet_payload.pop("provenance", None)
    planner_packet_payload.pop("lineage", None)

    task_identity = {
        "task_number": int(raw.get("task_number") or 0),
        "slice_id": str(raw.get("slice_id") or "").strip(),
        "parent_task_number": int(lineage.get("parent_task_number") or raw.get("task_number") or 0) or None,
    }
    references: dict[str, Any] = {}
    ticket_contract_ref = str(provenance.get("ticket_contract_ref") or "").strip()
    if ticket_contract_ref:
        references["ticket_contract_ref"] = ticket_contract_ref
    snapshot_id = str(codeintel_anchor.get("snapshot_id") or "").strip()
    if snapshot_id:
        references["anchor_snapshot_ref"] = f"codeintel://snapshot/{snapshot_id}"

    execution_contract: dict[str, Any] = {
        "packet_max_bytes": PLANNER_SLICE_PACKET_CURRENT_MAX_BYTES,
        "packet_mode": "generic",
    }
    if tool_policy:
        execution_contract["tool_policy"] = tool_policy

    envelope_dict: dict[str, Any] = {
        "schema_name": "aix.utf_task.current",
        "schema_version": "current",
        "task_identity": task_identity,
        "planner_packet": planner_packet_payload,
        "execution_contract": execution_contract,
    }
    if references:
        envelope_dict["references"] = references

    component_bytes = {
        "schema": _json_size_bytes(
            {
                "schema_name": envelope_dict["schema_name"],
                "schema_version": envelope_dict["schema_version"],
            }
        ),
        "task_identity": _json_size_bytes(envelope_dict["task_identity"]),
        "planner_packet": _json_size_bytes(envelope_dict["planner_packet"]),
        "execution_contract": _json_size_bytes(envelope_dict["execution_contract"]),
        "references": _json_size_bytes(envelope_dict.get("references", {})),
    }
    allowed_surface = dict(envelope_dict["planner_packet"].get("allowed_surface") or {})
    planner_packet_component_bytes = {
        "core_contract": _json_size_bytes(
            {key: value for key, value in envelope_dict["planner_packet"].items() if key != "allowed_surface"}
        ),
        "allowed_surface.anchor": _json_size_bytes(allowed_surface),
    }
    accounting = {
        "planner_request_shape": "aix.utf_task.current",
        "total_bytes": _json_size_bytes(envelope_dict),
        "component_bytes": component_bytes,
        "planner_packet_component_bytes": planner_packet_component_bytes,
        "top_contributors": sorted(
            [
                {"component": "planner_packet", "bytes": component_bytes["planner_packet"]},
                {"component": "execution_contract", "bytes": component_bytes["execution_contract"]},
                {"component": "task_identity", "bytes": component_bytes["task_identity"]},
                {"component": "references", "bytes": component_bytes["references"]},
                {
                    "component": "planner_packet.allowed_surface.anchor",
                    "bytes": planner_packet_component_bytes["allowed_surface.anchor"],
                },
                {"component": "planner_packet.core_contract", "bytes": planner_packet_component_bytes["core_contract"]},
            ],
            key=lambda item: item["bytes"] if isinstance(item["bytes"], int) else 0,
            reverse=True,
        ),
        "planner_packet_audit": {
            "total_bytes": _json_size_bytes(planner_packet_payload),
            "field_bytes": {
                str(key): _json_size_bytes(value) for key, value in planner_packet_payload.items() if str(key).strip()
            },
            "forbidden_fields": [],
            "duplicate_fields": [],
        },
    }
    return envelope_dict, accounting


def build_planner_request_envelope_with_accounting(
    *,
    prompt: str,
    planner_packet: dict[str, Any],
    task_intent: str,
    max_steps: int,
    planner_attempt_number: int,
    project_id: str | None = None,
    task_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    task_id = task_id or str(
        (planner_packet or {}).get("task_id") or (planner_packet or {}).get("task_number") or "unknown"
    )
    _ = project_id or str((planner_packet or {}).get("project_id") or "").strip() or None
    _ = prompt, task_intent, max_steps, planner_attempt_number
    print(f"DEBUG_ENVELOPE_BUILD task={task_id or 'unknown'} intent={task_intent}")
    if not _is_strict_packet_bound_replace_function_lane(planner_packet):
        return _build_generic_current_request_envelope(planner_packet=planner_packet, task_id=task_id)

    packet = PlannerSlicePacketCurrent.model_validate(planner_packet)
    task_identity = {
        "task_number": packet.task_number,
        "slice_id": packet.slice_id,
        "parent_task_number": packet.task_number,
    }
    planner_packet_core = packet.model_dump(mode="json", exclude_none=True)
    execution_contract = {
        "packet_max_bytes": PLANNER_SLICE_PACKET_CURRENT_MAX_BYTES,
        "packet_mode": "strict_current_only",
    }
    envelope = PlannerRequestEnvelopeCurrent(
        task_identity=task_identity,
        planner_packet=planner_packet_core,
        execution_contract=execution_contract,
    )
    envelope_dict = envelope.model_dump(mode="json", exclude_none=True)
    component_bytes = {
        "schema": _json_size_bytes(
            {
                "schema_name": envelope_dict["schema_name"],
                "schema_version": envelope_dict["schema_version"],
            }
        ),
        "task_identity": _json_size_bytes(envelope_dict["task_identity"]),
        "planner_packet": _json_size_bytes(envelope_dict["planner_packet"]),
        "execution_contract": _json_size_bytes(envelope_dict["execution_contract"]),
        "references": _json_size_bytes(envelope_dict.get("references", {})),
    }
    planner_packet_component_bytes = {
        "core_contract": _json_size_bytes(
            {key: value for key, value in envelope_dict["planner_packet"].items() if key != "allowed_surface"}
        ),
        "allowed_surface.anchor": _json_size_bytes(envelope_dict["planner_packet"]["allowed_surface"]),
    }
    total_bytes = _json_size_bytes(envelope_dict)
    top_contributors: list[dict[str, str | int]] = [
        {"component": "planner_packet", "bytes": component_bytes["planner_packet"]},
        {"component": "execution_contract", "bytes": component_bytes["execution_contract"]},
        {"component": "task_identity", "bytes": component_bytes["task_identity"]},
        {"component": "references", "bytes": component_bytes["references"]},
        {
            "component": "planner_packet.allowed_surface.anchor",
            "bytes": planner_packet_component_bytes["allowed_surface.anchor"],
        },
        {"component": "planner_packet.core_contract", "bytes": planner_packet_component_bytes["core_contract"]},
    ]
    top_contributors.sort(key=lambda item: item["bytes"] if isinstance(item["bytes"], int) else 0, reverse=True)
    accounting = {
        "planner_request_shape": "aix.utf_task.current",
        "total_bytes": total_bytes,
        "component_bytes": component_bytes,
        "planner_packet_component_bytes": planner_packet_component_bytes,
        "top_contributors": top_contributors,
        "planner_packet_audit": packet.build_audit(),
    }
    return envelope_dict, accounting


def build_planner_request_envelope(
    *,
    prompt: str,
    planner_packet: dict[str, Any],
    task_intent: str,
    max_steps: int,
    planner_attempt_number: int,
    project_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    envelope_dict, _accounting = build_planner_request_envelope_with_accounting(
        prompt=prompt,
        planner_packet=planner_packet,
        task_intent=task_intent,
        max_steps=max_steps,
        planner_attempt_number=planner_attempt_number,
        project_id=project_id,
        task_id=task_id,
    )
    return envelope_dict


def serialize_planner_request_envelope(*, envelope: dict[str, Any]) -> str:
    return json.dumps(envelope, ensure_ascii=True, separators=(",", ":"))
