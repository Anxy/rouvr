from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _decode_jwt_payload(token: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1].replace("-", "+").replace("_", "/")
    padded = payload + "=" * ((4 - (len(payload) % 4)) % 4)
    try:
        decoded = base64.b64decode(padded.encode("ascii"), validate=False).decode("utf-8")
        parsed = json.loads(decoded)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def summarize_bearer_token(
    *, token_name: str, token_value: str | None, warn_before_seconds: int = 3600
) -> dict[str, Any]:
    normalized = _normalize_text(token_value)
    summary: dict[str, Any] = {
        "token_name": token_name,
        "configured": bool(normalized),
        "jwt_like": False,
        "expires_at_utc": None,
        "seconds_remaining": None,
        "expired": False,
        "warning": None,
    }
    if not normalized:
        summary["warning"] = "missing"
        return summary

    payload = _decode_jwt_payload(normalized)
    if payload is None:
        summary["warning"] = "non_jwt_or_unparseable"
        return summary

    summary["jwt_like"] = True
    exp_raw = payload.get("exp")
    if not isinstance(exp_raw, (int, float)):
        summary["warning"] = "jwt_exp_missing"
        return summary

    exp = int(exp_raw)
    expires_at = datetime.fromtimestamp(exp, tz=UTC)
    seconds_remaining = int((expires_at - datetime.now(UTC)).total_seconds())
    summary["expires_at_utc"] = expires_at.isoformat()
    summary["seconds_remaining"] = seconds_remaining
    summary["expired"] = seconds_remaining <= 0
    if seconds_remaining <= 0:
        summary["warning"] = "expired"
    elif seconds_remaining <= int(warn_before_seconds):
        summary["warning"] = "expires_soon"
    return summary
