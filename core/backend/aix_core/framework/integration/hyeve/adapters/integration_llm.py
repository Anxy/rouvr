"""AIX Framework Hyeve Integration LLM Capability Adapter.

Sprint: F9 Hyeve Capability Layer (F9-P2)
Grounded in: GovernedProviderRouter, hyeve_integration_client.py.
"""

from __future__ import annotations

from typing import Any

from backend.infrastructure.model_gateway.hyeve_integration_client import (
    ChatCompletionResultV1,
    ChatMessage,
    chat_completion,
)
from backend.infrastructure.model_gateway.provider_routing import (
    GovernedProviderRouter,
    RouteDecisionV1,
)


class IntegrationLlmCapabilityAdapter:
    """Canonical capability adapter executing LLM chat completion through Hyeve Integration."""

    def __init__(self, router: GovernedProviderRouter | None = None) -> None:
        self._router = router or GovernedProviderRouter()

    async def execute_chat(
        self,
        *,
        role: str,
        messages: list[ChatMessage],
        tenant_id: str,
        actor_id: str,
        authorization_header: str,
        model_ref: str | None = None,
        response_format: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> ChatCompletionResultV1:
        # 1. Governed provider resolution: fail closed on bypass
        if model_ref and str(model_ref).strip():
            route_decision = self._router.resolve(role=role, model_ref=str(model_ref).strip())
        else:
            route_decision = self._router.resolve_role(role)
        if route_decision.disposition != "ALLOW":
            raise PermissionError(
                f"GovernedProviderRouter denied LLM execution for role {role!r}: {route_decision.reason_codes}"
            )

        if not route_decision.route:
            raise PermissionError(f"GovernedProviderRouter returned no route for role {role!r}")

        # 2. Invoke Hyeve Integration client (preserve model= for parity with live sites)
        return await chat_completion(
            messages=messages,
            tenant_id=tenant_id,
            actor_id=actor_id,
            authorization_header=authorization_header,
            response_format=response_format,
            provider=route_decision.route.provider_ref,
            model=model_ref or route_decision.route.model_ref,
            timeout_seconds=timeout_seconds,
        )
