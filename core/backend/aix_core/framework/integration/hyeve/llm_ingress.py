"""F9 LLM ingress: GovernedProviderRouter + optional IntegrationLlmCapabilityAdapter."""

from __future__ import annotations

from typing import Any

from backend.infrastructure.model_gateway.hyeve_integration_client import (
    ChatCompletionResultV1,
    ChatMessage,
    chat_completion,
)
from backend.infrastructure.model_gateway.provider_routing import GovernedProviderRouter


def hyeve_capabilities_enabled(settings: object | None = None) -> bool:
    if settings is not None and hasattr(settings, "AIX_FRAMEWORK_HYEVE_CAPABILITIES"):
        return bool(getattr(settings, "AIX_FRAMEWORK_HYEVE_CAPABILITIES"))
    try:
        from backend.hyeve_aix_service.config import settings as _settings

        return bool(getattr(_settings, "AIX_FRAMEWORK_HYEVE_CAPABILITIES", False))
    except Exception:
        return False


async def governed_chat_completion(
    *,
    role: str,
    messages: list[ChatMessage],
    tenant_id: str,
    actor_id: str,
    authorization_header: str,
    model_ref: str | None = None,
    response_format: dict[str, Any] | None = None,
    timeout_seconds: float | None = None,
    settings: object | None = None,
    router: GovernedProviderRouter | None = None,
    use_hyeve_adapter: bool | None = None,
) -> ChatCompletionResultV1:
    """Single LLM ingress: GPR always; F9 adapter when flag on."""
    enabled = hyeve_capabilities_enabled(settings) if use_hyeve_adapter is None else bool(use_hyeve_adapter)
    gpr = router or GovernedProviderRouter()

    if enabled:
        from backend.aix_core.framework.integration.hyeve.adapters.integration_llm import (
            IntegrationLlmCapabilityAdapter,
        )
        from backend.aix_core.framework.integration.hyeve.resolver import (
            get_hyeve_capability_resolver,
        )

        if not get_hyeve_capability_resolver().has_capability("llm.chat_completion"):
            raise PermissionError("Hyeve capability llm.chat_completion missing from manifest")
        adapter = IntegrationLlmCapabilityAdapter(router=gpr)
        return await adapter.execute_chat(
            role=role,
            messages=messages,
            tenant_id=tenant_id,
            actor_id=actor_id,
            authorization_header=authorization_header,
            model_ref=model_ref,
            response_format=response_format,
            timeout_seconds=timeout_seconds,
        )

    decision = gpr.resolve(role=role, model_ref=model_ref)
    if decision.disposition != "ALLOW":
        raise PermissionError(
            f"GovernedProviderRouter denied LLM for role {role!r}: {decision.reason_codes}"
        )
    return await chat_completion(
        messages=messages,
        tenant_id=tenant_id,
        actor_id=actor_id,
        authorization_header=authorization_header,
        response_format=response_format,
        model=model_ref,
        timeout_seconds=timeout_seconds,
        provider=(decision.route.provider_ref if decision.route else "auto"),
    )


__all__ = ["governed_chat_completion", "hyeve_capabilities_enabled"]
