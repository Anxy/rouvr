"""Event shape for the maintained AIX event consumer.

Accepts WorkHub/OpsBus event dicts (fail-closed parsing) and merges task
detail obtained via projection/WorkHub readback for fields the raw event
does not carry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class AIXEvent:
    event_id: str
    event_type: str
    tenant_id: str
    task_id: str | None = None
    task_number: int | None = None
    objective_id: str = ""
    cycle_id: str = ""
    correlation_id: str = ""
    candidate_sha: str = ""
    plan_sha256: str = ""
    seq: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_int(value: Any, code: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{code}: must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValueError(f"{code}: must be an integer")


def parse_event(payload: Mapping[str, Any]) -> AIXEvent:
    """Parse one raw event dict, failing closed with a coded ValueError."""
    if not isinstance(payload, Mapping):
        raise ValueError("EVENT_NOT_AN_OBJECT: event payload must be a mapping")
    event_id = _text(payload.get("event_id", payload.get("id", "")))
    if not event_id:
        raise ValueError("EVENT_ID_REQUIRED: event carries no event_id")
    event_type = _text(payload.get("event_type", payload.get("type", "")))
    if not event_type:
        raise ValueError("EVENT_TYPE_REQUIRED: event carries no event_type")
    tenant_id = _text(payload.get("tenant_id", payload.get("tenant", "")))
    if not tenant_id:
        raise ValueError("TENANT_REQUIRED: event carries no tenant_id")
    task_id = _text(payload.get("task_id", ""))
    task_number = _optional_int(payload.get("task_number"), "TASK_NUMBER_INVALID")
    if not task_id and task_number is None:
        raise ValueError("TASK_REF_REQUIRED: event carries no task_id/task_number")
    correlation = _text(payload.get("correlation_id", "")) or event_id
    seq = _optional_int(payload.get("seq"), "SEQ_INVALID")
    return AIXEvent(
        event_id=event_id,
        event_type=event_type,
        tenant_id=tenant_id,
        task_id=task_id or None,
        task_number=task_number,
        objective_id=_text(payload.get("objective_id", "")),
        cycle_id=_text(payload.get("cycle_id", "")),
        correlation_id=correlation,
        candidate_sha=_text(payload.get("candidate_sha", "")),
        plan_sha256=_text(payload.get("plan_sha256", "")),
        seq=seq,
        raw=dict(payload),
    )


def merge_detail(event: AIXEvent, detail: Mapping[str, Any] | None) -> AIXEvent:
    """Fill blank event fields from task detail (event values always win)."""
    if not detail:
        return event
    def pick(name: str, current: str) -> str:
        return current or _text(detail.get(name, ""))
    task_id = event.task_id or _text(detail.get("task_id", "")) or None
    task_number = event.task_number
    if task_number is None:
        task_number = _optional_int(detail.get("task_number"), "TASK_NUMBER_INVALID")
    seq = event.seq
    if seq is None:
        seq = _optional_int(detail.get("seq"), "SEQ_INVALID")
    return AIXEvent(
        event_id=event.event_id,
        event_type=event.event_type,
        tenant_id=pick("tenant_id", event.tenant_id),
        task_id=task_id,
        task_number=task_number,
        objective_id=pick("objective_id", event.objective_id),
        cycle_id=pick("cycle_id", event.cycle_id),
        correlation_id=pick("correlation_id", event.correlation_id),
        candidate_sha=pick("candidate_sha", event.candidate_sha),
        plan_sha256=pick("plan_sha256", event.plan_sha256),
        seq=seq,
        raw=event.raw,
    )
