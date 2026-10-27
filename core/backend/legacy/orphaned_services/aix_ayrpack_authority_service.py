from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

AYRPACK_AUTHORITY_STALENESS_WINDOW = timedelta(days=14)
_ALLOWED_FUTURE_SKEW = timedelta(minutes=5)
_POST_EXECUTION_STATUSES = {
    "running",
    "blocked",
    "failed",
    "done",
    "completed",
    "needs_review",
}


def _normalize_text(value: Any) -> str | None:
    if hasattr(value, "value"):
        value = value.value
    text = str(value or "").strip()
    return text or None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _parse_timestamp(value: Any) -> datetime | None:
    text = _normalize_text(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _has_post_execution_signal(
    *,
    control_plane: dict[str, Any] | None = None,
    latest_digest: dict[str, Any] | None = None,
    final_status: Any = None,
) -> bool:
    control = _as_dict(control_plane)
    digest = _as_dict(latest_digest)
    if _normalize_text(control.get("status")) or _normalize_text(control.get("handoff_status")):
        return True
    if _normalize_text(control.get("phase")) or _normalize_text(control.get("reason_code")):
        return True
    if _normalize_text(digest.get("current_stage")) or _normalize_text(digest.get("reason_code")):
        return True
    return _normalize_text(final_status) in _POST_EXECUTION_STATUSES


def is_ayrpack_authoritative(
    ayrpack: Any,
    *,
    now_utc: datetime | None = None,
    max_staleness: timedelta = AYRPACK_AUTHORITY_STALENESS_WINDOW,
) -> bool:
    packet = _as_dict(ayrpack)
    frame = _as_dict(packet.get("active_frame"))
    if not frame:
        return False
    if not _normalize_text(frame.get("status")):
        return False
    frame_timestamp = _parse_timestamp(frame.get("updated_at_utc")) or _parse_timestamp(frame.get("started_at_utc"))
    if frame_timestamp is None:
        return False
    current = now_utc.astimezone(UTC) if isinstance(now_utc, datetime) else datetime.now(UTC)
    if frame_timestamp > current + _ALLOWED_FUTURE_SKEW:
        return False
    return current - frame_timestamp <= max_staleness


def resolve_lifecycle_authority(
    ayrpack: Any,
    *,
    control_plane: dict[str, Any] | None = None,
    latest_digest: dict[str, Any] | None = None,
    final_status: Any = None,
    now_utc: datetime | None = None,
    max_staleness: timedelta = AYRPACK_AUTHORITY_STALENESS_WINDOW,
) -> dict[str, Any]:
    packet = _as_dict(ayrpack)
    frame = _as_dict(packet.get("active_frame"))
    authoritative = is_ayrpack_authoritative(
        packet,
        now_utc=now_utc,
        max_staleness=max_staleness,
    )
    frame_status = _normalize_text(frame.get("status"))
    if authoritative:
        return {
            "authoritative": True,
            "lifecycle_source": "ayrpack.active_frame.status",
            "current_state": frame_status,
            "pending_propagation": False,
        }
    if not frame and _has_post_execution_signal(
        control_plane=control_plane,
        latest_digest=latest_digest,
        final_status=final_status,
    ):
        return {
            "authoritative": False,
            "lifecycle_source": "pending_propagation",
            "current_state": "pending_propagation",
            "pending_propagation": True,
        }
    return {
        "authoritative": False,
        "lifecycle_source": "fallback",
        "current_state": None,
        "pending_propagation": False,
    }


__all__ = [
    "AYRPACK_AUTHORITY_STALENESS_WINDOW",
    "is_ayrpack_authoritative",
    "resolve_lifecycle_authority",
]
