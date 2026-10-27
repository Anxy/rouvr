"""Standalone task lifecycle state machine for ayrpack packets.

This module is independent of runner and WorkHub infrastructure.
It operates only on canonical packet state and the task ticket validator.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from enum import Enum
from typing import Any, NotRequired, TypedDict
from uuid import uuid4

from hyeve_core.task_contract_validation import validate_task_ticket_contract

try:
    from hyeve_task_contract_engine import synthesize_task_ticket_contract
except ModuleNotFoundError:
    import sys
    from pathlib import Path

    _HYEVE_ROOT = Path(__file__).resolve().parents[5]
    _ENGINE_PACKAGE_ROOT = _HYEVE_ROOT / "libs" / "hyeve_task_contract_engine"
    if str(_ENGINE_PACKAGE_ROOT) not in sys.path:
        sys.path.insert(0, str(_ENGINE_PACKAGE_ROOT))
    from hyeve_task_contract_engine import synthesize_task_ticket_contract

from .ayrpack_current import (
    ActiveFrameKind,
    ActorRefCurrent,
    AyrpackCurrent,
    ContractRefCurrent,
    SpineEntryCurrent,
    SpineEntryKind,
)


class TaskLifecycleState(str, Enum):
    CREATED = "created"
    REVIEW = "review"
    REVIEW_ENRICHED = "review_enriched"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


class LifecycleTransitionResultCurrent(TypedDict):
    packet: AyrpackCurrent
    applied: bool
    from_state: str
    to_state: str
    entry: SpineEntryCurrent
    validation: NotRequired[dict[str, Any]]


class LifecycleSpineAppendResultCurrent(TypedDict):
    packet: AyrpackCurrent
    entry: SpineEntryCurrent


def build_initial_task_ayrpack(
    *,
    trace_id: str,
    producer: str,
    project_id: str | None = None,
    task_id: str | None = None,
    task_number: int | None = None,
    packet_id: str | None = None,
    lane: str | None = None,
    correlation_id: str | None = None,
    actor: ActorRefCurrent | None = None,
    contract_ref: ContractRefCurrent | None = None,
    parent_packet_id: str | None = None,
    child_packet_ids: list[str] | None = None,
    timestamp_utc: str | None = None,
) -> AyrpackCurrent:
    """Build a canonical created-state ayrpack envelope."""

    now = timestamp_utc or _utcnow()
    return {
        "meta": {
            "schema_version": "Current",
            "trace_id": trace_id,
            "packet_id": packet_id or f"ayrpack-{uuid4().hex}",
            "created_at_utc": now,
            "updated_at_utc": now,
            "producer": producer,
        },
        "header": {
            "packet_type": "ayrpack",
            "packet_version": "Current",
            **({"project_id": project_id} if project_id else {}),
            **({"task_id": task_id} if task_id else {}),
            **({"task_number": task_number} if task_number is not None else {}),
            **({"correlation_id": correlation_id} if correlation_id else {}),
            **({"lane": lane} if lane else {}),
            **({"parent_packet_id": parent_packet_id} if parent_packet_id else {}),
            **({"child_packet_ids": list(child_packet_ids or [])} if child_packet_ids else {}),
        },
        "active_frame": {
            "frame_id": _next_frame_id(),
            "kind": ActiveFrameKind.SYSTEM,
            "phase": TaskLifecycleState.CREATED.value,
            "status": TaskLifecycleState.CREATED.value,
            **({"actor": dict(actor)} if actor else {}),
            "started_at_utc": now,
            "updated_at_utc": now,
            "summary": "task packet created",
            **({"contract_ref": dict(contract_ref)} if contract_ref else {}),
        },
        "spine": [],
        "contract_ref": dict(
            contract_ref or {"kind": "task_ticket", "ref": "workhub://tasks/pending/canonical_task_contract.current"}
        ),
    }


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _next_spine_index(spine: list[SpineEntryCurrent]) -> int:
    if not spine:
        return 0
    return max(int(entry.get("index", 0)) for entry in spine) + 1


def _next_frame_id() -> str:
    return f"frame-{uuid4().hex}"


def _next_entry_id() -> str:
    return f"spine-{uuid4().hex}"


def _packet_spine(packet: AyrpackCurrent) -> list[SpineEntryCurrent]:
    raw_spine = packet.get("spine") or []
    return list(raw_spine)


def _entry_kind_value(entry: SpineEntryCurrent) -> str:
    raw_kind = entry.get("kind")
    if isinstance(raw_kind, Enum):
        return str(raw_kind.value or "")
    return str(raw_kind or "")


def _current_state(packet: AyrpackCurrent) -> str:
    return str(packet.get("active_frame", {}).get("status") or TaskLifecycleState.CREATED.value)


def _default_frame_kind(state: str) -> ActiveFrameKind:
    mapping = {
        TaskLifecycleState.CREATED.value: ActiveFrameKind.SYSTEM,
        TaskLifecycleState.REVIEW.value: ActiveFrameKind.REVIEW,
        TaskLifecycleState.REVIEW_ENRICHED.value: ActiveFrameKind.REVIEW,
        TaskLifecycleState.READY.value: ActiveFrameKind.PLANNER,
        TaskLifecycleState.RUNNING.value: ActiveFrameKind.EXECUTOR,
        TaskLifecycleState.COMPLETED.value: ActiveFrameKind.VERIFIER,
        TaskLifecycleState.DONE.value: ActiveFrameKind.VERIFIER,
        TaskLifecycleState.FAILED.value: ActiveFrameKind.VERIFIER,
        TaskLifecycleState.BLOCKED.value: ActiveFrameKind.OPERATOR,
    }
    return mapping.get(state, ActiveFrameKind.SYSTEM)


def _review_assignee(*, actor: ActorRefCurrent, assigned_to: ActorRefCurrent | None) -> ActorRefCurrent:
    if assigned_to is not None:
        return dict(assigned_to)
    if actor.get("kind") in {"human", "agent"}:
        return dict(actor)
    raise ValueError("review transitions require assigned_to when actor.kind is system")


def _append_spine_entry(
    *,
    packet: AyrpackCurrent,
    entry_kind: SpineEntryKind,
    phase: str,
    status: str,
    actor: ActorRefCurrent,
    title: str,
    summary: str,
    contract_ref: ContractRefCurrent | None,
    timestamp_utc: str,
) -> SpineEntryCurrent:
    spine = _packet_spine(packet)
    entry: SpineEntryCurrent = {
        "entry_id": _next_entry_id(),
        "index": _next_spine_index(spine),
        "kind": entry_kind,
        "timestamp_utc": timestamp_utc,
        "phase": phase,
        "status": status,
        "actor": dict(actor),
        "title": title,
        "summary": summary,
    }
    if contract_ref is not None:
        entry["contract_ref"] = dict(contract_ref)
    spine.append(entry)
    packet.update({"spine": spine})
    return entry


def _latest_authoritative_transition(packet: AyrpackCurrent) -> SpineEntryCurrent | None:
    for entry in reversed(_packet_spine(packet)):
        if _entry_kind_value(entry) in {SpineEntryKind.HANDOFF.value, SpineEntryKind.DECISION.value}:
            return entry
    return None


def assert_lifecycle_packet_invariants(
    *,
    packet: AyrpackCurrent,
    previous_spine_len: int | None = None,
    expected_entry: SpineEntryCurrent | None = None,
) -> None:
    spine = _packet_spine(packet)
    indexes = [int(entry.get("index", -1)) for entry in spine]
    if indexes != sorted(indexes) or len(indexes) != len(set(indexes)):
        raise AssertionError("LIFECYCLE_SPINE_ORDER_INVALID spine index/order must be strictly monotonic")
    if previous_spine_len is not None and len(spine) != previous_spine_len + 1:
        raise AssertionError(
            "LIFECYCLE_SPINE_APPEND_INVALID authoritative lifecycle writes must append exactly one entry"
        )
    if expected_entry is not None:
        if not spine:
            raise AssertionError("LIFECYCLE_SPINE_APPEND_INVALID expected appended entry but spine is empty")
        if spine[-1] != expected_entry:
            raise AssertionError("LIFECYCLE_SPINE_APPEND_INVALID last spine entry does not match appended entry")
    latest_transition = _latest_authoritative_transition(packet)
    if latest_transition is None:
        return
    frame_status = str(packet.get("active_frame", {}).get("status") or "")
    transition_status = str(latest_transition.get("status") or "")
    if frame_status != transition_status:
        raise AssertionError(
            "LIFECYCLE_STATUS_MISMATCH active_frame.status must match latest authoritative lifecycle transition"
        )


def auto_route_created_task(
    *,
    packet: AyrpackCurrent,
    title: str,
    description: str | None,
    metadata: dict[str, Any] | None,
    actor: ActorRefCurrent,
    assigned_to: ActorRefCurrent | None = None,
    timestamp_utc: str | None = None,
) -> LifecycleTransitionResultCurrent:
    """Route a created task to review or ready based on contract completeness."""

    current_state = _current_state(packet)
    if current_state != TaskLifecycleState.CREATED.value:
        raise ValueError(f"auto_route_created_task requires current state 'created', got '{current_state}'")

    synthesized_contract = synthesize_task_ticket_contract(
        title=title,
        description=description,
        metadata=metadata,
    )
    validation = validate_task_ticket_contract(
        title=title,
        description=description,
        metadata={"canonical_task_contract.current": synthesized_contract},
        require_execution_slice=False,
    )
    target_state = TaskLifecycleState.READY.value if bool(validation.get("valid")) else TaskLifecycleState.REVIEW.value
    summary = (
        "contract complete; task routed directly to ready"
        if target_state == TaskLifecycleState.READY.value
        else "contract incomplete; task routed to review"
    )
    return transition_task_state(
        packet=packet,
        to_state=target_state,
        actor=actor,
        assigned_to=assigned_to,
        summary=summary,
        title=f"{current_state} -> {target_state}",
        entry_kind=SpineEntryKind.DECISION,
        contract_ref=dict(packet.get("contract_ref") or {}),
        timestamp_utc=timestamp_utc,
        validation_override=validation,
    )


def transition_task_state(
    *,
    packet: AyrpackCurrent,
    to_state: str,
    actor: ActorRefCurrent,
    assigned_to: ActorRefCurrent | None = None,
    title: str | None = None,
    summary: str | None = None,
    entry_kind: SpineEntryKind | None = None,
    contract_ref: ContractRefCurrent | None = None,
    timestamp_utc: str | None = None,
    task_title: str | None = None,
    task_description: str | None = None,
    task_metadata: dict[str, Any] | None = None,
    validation_override: dict[str, Any] | None = None,
) -> LifecycleTransitionResultCurrent:
    """Apply a lifecycle transition with append-only spine semantics.

    Reversals are intentionally supported; no directional restrictions are imposed.
    """

    packet_copy: AyrpackCurrent = deepcopy(packet)
    initial_spine_len = len(list(packet_copy.get("spine") or []))
    from_state = _current_state(packet_copy)
    event_time = timestamp_utc or _utcnow()
    ref = contract_ref or dict(packet_copy.get("contract_ref") or {})
    requested_kind = entry_kind or SpineEntryKind.HANDOFF

    if to_state == TaskLifecycleState.RUNNING.value and from_state != TaskLifecycleState.READY.value:
        blocked_entry = _append_spine_entry(
            packet=packet_copy,
            entry_kind=SpineEntryKind.DECISION,
            phase=from_state,
            status=from_state,
            actor=actor,
            title=title or f"{from_state} -> running blocked",
            summary=summary or "task cannot enter running without coming from ready",
            contract_ref=ref or None,
            timestamp_utc=event_time,
        )
        packet_copy["active_frame"] = {
            **dict(packet_copy.get("active_frame") or {}),
            "kind": _default_frame_kind(from_state),
            "status": from_state,
            "phase": from_state,
            "updated_at_utc": event_time,
            "summary": blocked_entry["summary"],
            "actor": dict(actor),
        }
        result = {
            "packet": packet_copy,
            "applied": False,
            "from_state": from_state,
            "to_state": to_state,
            "entry": blocked_entry,
        }
        assert_lifecycle_packet_invariants(
            packet=packet_copy,
            previous_spine_len=initial_spine_len,
            expected_entry=blocked_entry,
        )
        return result

    if from_state == TaskLifecycleState.REVIEW_ENRICHED.value and to_state == TaskLifecycleState.READY.value:
        if actor.get("kind") != "human":
            blocked_entry = _append_spine_entry(
                packet=packet_copy,
                entry_kind=SpineEntryKind.DECISION,
                phase=from_state,
                status=from_state,
                actor=actor,
                title=title or f"{from_state} -> ready blocked",
                summary=summary or "review_enriched cannot auto-promote; explicit human confirmation is required",
                contract_ref=ref or None,
                timestamp_utc=event_time,
            )
            packet_copy["active_frame"] = {
                **dict(packet_copy.get("active_frame") or {}),
                "kind": _default_frame_kind(from_state),
                "status": from_state,
                "phase": from_state,
                "updated_at_utc": event_time,
                "summary": blocked_entry["summary"],
                "actor": dict(actor),
            }
            result = {
                "packet": packet_copy,
                "applied": False,
                "from_state": from_state,
                "to_state": to_state,
                "entry": blocked_entry,
            }
            assert_lifecycle_packet_invariants(
                packet=packet_copy,
                previous_spine_len=initial_spine_len,
                expected_entry=blocked_entry,
            )
            return result

    if (
        from_state in {TaskLifecycleState.REVIEW.value, TaskLifecycleState.REVIEW_ENRICHED.value}
        and to_state == TaskLifecycleState.READY.value
    ):
        normalized_metadata = (
            {
                "canonical_task_contract.current": synthesize_task_ticket_contract(
                    title=task_title or "",
                    description=task_description,
                    metadata=task_metadata,
                )
            }
            if validation_override is None
            else task_metadata
        )
        validation = validation_override or validate_task_ticket_contract(
            title=task_title or "",
            description=task_description,
            metadata=normalized_metadata,
            require_execution_slice=False,
        )
        if not bool(validation.get("valid")):
            blocked_entry = _append_spine_entry(
                packet=packet_copy,
                entry_kind=SpineEntryKind.DECISION,
                phase=from_state,
                status=from_state,
                actor=actor,
                title=title or f"{from_state} -> ready blocked",
                summary=summary or "contract incomplete; review cannot promote to ready until validation passes",
                contract_ref=ref or None,
                timestamp_utc=event_time,
            )
            packet_copy["active_frame"] = {
                **dict(packet_copy.get("active_frame") or {}),
                "kind": _default_frame_kind(from_state),
                "status": from_state,
                "phase": from_state,
                "updated_at_utc": event_time,
                "summary": blocked_entry["summary"],
                "actor": dict(actor),
            }
            result = {
                "packet": packet_copy,
                "applied": False,
                "from_state": from_state,
                "to_state": to_state,
                "entry": blocked_entry,
                "validation": validation,
            }
            assert_lifecycle_packet_invariants(
                packet=packet_copy,
                previous_spine_len=initial_spine_len,
                expected_entry=blocked_entry,
            )
            return result

    frame: dict[str, Any] = dict(packet_copy.get("active_frame") or {})
    frame["frame_id"] = str(frame.get("frame_id") or _next_frame_id())
    frame["kind"] = (
        ActiveFrameKind.REVIEW if to_state == TaskLifecycleState.REVIEW.value else _default_frame_kind(to_state)
    )
    frame["phase"] = to_state
    frame["status"] = to_state
    frame["actor"] = dict(actor)
    frame["updated_at_utc"] = event_time
    frame["summary"] = summary or f"state transitioned from {from_state} to {to_state}"
    if not frame.get("started_at_utc"):
        frame["started_at_utc"] = event_time
    if ref:
        frame["contract_ref"] = dict(ref)
    if to_state == TaskLifecycleState.REVIEW.value:
        frame["assigned_to"] = _review_assignee(actor=actor, assigned_to=assigned_to)
    else:
        frame.pop("assigned_to", None)
    packet_copy["active_frame"] = frame

    entry = _append_spine_entry(
        packet=packet_copy,
        entry_kind=requested_kind,
        phase=to_state,
        status=to_state,
        actor=actor,
        title=title or f"{from_state} -> {to_state}",
        summary=summary or f"state transitioned from {from_state} to {to_state}",
        contract_ref=ref or None,
        timestamp_utc=event_time,
    )
    result: LifecycleTransitionResultCurrent = {
        "packet": packet_copy,
        "applied": True,
        "from_state": from_state,
        "to_state": to_state,
        "entry": entry,
    }
    if validation_override is not None:
        result["validation"] = validation_override
    assert_lifecycle_packet_invariants(
        packet=packet_copy,
        previous_spine_len=initial_spine_len,
        expected_entry=entry,
    )
    return result


def append_packet_event(
    *,
    packet: AyrpackCurrent,
    actor: ActorRefCurrent,
    title: str,
    summary: str,
    status: str | None = None,
    contract_ref: ContractRefCurrent | None = None,
    timestamp_utc: str | None = None,
) -> LifecycleSpineAppendResultCurrent:
    packet_copy: AyrpackCurrent = deepcopy(packet)
    initial_spine_len = len(list(packet_copy.get("spine") or []))
    current_state = _current_state(packet_copy)
    event_time = timestamp_utc or _utcnow()
    ref = contract_ref or dict(packet_copy.get("contract_ref") or {})
    entry = _append_spine_entry(
        packet=packet_copy,
        entry_kind=SpineEntryKind.EVENT,
        phase=current_state,
        status=status or current_state,
        actor=actor,
        title=title,
        summary=summary,
        contract_ref=ref or None,
        timestamp_utc=event_time,
    )
    frame: dict[str, Any] = dict(packet_copy.get("active_frame") or {})
    frame["updated_at_utc"] = event_time
    frame["actor"] = dict(actor)
    frame["summary"] = summary
    packet_copy["active_frame"] = frame
    assert_lifecycle_packet_invariants(
        packet=packet_copy,
        previous_spine_len=initial_spine_len,
        expected_entry=entry,
    )
    return {"packet": packet_copy, "entry": entry}


__all__ = [
    "LifecycleSpineAppendResultCurrent",
    "LifecycleTransitionResultCurrent",
    "append_packet_event",
    "TaskLifecycleState",
    "assert_lifecycle_packet_invariants",
    "auto_route_created_task",
    "build_initial_task_ayrpack",
    "transition_task_state",
]
