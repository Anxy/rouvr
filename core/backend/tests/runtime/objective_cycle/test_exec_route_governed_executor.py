"""EXEC-ROUTE: governed executor codegen — no direct OpenRouter from AIX CREATE path."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.executor.source_patch_codegen import (
    generate_source_patch,
)
from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.infrastructure.model_gateway.hyeve_integration_client import (
    ChatCompletionResultV1,
    HyeveIntegrationChatError,
)
from backend.infrastructure.model_gateway.intelligence_layer.execution_routing import (
    resolve_intelligence_role,
)
from backend.infrastructure.model_gateway.provider_routing import (
    GovernedProviderRouter,
    RouteDecisionV1,
)


def _empty_discovery() -> DiscoveryReport:
    # Seed an authorized empty CREATE target so auth/route gates are exercised
    # (genuinely empty discovery now fails closed as CREATE_CONTEXT_ABSENT).
    return DiscoveryReport(config_files={"proposed/erp-system/index.html": ""})


def test_source_patch_codegen_has_no_direct_openrouter_http() -> None:
    """Static: governed CREATE path must not POST OpenRouter or read AIX OR keys."""
    here = Path(__file__).resolve()
    path = here.parents[3] / "aix_core/runtime/executor/source_patch_codegen.py"
    assert path.is_file(), path
    text = path.read_text(encoding="utf-8")
    assert "_openrouter_patch_once" not in text
    assert "_openrouter_key" not in text
    assert "openrouter.ai" not in text.lower()
    assert "/chat/completions" not in text
    assert "GovernedProviderRouter" in text
    assert "chat_completion" in text
    assert "resolve_intelligence_role" in text


def test_executor_role_route_is_allow_via_gpr() -> None:
    route = resolve_intelligence_role("executor")
    assert route.role == "executor"
    assert route.model_ref.strip()
    assert route.decision.disposition == "ALLOW"
    assert route.decision.route is not None
    assert route.decision.route.gateway == "hyeve_integration"
    assert route.decision.route.route_kind == "governed"


def test_gpr_denies_direct_provider_route_kind() -> None:
    decision = GovernedProviderRouter().resolve(
        role="executor",
        model_ref="openrouter/openai/gpt-4o-mini",
        route_kind="direct_provider",
    )
    assert decision.disposition == "DENY"
    assert "DIRECT_PROVIDER_ROUTE_FORBIDDEN" in decision.reason_codes


@pytest.mark.asyncio
async def test_generate_source_patch_fails_closed_without_auth() -> None:
    result = await generate_source_patch(
        objective="Create proposed/erp-system/index.html with a heading",
        discovery=_empty_discovery(),
        tenant_id="tenant-1",
        actor_id="actor-1",
        authorization_header=None,
    )
    assert result.no_files_identified is True
    assert result.failure_code == "EXECUTOR_AUTH_REQUIRED"
    assert "direct OpenRouter is forbidden" in (result.reason or "")


@pytest.mark.asyncio
async def test_generate_source_patch_fails_closed_on_route_deny(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _deny(**_kwargs: Any) -> RouteDecisionV1:
        return RouteDecisionV1(
            disposition="DENY",
            route=None,
            reason_codes=("GATEWAY_NOT_ALLOWED",),
            evidence={"gateway": "direct"},
        )

    monkeypatch.setattr(GovernedProviderRouter, "resolve", lambda self, **kw: _deny(**kw))

    called: list[str] = []

    async def _should_not_call(**_kwargs: Any) -> ChatCompletionResultV1:
        called.append("chat")
        raise AssertionError("chat_completion must not run after route deny")

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.source_patch_codegen.chat_completion",
        _should_not_call,
    )

    result = await generate_source_patch(
        objective="Create proposed/erp-system/app.js",
        discovery=_empty_discovery(),
        tenant_id="tenant-1",
        actor_id="actor-1",
        authorization_header="Bearer tok",
        model_override="openrouter/openai/gpt-4o-mini",
    )
    assert result.failure_code == "GOVERNED_ROUTE_DENIED"
    assert called == []


@pytest.mark.asyncio
async def test_generate_source_patch_forwards_tenant_actor_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    async def _fake_chat(**kwargs: Any) -> ChatCompletionResultV1:
        captured.append(kwargs)
        return ChatCompletionResultV1(
            output_text=(
                '{"edits":[{"path":"app.js","new_content":"console.log(1);\\n",'
                '"replacements":[]}],"no_files_identified":false,"reason":""}'
            ),
            provider="auto",
            resolved_provider="openrouter",
            model="openrouter/openai/gpt-4o-mini",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.source_patch_codegen.chat_completion",
        _fake_chat,
    )

    discovery = DiscoveryReport(config_files={"app.js": "// empty\n"})
    result = await generate_source_patch(
        objective="Replace app.js with a one-line console.log",
        discovery=discovery,
        tenant_id="5ED5Z0CF2MCPXHRAJWW7SBWZSE",
        actor_id="5298SWBWG37T8WSMMH645P0Y08",
        authorization_header="Bearer caller-jwt",
    )
    assert result.edits
    assert result.edits[0].path == "app.js"
    assert len(captured) >= 1
    call = captured[0]
    assert call["tenant_id"] == "5ED5Z0CF2MCPXHRAJWW7SBWZSE"
    assert call["actor_id"] == "5298SWBWG37T8WSMMH645P0Y08"
    assert call["authorization_header"] == "Bearer caller-jwt"
    assert call["metadata"]["aix_role"] == "executor"
    # Credential ownership: AIX forwards caller auth; Integration owns provider key.
    assert "api_key" not in call
    assert "OPENROUTER" not in str(call.keys())


@pytest.mark.asyncio
async def test_generate_source_patch_propagates_integration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raise(**_kwargs: Any) -> ChatCompletionResultV1:
        raise HyeveIntegrationChatError("UPSTREAM_DENIED", detail="integration missing key")

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.source_patch_codegen.chat_completion",
        _raise,
    )

    result = await generate_source_patch(
        objective="Create proposed/erp-system/index.html",
        discovery=_empty_discovery(),
        tenant_id="tenant-1",
        actor_id="actor-1",
        authorization_header="Bearer tok",
    )
    assert result.no_files_identified is True
    assert result.failure_code == "INTEGRATION_UPSTREAM_DENIED"
    assert "integration missing key" in (result.reason or "")


@pytest.mark.asyncio
async def test_generate_source_patch_never_imports_httpx_for_llm() -> None:
    """Runtime guard: module must not use httpx for the LLM hop."""
    import backend.aix_core.runtime.executor.source_patch_codegen as mod

    assert not hasattr(mod, "httpx")
    src = inspect.getsource(mod.generate_source_patch)
    assert "httpx" not in src
    assert "_governed_executor_patch_once" in inspect.getsource(mod)
