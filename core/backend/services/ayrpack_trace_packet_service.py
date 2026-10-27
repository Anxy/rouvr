from __future__ import annotations

from typing import Any

TRACE_SCHEMA_NAME = "aix_trace_packet_current"
TRACE_SCHEMA_VERSION = "v1"


def build_planner_trace_packet_payload(
    *,
    packet_id: str,
    project_id: str,
    correlation_id: str,
    workspace_root: str,
    provider: str,
    model: str | None,
    prompt: str,
    attempts_used: int,
    raw_output_text: str,
    parsed_output: Any,
    normalized_steps: list[dict[str, Any]],
    transport_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "packet_id": packet_id,
        "project_id": project_id,
        "conversation_id": correlation_id,
        "trace_id": correlation_id,
        "lane": "aix_planner_trace",
        "schema_name": TRACE_SCHEMA_NAME,
        "schema_version": TRACE_SCHEMA_VERSION,
        "payload_json": {
            "trace_kind": "planner_trace",
            "context": {
                "project_id": project_id,
                "correlation_id": correlation_id,
                "workspace_root": workspace_root,
            },
            "planner": {
                "provider": provider,
                "model": model,
                "prompt": prompt,
                "attempts_used": attempts_used,
                "raw_output_text": raw_output_text,
                "parsed_output": parsed_output,
                "normalized_steps": normalized_steps,
                "transport_diagnostics": dict(transport_diagnostics or {}),
            },
        },
        "metadata_json": {
            "source": "aix.devexecutor_planner_bridge",
            "shadow_write": True,
        },
        "status": "pending",
    }


def build_model_gateway_trace_packet_payload(
    *,
    packet_id: str,
    project_id: str,
    conversation_id: str,
    correlation_id: str,
    actor_id: str,
    request_payload: dict[str, Any],
    decision_payload: dict[str, Any],
    usage_payload: dict[str, Any],
    redaction_payload: dict[str, Any],
    routing_payload: dict[str, Any],
    decision_record_id: str | None,
) -> dict[str, Any]:
    return {
        "packet_id": packet_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "trace_id": correlation_id,
        "lane": f"model_gateway_{request_payload.get('phase') or 'executor'}",
        "schema_name": TRACE_SCHEMA_NAME,
        "schema_version": TRACE_SCHEMA_VERSION,
        "payload_json": {
            "trace_kind": "model_gateway_decision",
            "context": {
                "project_id": project_id,
                "correlation_id": correlation_id,
                "conversation_id": conversation_id,
                "decision_record_id": decision_record_id,
            },
            "request": request_payload,
            "decision": decision_payload,
            "usage": usage_payload,
            "redaction": redaction_payload,
            "routing": routing_payload,
        },
        "metadata_json": {
            "source": "aix.model_gateway.resolve",
            "shadow_write": True,
            "actor_id": actor_id,
        },
        "created_by": actor_id,
        "updated_by": actor_id,
        "status": "pending",
    }


def build_outcome_policy_trace_packet_payload(
    *,
    packet_id: str,
    project_id: str,
    conversation_id: str,
    correlation_id: str,
    actor_id: str,
    workspace_root: str,
    task_number: int,
    task_intent: str,
    final_status: str,
    raw_signal: dict[str, Any],
    normalized_signal: dict[str, Any],
    classified_policy: dict[str, Any],
    decision_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "packet_id": packet_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "trace_id": correlation_id,
        "lane": "aix_outcome_policy_trace",
        "schema_name": TRACE_SCHEMA_NAME,
        "schema_version": TRACE_SCHEMA_VERSION,
        "payload_json": {
            "trace_kind": "outcome_policy_trace",
            "context": {
                "project_id": project_id,
                "correlation_id": correlation_id,
                "conversation_id": conversation_id,
                "workspace_root": workspace_root,
                "task_number": task_number,
                "task_intent": task_intent,
                "final_status": final_status,
            },
            "raw_signal": raw_signal,
            "normalized_signal": normalized_signal,
            "classified_policy": classified_policy,
            "decision": decision_payload,
        },
        "metadata_json": {
            "source": "aix.v2_runtime",
            "shadow_write": True,
            "actor_id": actor_id,
        },
        "created_by": actor_id,
        "updated_by": actor_id,
        "status": "pending",
    }


__all__ = [
    "TRACE_SCHEMA_NAME",
    "TRACE_SCHEMA_VERSION",
    "build_model_gateway_trace_packet_payload",
    "build_outcome_policy_trace_packet_payload",
    "build_planner_trace_packet_payload",
]
