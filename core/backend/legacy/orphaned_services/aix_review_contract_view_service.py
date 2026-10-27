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


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _task_metadata(task: dict[str, Any]) -> dict[str, Any]:
    metadata = task.get("metadata_json")
    if isinstance(metadata, dict):
        return dict(metadata)
    metadata = task.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _packet(metadata: dict[str, Any]) -> dict[str, Any]:
    packet = metadata.get("ayrpack")
    return dict(packet) if isinstance(packet, dict) else {}


def _version_entry(item: dict[str, Any]) -> dict[str, Any]:
    contract_ref = _as_dict(item.get("contract_ref"))
    base_contract_ref = _as_dict(item.get("base_contract_ref"))
    diff = _as_dict(item.get("diff"))
    return {
        "created_at_utc": _normalize_text(item.get("created_at_utc")),
        "source": _normalize_text(item.get("source")),
        "summary": _normalize_text(item.get("summary")) or _normalize_text(diff.get("summary")),
        "changed_paths": [str(path).strip() for path in _as_list(item.get("changed_paths")) if str(path).strip()],
        "contract_ref": contract_ref or None,
        "base_contract_ref": base_contract_ref or None,
        "diff": diff or None,
    }


def build_review_contract_view(*, task: dict[str, Any]) -> dict[str, Any]:
    metadata = _task_metadata(task)
    packet = _packet(metadata)
    frame = _as_dict(packet.get("active_frame"))
    lifecycle_resolution = resolve_lifecycle_authority(packet, final_status=task.get("status"))
    lifecycle_authoritative = bool(lifecycle_resolution.get("authoritative"))
    authoritative_frame = frame if lifecycle_authoritative else {}
    frame_contract_ref = _as_dict(authoritative_frame.get("contract_ref"))
    packet_contract_ref = _as_dict(packet.get("contract_ref"))
    contract_ref = frame_contract_ref or packet_contract_ref
    versions = [
        _version_entry(item) for item in _as_list(metadata.get("ticket_contract_versions")) if isinstance(item, dict)
    ]
    latest_version = versions[-1] if versions else {}
    current_state = _normalize_text(authoritative_frame.get("status")) or _normalize_text(
        lifecycle_resolution.get("current_state")
    )
    current_summary = _normalize_text(authoritative_frame.get("summary"))
    lifecycle_source = str(lifecycle_resolution.get("lifecycle_source") or "fallback")
    return {
        "schema_version": "aix.review_contract_view.current",
        "view_classification": "projection",
        "authoritative_sources": {
            "lifecycle": "ayrpack.active_frame.status",
            "current_contract_ref": "ayrpack.active_frame.contract_ref",
            "review_contract_versions": "metadata.ticket_contract_versions",
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
            "current_state": current_state,
            "lifecycle_source": lifecycle_source,
            "active_frame_kind": _normalize_text(authoritative_frame.get("kind")),
            "active_frame_summary": current_summary,
            "active_frame_updated_at_utc": _normalize_text(authoritative_frame.get("updated_at_utc")),
        },
        "contract": {
            "source_classification": "source_of_truth" if contract_ref else "projection",
            "current_contract_ref": _normalize_text(contract_ref.get("ref")),
            "current_contract_version": _normalize_text(contract_ref.get("version")),
            "pending_human_confirmation": current_state == "review_enriched",
        },
        "review_enrichment": {
            "source_classification": "audit",
            "latest_summary": _normalize_text(latest_version.get("summary")),
            "latest_changed_paths": [
                str(path).strip() for path in _as_list(latest_version.get("changed_paths")) if str(path).strip()
            ],
            "latest_diff": _as_dict(latest_version.get("diff")) or None,
            "versions": versions,
        },
        "current_location": {
            "source_classification": "projection",
            "subsystem": "review" if current_state in {"review", "review_enriched"} else "task",
            "stage": current_state or "unknown",
            "status": current_state,
            "lifecycle_source": lifecycle_source,
            "summary": current_summary or _normalize_text(latest_version.get("summary")),
        },
    }


__all__ = ["build_review_contract_view"]
