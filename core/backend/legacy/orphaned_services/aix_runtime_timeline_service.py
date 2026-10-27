from __future__ import annotations

from typing import Any

from backend.legacy.orphaned_services.aix_ayrpack_authority_service import resolve_lifecycle_authority


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
    metadata = _task_metadata(task)
    packet = metadata.get("ayrpack")
    return dict(packet) if isinstance(packet, dict) else {}


def _spine_entries(packet: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    raw_entries = packet.get("audit_events") or packet.get("spine") or []
    entries = [dict(item) for item in raw_entries if isinstance(item, dict)]
    entries.sort(key=lambda item: int(item.get("index") or 0))
    if limit > 0:
        entries = entries[-limit:]
    normalized: list[dict[str, Any]] = []
    for item in entries:
        details = _as_dict(item.get("details"))
        scope = _as_dict(details.get("scope"))
        normalized.append(
            {
                "entry_id": _normalize_text(item.get("entry_id")),
                "index": int(item.get("index") or 0),
                "kind": _normalize_text(item.get("kind")),
                "timestamp_utc": _normalize_text(item.get("timestamp_utc")),
                "phase": _normalize_text(item.get("phase")),
                "status": _normalize_text(item.get("status")),
                "title": _normalize_text(item.get("title")),
                "summary": _normalize_text(item.get("summary")),
                "actor": _as_dict(item.get("actor")) or None,
                "reason_code": _normalize_text(details.get("reason_code")) or _normalize_text(details.get("reason")),
                "primitive": _normalize_text(details.get("primitive")),
                "scope": scope or None,
            }
        )
    return normalized


def _last_authoritative_transition(entries: list[dict[str, Any]], *, current_state: str | None) -> dict[str, Any]:
    authoritative_kinds = {"decision", "handoff"}
    target_state = _normalize_text(current_state)
    for item in reversed(entries):
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in authoritative_kinds:
            continue
        if target_state and str(item.get("status") or "").strip() == target_state:
            return dict(item)
    for item in reversed(entries):
        kind = str(item.get("kind") or "").strip().lower()
        if kind in authoritative_kinds:
            return dict(item)
    return {}


def build_runtime_timeline_view(
    *,
    task: dict[str, Any],
    fast_ledger: dict[str, Any] | None = None,
    timeline_limit: int = 10,
) -> dict[str, Any]:
    packet = _packet(task)
    frame = _as_dict(packet.get("active_frame"))
    ledger = _as_dict(fast_ledger)
    latest = _as_dict(ledger.get("latest"))
    latest_event = _as_dict(latest.get("event"))
    latest_digest = _as_dict(latest_event.get("execution_digest"))
    control_plane = _as_dict(latest_event.get("control_plane_outcome"))
    lifecycle_resolution = resolve_lifecycle_authority(
        packet,
        control_plane=control_plane,
        latest_digest=latest_digest,
        final_status=control_plane.get("handoff_status") or control_plane.get("status"),
    )
    lifecycle_authoritative = bool(lifecycle_resolution.get("authoritative"))
    authoritative_frame = frame if lifecycle_authoritative else {}
    packet_contract_ref = _as_dict(packet.get("contract_ref"))
    frame_contract_ref = _as_dict(authoritative_frame.get("contract_ref"))
    contract_ref = frame_contract_ref or packet_contract_ref
    entries = _spine_entries(packet, limit=max(1, int(timeline_limit)))
    current_state = _normalize_text(authoritative_frame.get("status")) or _normalize_text(
        lifecycle_resolution.get("current_state")
    )
    last_transition = _last_authoritative_transition(entries, current_state=current_state)
    digest_mutation = _as_dict(latest_digest.get("mutation"))
    digest_attestation = _as_dict(latest_digest.get("attestation"))
    changed_files = _string_list(digest_mutation.get("touched_files") or latest_digest.get("changed_files"))
    fallback_entries = [
        dict(item) for item in list(digest_mutation.get("fallback_entries") or []) if isinstance(item, dict)
    ]

    execution_attestation = _as_dict(authoritative_frame.get("execution_capability_attestation")) or digest_attestation
    contract_version = _normalize_text(contract_ref.get("version"))
    contract_pointer = _normalize_text(contract_ref.get("ref"))
    current_summary = (
        _normalize_text(authoritative_frame.get("summary"))
        or _normalize_text(control_plane.get("reason_summary"))
        or _normalize_text(latest.get("summary"))
    )
    current_stage = (
        _normalize_text(latest_digest.get("current_stage"))
        or _normalize_text(control_plane.get("phase"))
        or _normalize_text(authoritative_frame.get("phase"))
    )
    lifecycle_source = str(lifecycle_resolution.get("lifecycle_source") or "fallback")
    current_location = {
        "source_classification": "projection",
        "subsystem": (
            "review" if current_state in {"review", "review_enriched"} else ("runner" if current_stage else "task")
        ),
        "stage": current_stage or current_state,
        "status": current_state
        if lifecycle_source != "fallback"
        else (_normalize_text(control_plane.get("status")) or current_state),
        "lifecycle_source": lifecycle_source,
        "summary": current_summary,
        "reason_code": _normalize_text(control_plane.get("reason_code"))
        or _normalize_text(latest_digest.get("reason_code")),
        "reason_summary": _normalize_text(control_plane.get("reason_summary"))
        or _normalize_text(latest_digest.get("reason_summary")),
        "blocked_reason": _normalize_text(control_plane.get("blocked_reason")),
        "since": _normalize_text(control_plane.get("blocked_at"))
        or _normalize_text(authoritative_frame.get("updated_at_utc")),
    }
    return {
        "schema_version": "aix.runtime_timeline.current",
        "timeline_classification": "projection",
        "authoritative_sources": {
            "lifecycle": "ayrpack.active_frame.status",
            "contract_ref": "ayrpack.contract_ref",
            "spine": "ayrpack.spine",
            "attestation": "ayrpack.active_frame.execution_capability_attestation",
            "run_context": "fast_ledger.latest.event.execution_digest",
        },
        "task": {
            "source_classification": "projection",
            "task_id": _normalize_text(task.get("id")),
            "task_number": int(task.get("task_number") or 0) or None,
            "project_id": _normalize_text(task.get("project_id")),
            "task_status": _normalize_text(task.get("status")),
            "title": _normalize_text(task.get("title")),
        },
        "lifecycle": {
            "source_classification": "source_of_truth" if lifecycle_authoritative else "projection",
            "current_state": current_state or _normalize_text(control_plane.get("handoff_status")),
            "lifecycle_source": lifecycle_source,
            "active_frame_kind": _normalize_text(authoritative_frame.get("kind")),
            "active_frame_phase": _normalize_text(authoritative_frame.get("phase")),
            "active_frame_summary": _normalize_text(authoritative_frame.get("summary")),
            "active_frame_updated_at_utc": _normalize_text(authoritative_frame.get("updated_at_utc")),
        },
        "contract": {
            "source_classification": "source_of_truth" if contract_ref else "projection",
            "current_contract_ref": contract_pointer,
            "current_contract_version": contract_version,
            "pending_human_confirmation": (current_state == "review_enriched"),
        },
        "attestation": {
            "source_classification": "source_of_truth"
            if authoritative_frame.get("execution_capability_attestation")
            else "projection",
            "passed": execution_attestation.get("passed"),
            "reason_code": _normalize_text(execution_attestation.get("reason_code")),
            "reason_summary": _normalize_text(execution_attestation.get("reason_summary")),
            "failed_checks": [
                dict(item) for item in list(execution_attestation.get("failed_checks") or []) if isinstance(item, dict)
            ],
        },
        "mutation": {
            "source_classification": "projection",
            "chosen_mutation_path": _normalize_text(digest_mutation.get("chosen_mutation_path")),
            "primary_mutation_tool": _normalize_text(digest_mutation.get("primary_mutation_tool")),
            "fallback_occurred": bool(digest_mutation.get("fallback_occurred")),
            "fallback_entries": fallback_entries,
            "touched_files": changed_files,
        },
        "current_location": current_location,
        "last_authoritative_transition": {
            "source_classification": "audit",
            **last_transition,
        }
        if last_transition
        else {
            "source_classification": "audit",
        },
        "timeline": {
            "source_classification": "audit",
            "entries": entries,
        },
        "refs": {
            "source_classification": "projection",
            "run_ledger_ref": _normalize_text(_as_dict(latest_digest.get("refs")).get("run_ledger_ref")),
            "run_ledger_latest_ref": _normalize_text(_as_dict(latest_digest.get("refs")).get("run_ledger_latest_ref")),
        },
    }


__all__ = ["build_runtime_timeline_view"]
