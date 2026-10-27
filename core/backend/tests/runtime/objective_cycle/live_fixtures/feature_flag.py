"""Controlled live-fixture for multi-file / repair acceptance scenarios."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Mapping, Any

FEATURE = {
    "name": "mutation_summary_live",
    "enabled": True,
    "version": 3,
    "expires_at": None,
}


def is_feature_enabled(
    feature: Mapping[str, Any], *, today: date | None = None
) -> bool:
    """Return whether a feature is enabled and has not expired.

    Expiry is evaluated by UTC calendar date. Missing or null expiry preserves
    the existing enabled-only behavior.
    """
    if not feature.get("enabled", False):
        return False

    expires_at = feature.get("expires_at")
    if expires_at is None:
        return True

    if isinstance(expires_at, datetime):
        expiry_date = expires_at.astimezone(timezone.utc).date()
    elif isinstance(expires_at, date):
        expiry_date = expires_at
    else:
        expiry_text = str(expires_at).replace("Z", "+00:00")
        try:
            expiry_date = date.fromisoformat(expiry_text)
        except ValueError:
            expiry_date = datetime.fromisoformat(expiry_text).astimezone(timezone.utc).date()

    current_date = today if today is not None else datetime.now(timezone.utc).date()
    return expiry_date >= current_date
