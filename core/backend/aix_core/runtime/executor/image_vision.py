"""OpenRouter vision helpers — describe screenshots and drive UI recreation."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class VisionDescribeResult:
    ok: bool
    description: str
    model: str
    detail: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def _openrouter_key(settings: object | None = None) -> str:
    if settings is not None:
        key = str(getattr(settings, "OPENROUTER_API_KEY", "") or "").strip()
        if key:
            return key
    return str(os.getenv("HYEVE_AIX_SERVICE_OPENROUTER_API_KEY", "") or "").strip()


def _openrouter_url(settings: object | None = None) -> str:
    if settings is not None:
        url = str(getattr(settings, "OPENROUTER_API_URL", "") or "").strip()
        if url:
            return url.rstrip("/")
    return str(os.getenv("HYEVE_AIX_SERVICE_OPENROUTER_API_URL", "https://openrouter.ai/api/v1") or "").rstrip("/")


def _vision_model(settings: object | None = None) -> str:
    if settings is not None:
        model = str(getattr(settings, "OPENROUTER_VISION_MODEL", "") or "").strip()
        if model:
            return model.removeprefix("openrouter/")
    env = str(os.getenv("HYEVE_AIX_SERVICE_OPENROUTER_VISION_MODEL", "") or "").strip()
    if env:
        return env.removeprefix("openrouter/")
    # Standard VL model (the :free slug 404s on OpenRouter).
    return "qwen/qwen2.5-vl-72b-instruct"


def _data_url(*, raw: bytes, mime: str) -> str:
    media = mime if mime.startswith("image/") else "image/png"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{media};base64,{encoded}"


async def describe_image_bytes(
    *,
    raw: bytes,
    mime: str,
    filename: str,
    objective_hint: str = "",
    settings: object | None = None,
) -> VisionDescribeResult:
    """Ask a vision model to inventory UI structure/text from an attached image."""
    key = _openrouter_key(settings)
    if not key:
        return VisionDescribeResult(
            ok=False,
            description="",
            model="",
            detail="OPENROUTER_API_KEY missing",
        )
    if not raw:
        return VisionDescribeResult(ok=False, description="", model="", detail="empty_image")

    model = _vision_model(settings)
    base = _openrouter_url(settings)
    hint = (objective_hint or "").strip()
    prompt = (
        "You are analyzing a UI screenshot attached by a user for an AIX objective.\n"
        "Extract EVERYTHING needed to recreate the same screen in HTML/CSS.\n"
        "Be concrete and exhaustive:\n"
        "1) Layout structure (sections, columns, cards)\n"
        "2) Exact visible text (labels, headings, button copy, placeholders, links)\n"
        "3) Form fields and validation hints if any\n"
        "4) Colors, fonts, spacing, borders, radii, shadows (approximate hex when possible)\n"
        "5) Icons/images and their placement\n"
        "6) Responsive/mobile notes if obvious\n"
        f"Filename: {filename}\n"
        f"User objective hint: {hint or '(none)'}\n"
        "Return a structured plain-text inventory (no markdown code fences)."
    )
    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": _data_url(raw=raw, mime=mime)},
                    },
                ],
            }
        ],
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://127.0.0.1:8010",
        "X-Title": "hyeve-aix-image-vision",
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(f"{base}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
    except Exception as exc:  # noqa: BLE001
        return VisionDescribeResult(
            ok=False,
            description="",
            model=model,
            detail=f"vision_failed:{type(exc).__name__}:{exc}",
        )

    choices = body.get("choices") if isinstance(body, dict) else None
    content = ""
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        content = str((message or {}).get("content") or "").strip()
    usage_raw = body.get("usage") if isinstance(body, dict) else {}
    usage_raw = usage_raw if isinstance(usage_raw, dict) else {}
    prompt_tokens = int(usage_raw.get("prompt_tokens") or 0)
    completion_tokens = int(usage_raw.get("completion_tokens") or 0)
    total_tokens = int(usage_raw.get("total_tokens") or (prompt_tokens + completion_tokens))
    if not content:
        return VisionDescribeResult(
            ok=False,
            description="",
            model=str(body.get("model") or model),
            detail="empty_vision_reply",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
    return VisionDescribeResult(
        ok=True,
        description=content,
        model=str(body.get("model") or model),
        detail="ok",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def image_attachment_payloads(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return image attachments that still have raw bytes available."""
    out: list[dict[str, Any]] = []
    for item in attachments:
        if not isinstance(item, dict):
            continue
        mime = str(item.get("mime") or "").lower()
        filename = str(item.get("filename") or "")
        raw = item.get("raw")
        if not isinstance(raw, (bytes, bytearray)):
            continue
        if mime.startswith("image/") or filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            out.append(item)
    return out
