from __future__ import annotations

from typing import Any

import httpx

from backend.hyeve_aix_service.config import settings


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalize_model(row: dict[str, Any]) -> dict[str, Any]:
    pricing = _as_dict(row.get("pricing"))
    top_provider = _as_dict(row.get("top_provider"))
    return {
        "model_id": str(row.get("id") or ""),
        "model_name": str(row.get("name") or row.get("id") or ""),
        "context_length": row.get("context_length"),
        "pricing": {
            "prompt": _to_float(pricing.get("prompt")),
            "completion": _to_float(pricing.get("completion")),
            "request": _to_float(pricing.get("request")),
            "image": _to_float(pricing.get("image")),
        },
        "top_provider": {
            "is_moderated": bool(top_provider.get("is_moderated")) if top_provider else None,
            "max_completion_tokens": top_provider.get("max_completion_tokens"),
        },
    }


async def fetch_openrouter_model_catalog(*, allowed_patterns: list[str] | None = None) -> dict[str, Any]:
    api_key = str(settings.OPENROUTER_API_KEY or "").strip()
    if not api_key:
        return {"status": "skipped", "reason": "OPENROUTER_API_KEY_MISSING", "total": 0, "items": []}

    base_url = str(settings.OPENROUTER_API_URL).rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    referer = str(settings.OPENROUTER_HTTP_REFERER or "").strip()
    title = str(settings.OPENROUTER_APP_TITLE or "").strip()
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title

    try:
        async with httpx.AsyncClient(timeout=float(settings.OPENROUTER_API_TIMEOUT_SECONDS)) as client:
            response = await client.get(f"{base_url}/models", headers=headers)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        return {"status": "error", "reason": str(exc), "total": 0, "items": []}

    rows = _as_list(payload.get("data")) if isinstance(payload, dict) else []
    normalized = [_normalize_model(row) for row in rows if isinstance(row, dict)]
    allowed = [item for item in (allowed_patterns or []) if str(item).strip()]
    return {
        "status": "ok",
        "reason": None,
        "total": len(normalized),
        "allowed_patterns": allowed,
        "items": normalized,
    }
