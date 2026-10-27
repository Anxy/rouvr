"""001K-W01: Versioned objective-cycle event taxonomy / schema registry."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

EVENT_SCHEMA_VERSION = "aix.objective-cycle.events.v1"

EventFamily = Literal[
    "cycle",
    "step",
    "attempt",
    "budget",
    "approval",
    "evidence",
    "discovery",
    "tool",
    "gate",
]

EVENT_TYPES: frozenset[str] = frozenset(
    {
        "cycle.admitted",
        "cycle.started",
        "cycle.waiting_approval",
        "cycle.waiting_blocker",
        "cycle.completed_for_review",
        "cycle.failed",
        "cycle.cancelled",
        "cycle.blocked",
        "step.dispatched",
        "step.verified",
        "step.failed",
        "attempt.started",
        "attempt.succeeded",
        "attempt.failed",
        "budget.reserved",
        "budget.consumed",
        "budget.exhausted",
        "approval.signal_received",
        "approval.invalid",
        "evidence.appended",
        "evidence.review_packet_ready",
        "discovery.manifest_read",
        "discovery.file_read",
        "discovery.search_executed",
        "discovery.started",
        "discovery.completed",
        "tool.file_read",
        "tool.file_written",
        "tool.file_deleted",
        "tool.search",
        "tool.list_directory",
        "tool.patch_applied",
        "tool.command_run",
        "tool.git_status",
        "tool.git_diff",
        "gate.evaluated",
    }
)


@dataclass(frozen=True)
class CycleEventEnvelopeV1:
    schema_version: str
    event_id: str
    event_type: str
    family: EventFamily
    tenant_id: str
    objective_id: str
    cycle_id: str
    occurred_at: str
    correlation_id: str
    causation_id: str
    payload_refs: dict[str, str]
    # Never carry WorkHub lifecycle authority fields.


class EventSchemaError(ValueError):
    pass


def family_for(event_type: str) -> EventFamily:
    prefix = event_type.split(".", 1)[0]
    mapping: dict[str, EventFamily] = {
        "cycle": "cycle",
        "step": "step",
        "attempt": "attempt",
        "budget": "budget",
        "approval": "approval",
        "evidence": "evidence",
        "discovery": "discovery",
        "tool": "tool",
        "gate": "gate",
    }
    if prefix not in mapping:
        raise EventSchemaError(f"UNKNOWN_EVENT_FAMILY:{prefix}")
    return mapping[prefix]


def build_event(
    *,
    event_id: str,
    event_type: str,
    tenant_id: str,
    objective_id: str,
    cycle_id: str,
    correlation_id: str,
    causation_id: str,
    payload_refs: dict[str, str] | None = None,
) -> CycleEventEnvelopeV1:
    if event_type not in EVENT_TYPES:
        raise EventSchemaError(f"UNKNOWN_EVENT_TYPE:{event_type}")
    forbidden = {"lifecycle_state", "workhub_status", "auto_accept", "access_token"}
    refs = dict(payload_refs or {})
    if set(refs) & forbidden:
        raise EventSchemaError("FORBIDDEN_PAYLOAD_KEYS")
    return CycleEventEnvelopeV1(
        schema_version=EVENT_SCHEMA_VERSION,
        event_id=event_id,
        event_type=event_type,
        family=family_for(event_type),
        tenant_id=tenant_id,
        objective_id=objective_id,
        cycle_id=cycle_id,
        occurred_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload_refs=refs,
    )


def envelope_as_dict(event: CycleEventEnvelopeV1) -> dict[str, Any]:
    return {
        "schema_version": event.schema_version,
        "event_id": event.event_id,
        "event_type": event.event_type,
        "family": event.family,
        "tenant_id": event.tenant_id,
        "objective_id": event.objective_id,
        "cycle_id": event.cycle_id,
        "occurred_at": event.occurred_at,
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
        "payload_refs": dict(event.payload_refs),
    }
