"""Real HTTP client for Hyeve Integration's governed chat completion endpoint.

This is the concrete client `provider_routing.py`'s `GovernedProviderRouter` was
missing: that module only decides whether a route is ALLOWED, it never actually calls
anything. This module is the one place in AIX that makes the outbound HTTP request —
callers must run `GovernedProviderRouter.resolve()` first and only call this on ALLOW,
never as a substitute for that check.

Forwards the caller's own Authorization header rather than holding a separate service
credential — matches the existing pattern in `routers/model_gateway_router.py`'s
`sync_model_registry_from_integration`, which already reaches Hyeve Integration this way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx

from backend.hyeve_aix_service.config import settings

ChatRole = Literal["system", "user", "assistant"]


class HyeveIntegrationChatError(RuntimeError):
    def __init__(self, code: str, *, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class ChatMessage:
    role: ChatRole
    content: str


@dataclass(frozen=True)
class ChatCompletionResultV1:
    output_text: str
    provider: str
    resolved_provider: str
    model: str | None
    usage: dict[str, Any]


def _resolve_integration_base_url() -> str:
    base_url = str(settings.RUNTIME_PLANNER_API_URL or "http://integration:8020").strip().rstrip("/")
    return base_url or "http://integration:8020"


async def chat_completion(
    *,
    messages: list[ChatMessage],
    tenant_id: str,
    actor_id: str,
    authorization_header: str,
    response_format: dict[str, Any] | None = None,
    provider: str = "auto",
    model: str | None = None,
    temperature: float | None = None,
    timeout_seconds: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> ChatCompletionResultV1:
    """Call Hyeve Integration's `POST /api/integrations/llm/chat`. Raises
    `HyeveIntegrationChatError` on any non-2xx response or transport failure — never
    returns a synthetic/fallback reply. `model=None` lets Hyeve Integration apply its
    own default-model resolution rather than this client guessing one."""
    if not authorization_header:
        raise HyeveIntegrationChatError("AUTHORIZATION_REQUIRED", detail="no Authorization header to forward")
    if not messages:
        raise HyeveIntegrationChatError("MESSAGES_REQUIRED", detail="at least one message is required")

    url = f"{_resolve_integration_base_url()}/api/integrations/llm/chat"
    payload: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
    }
    if response_format is not None:
        payload["response_format"] = response_format
    if temperature is not None:
        payload["temperature"] = temperature
    if metadata:
        payload["metadata"] = dict(metadata)

    timeout = float(timeout_seconds if timeout_seconds is not None else settings.API_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": authorization_header,
                    "X-Tenant-ID": tenant_id,
                    "X-Hyeve-Tenant-Id": tenant_id,
                    "X-Actor-ID": actor_id,
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise HyeveIntegrationChatError("INTEGRATION_UNAVAILABLE", detail=exc.__class__.__name__) from exc

    if response.status_code != 200:
        raise HyeveIntegrationChatError(
            "INTEGRATION_CHAT_REJECTED",
            detail=f"status={response.status_code} body={response.text[:500]}",
        )

    body = response.json()
    data = body.get("data") if isinstance(body, dict) else None
    envelope_ok = isinstance(body, dict) and bool(body.get("success", True)) and isinstance(data, dict)
    if not envelope_ok:
        raise HyeveIntegrationChatError("INTEGRATION_CHAT_FAILED", detail=str(body)[:500])
    if data.get("status") != "completed":
        err = data.get("error") or data
        detail = str(err)[:800]
        code = "INTEGRATION_CHAT_FAILED"
        # Surface OpenRouter billing / auth failures clearly (Integration often still
        # returns HTTP 200 with status=failed in the envelope).
        err_text = detail.lower()
        if "402" in detail or "insufficient credits" in err_text or "openrouter_credits" in err_text:
            code = "OPENROUTER_INSUFFICIENT_CREDITS"
        elif "401" in detail or "invalid api key" in err_text or "unauthorized" in err_text:
            code = "OPENROUTER_UNAUTHORIZED"
        elif "429" in detail or "ratelimit" in err_text or "rate_limit" in err_text or "rate limit" in err_text:
            code = "OPENROUTER_RATE_LIMITED"
        raise HyeveIntegrationChatError(code, detail=detail)

    return ChatCompletionResultV1(
        output_text=str(data.get("output_text") or ""),
        provider=str(data.get("provider") or provider),
        resolved_provider=str(data.get("resolved_provider") or ""),
        model=data.get("model"),
        usage=dict(data.get("usage") or {}),
    )


__all__ = [
    "ChatRole",
    "ChatMessage",
    "ChatCompletionResultV1",
    "HyeveIntegrationChatError",
    "chat_completion",
]
