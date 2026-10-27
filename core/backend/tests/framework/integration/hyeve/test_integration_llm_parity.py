"""Tests for F9-P2 Integration LLM Capability Adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
import pytest

from backend.aix_core.framework.integration.hyeve.adapters.integration_llm import (
    IntegrationLlmCapabilityAdapter,
)
from backend.infrastructure.model_gateway.hyeve_integration_client import (
    ChatCompletionResultV1,
    ChatMessage,
)
from backend.infrastructure.model_gateway.provider_routing import (
    GovernedProviderRouter,
    RouteDecisionV1,
    RouteIdentityV1,
)


@pytest.mark.asyncio
async def test_integration_llm_adapter_success_flow():
    mock_router = GovernedProviderRouter()
    adapter = IntegrationLlmCapabilityAdapter(router=mock_router)

    fake_result = ChatCompletionResultV1(
        output_text="Test response",
        provider="integration:claude-3-7-sonnet",
        resolved_provider="anthropic",
        model="claude-3-7-sonnet",
        usage={"input_tokens": 10, "output_tokens": 20},
    )

    with patch(
        "backend.aix_core.framework.integration.hyeve.adapters.integration_llm.chat_completion",
        new=AsyncMock(return_value=fake_result),
    ) as mock_chat:
        res = await adapter.execute_chat(
            role="planner",
            messages=[ChatMessage(role="user", content="Hello")],
            tenant_id="tenant-1",
            actor_id="actor-1",
            authorization_header="Bearer token",
            model_ref="claude-3-7-sonnet",
        )

        assert res.output_text == "Test response"
        assert res.provider == "integration:claude-3-7-sonnet"
        mock_chat.assert_called_once()


@pytest.mark.asyncio
async def test_integration_llm_adapter_fails_closed_on_denied_route():
    mock_router = GovernedProviderRouter()
    adapter = IntegrationLlmCapabilityAdapter(router=mock_router)

    # Mock router to DENY execution
    deny_decision = RouteDecisionV1(
        disposition="DENY",
        route=None,
        reason_codes=("FORBIDDEN_ROUTE",),
        evidence={},
    )
    with patch.object(mock_router, "resolve_role", return_value=deny_decision), \
         patch.object(mock_router, "resolve", return_value=deny_decision):
        with pytest.raises(PermissionError, match="GovernedProviderRouter denied LLM execution"):
            await adapter.execute_chat(
                role="planner",
                messages=[ChatMessage(role="user", content="Hello")],
                tenant_id="tenant-1",
                actor_id="actor-1",
                authorization_header="Bearer token",
                model_ref="direct_openai",
            )
