from __future__ import annotations

from typing import Any

from backend.legacy.orphaned_services.aix_ayrpack_authority_service import is_ayrpack_authoritative


def _normalize_text(value: Any) -> str | None:
    if hasattr(value, "value"):
        value = value.value
    text = str(value or "").strip()
    return text or None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in list(value or []) if str(item).strip()]


def _task_metadata(task: dict[str, Any]) -> dict[str, Any]:
    metadata = task.get("metadata_json")
    if isinstance(metadata, dict):
        return dict(metadata)
    metadata = task.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _packet(task: dict[str, Any]) -> dict[str, Any]:
    packet = _task_metadata(task).get("ayrpack")
    return dict(packet) if isinstance(packet, dict) else {}


def _event_entries(packet: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    authoritative_kinds = {"decision", "handoff"}
    raw_entries = packet.get("audit_events") or packet.get("spine") or []
    entries = [dict(item) for item in raw_entries if isinstance(item, dict)]
    entries.sort(key=lambda item: int(item.get("index") or 0))
    if limit > 0:
        entries = entries[-limit:]
    normalized: list[dict[str, Any]] = []
    for item in entries:
        kind = _normalize_text(item.get("kind"))
        details = _as_dict(item.get("details"))
        normalized.append(
            {
                "entry_id": _normalize_text(item.get("entry_id")),
                "index": int(item.get("index") or 0),
                "kind": kind,
                "timestamp_utc": _normalize_text(item.get("timestamp_utc")),
                "phase": _normalize_text(item.get("phase")),
                "status": _normalize_text(item.get("status")),
                "title": _normalize_text(item.get("title")),
                "summary": _normalize_text(item.get("summary")),
                "actor": _as_dict(item.get("actor")) or None,
                "contract_ref": _as_dict(item.get("contract_ref")) or None,
                "reason_code": _normalize_text(details.get("reason_code")) or _normalize_text(details.get("reason")),
                "authoritative_transition": str(kind or "").lower() in authoritative_kinds,
                "classification": "audit",
            }
        )
    return normalized


def build_lifecycle_event_stream_view(
    *,
    task: dict[str, Any],
    fast_ledger: dict[str, Any] | None = None,
    event_limit: int = 25,
) -> dict[str, Any]:
    packet = _packet(task)
    frame = _as_dict(packet.get("active_frame"))
    lifecycle_authoritative = is_ayrpack_authoritative(packet)
    authoritative_frame = frame if lifecycle_authoritative else {}
    frame_contract_ref = _as_dict(authoritative_frame.get("contract_ref"))
    packet_contract_ref = _as_dict(packet.get("contract_ref"))
    contract_ref = frame_contract_ref or packet_contract_ref
    entries = _event_entries(packet, limit=max(1, int(event_limit)))

    ledger = _as_dict(fast_ledger)
    latest = _as_dict(ledger.get("latest"))
    latest_event = _as_dict(latest.get("event"))
    latest_digest = _as_dict(latest_event.get("execution_digest"))
    control_plane = _as_dict(latest_event.get("control_plane_outcome"))
    mutation = _as_dict(latest_digest.get("mutation"))

    current_state = _normalize_text(authoritative_frame.get("status")) or _normalize_text(
        control_plane.get("handoff_status")
    )
    current_summary = (
        _normalize_text(authoritative_frame.get("summary"))
        or _normalize_text(control_plane.get("reason_summary"))
        or _normalize_text(latest.get("summary"))
    )
    current_stage = (
        _normalize_text(latest_digest.get("current_stage"))
        or _normalize_text(control_plane.get("phase"))
        or _normalize_text(authoritative_frame.get("phase"))
        or current_state
    )
    latest_run_event = {
        "classification": "projection",
        "run_state": _normalize_text(control_plane.get("status")) or current_state,
        "run_phase": _normalize_text(control_plane.get("phase")) or current_stage,
        "reason_code": _normalize_text(control_plane.get("reason_code"))
        or _normalize_text(latest_digest.get("reason_code")),
        "reason_summary": _normalize_text(control_plane.get("reason_summary"))
        or _normalize_text(latest_digest.get("reason_summary")),
        "current_stage": current_stage,
        "primary_mutation_tool": _normalize_text(mutation.get("primary_mutation_tool")),
        "fallback_occurred": bool(mutation.get("fallback_occurred")),
        "touched_files": _string_list(mutation.get("touched_files") or latest_digest.get("changed_files")),
    }
    return {
        "schema_version": "aix.lifecycle_event_stream.current",
        "classification": "projection",
        "authoritative_sources": {
            "lifecycle": "ayrpack.active_frame.status",
            "contract_ref": "ayrpack.active_frame.contract_ref",
            "spine": "ayrpack.spine",
            "run_context": "fast_ledger.latest.event.execution_digest",
        },
        "task": {
            "classification": "projection",
            "task_id": _normalize_text(task.get("id")),
            "task_number": int(task.get("task_number") or 0) or None,
            "project_id": _normalize_text(task.get("project_id")),
            "task_status": _normalize_text(task.get("status")),
            "title": _normalize_text(task.get("title")),
        },
        "lifecycle": {
            "classification": "source_of_truth" if lifecycle_authoritative else "projection",
            "current_state": current_state,
            "active_frame_kind": _normalize_text(authoritative_frame.get("kind")),
            "active_frame_phase": _normalize_text(authoritative_frame.get("phase")),
            "active_frame_summary": _normalize_text(authoritative_frame.get("summary")),
            "active_frame_updated_at_utc": _normalize_text(authoritative_frame.get("updated_at_utc")),
        },
        "contract": {
            "classification": "source_of_truth" if contract_ref else "projection",
            "current_contract_ref": _normalize_text(contract_ref.get("ref")),
            "current_contract_version": _normalize_text(contract_ref.get("version")),
        },
        "current_location": {
            "classification": "projection",
            "subsystem": "review" if current_state in {"review", "review_enriched"} else "runner",
            "stage": current_stage,
            "status": _normalize_text(control_plane.get("status")) or current_state,
            "summary": current_summary,
        },
        "latest_run_event": latest_run_event,
        "event_stream": {
            "classification": "audit",
            "entries": entries,
        },
        "refs": {
            "classification": "projection",
            "run_ledger_ref": _normalize_text(_as_dict(latest_digest.get("refs")).get("run_ledger_ref")),
            "run_ledger_latest_ref": _normalize_text(_as_dict(latest_digest.get("refs")).get("run_ledger_latest_ref")),
        },
    }


__all__ = ["build_lifecycle_event_stream_view"]
