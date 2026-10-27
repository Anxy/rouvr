"""Tests for hyeve_integration_client.chat_completion -- the real HTTP client that
provider_routing.py's policy layer was missing (that module only ever decided
ALLOW/DENY, never made a call)."""

from __future__ import annotations

import pytest

from backend.infrastructure.model_gateway.hyeve_integration_client import (
    ChatMessage,
    HyeveIntegrationChatError,
    chat_completion,
)


class _FakeResponse:
    def __init__(self, *, status_code: int, json_body: dict, text: str = "") -> None:
        self.status_code = status_code
        self._json_body = json_body
        self.text = text or str(json_body)

    def json(self) -> dict:
        return self._json_body


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse, *, captured: list[dict]) -> None:
        self._response = response
        self._captured = captured

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def post(self, url: str, *, headers: dict, json: dict) -> _FakeResponse:
        self._captured.append({"url": url, "headers": headers, "json": json})
        return self._response


def _messages() -> list[ChatMessage]:
    return [ChatMessage(role="system", content="You are AIX intake."), ChatMessage(role="user", content="Fix docs")]


class TestChatCompletionSuccess:
    @pytest.mark.asyncio
    async def test_successful_completion_returns_output_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[dict] = []
        response = _FakeResponse(
            status_code=200,
            json_body={
                "success": True,
                "data": {
                    "status": "completed",
                    "provider": "auto",
                    "resolved_provider": "openrouter",
                    "model": "some/model",
                    "job_id": "job-1",
                    "output_text": '{"reply": "hi", "task_class": "docs_only_change"}',
                    "usage": {"total_tokens": 42},
                },
            },
        )
        monkeypatch.setattr(
            "backend.infrastructure.model_gateway.hyeve_integration_client.httpx.AsyncClient",
            lambda **_kwargs: _FakeAsyncClient(response, captured=captured),
        )

        result = await chat_completion(
            messages=_messages(),
            tenant_id="tenant-1",
            actor_id="user-1",
            authorization_header="Bearer real-token",
        )

        assert result.output_text == '{"reply": "hi", "task_class": "docs_only_change"}'
        assert result.resolved_provider == "openrouter"
        assert result.usage == {"total_tokens": 42}
        assert captured[0]["headers"]["Authorization"] == "Bearer real-token"
        assert captured[0]["headers"]["X-Tenant-ID"] == "tenant-1"
        assert captured[0]["json"]["provider"] == "auto"

    @pytest.mark.asyncio
    async def test_response_format_forwarded_when_provided(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[dict] = []
        response = _FakeResponse(
            status_code=200,
            json_body={
                "success": True,
                "data": {"status": "completed", "provider": "auto", "resolved_provider": "openrouter", "model": "m", "job_id": "j", "output_text": "{}"},
            },
        )
        monkeypatch.setattr(
            "backend.infrastructure.model_gateway.hyeve_integration_client.httpx.AsyncClient",
            lambda **_kwargs: _FakeAsyncClient(response, captured=captured),
        )

        await chat_completion(
            messages=_messages(),
            tenant_id="tenant-1",
            actor_id="user-1",
            authorization_header="Bearer real-token",
            response_format={"type": "json_object"},
        )

        assert captured[0]["json"]["response_format"] == {"type": "json_object"}


class TestChatCompletionFailClosed:
    @pytest.mark.asyncio
    async def test_missing_authorization_rejected_before_any_request(self) -> None:
        with pytest.raises(HyeveIntegrationChatError) as exc_info:
            await chat_completion(
                messages=_messages(),
                tenant_id="tenant-1",
                actor_id="user-1",
                authorization_header="",
            )
        assert exc_info.value.code == "AUTHORIZATION_REQUIRED"

    @pytest.mark.asyncio
    async def test_empty_messages_rejected(self) -> None:
        with pytest.raises(HyeveIntegrationChatError) as exc_info:
            await chat_completion(
                messages=[],
                tenant_id="tenant-1",
                actor_id="user-1",
                authorization_header="Bearer x",
            )
        assert exc_info.value.code == "MESSAGES_REQUIRED"

    @pytest.mark.asyncio
    async def test_non_200_status_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[dict] = []
        response = _FakeResponse(status_code=502, json_body={}, text="bad gateway")
        monkeypatch.setattr(
            "backend.infrastructure.model_gateway.hyeve_integration_client.httpx.AsyncClient",
            lambda **_kwargs: _FakeAsyncClient(response, captured=captured),
        )
        with pytest.raises(HyeveIntegrationChatError) as exc_info:
            await chat_completion(
                messages=_messages(),
                tenant_id="tenant-1",
                actor_id="user-1",
                authorization_header="Bearer x",
            )
        assert exc_info.value.code == "INTEGRATION_CHAT_REJECTED"

    @pytest.mark.asyncio
    async def test_provider_reported_failure_raises_not_a_synthetic_reply(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[dict] = []
        response = _FakeResponse(
            status_code=200,
            json_body={
                "success": False,
                "data": {"status": "failed", "provider": "auto", "resolved_provider": "openrouter", "model": "m", "job_id": "j", "error": "rate_limited"},
            },
        )
        monkeypatch.setattr(
            "backend.infrastructure.model_gateway.hyeve_integration_client.httpx.AsyncClient",
            lambda **_kwargs: _FakeAsyncClient(response, captured=captured),
        )
        with pytest.raises(HyeveIntegrationChatError) as exc_info:
            await chat_completion(
                messages=_messages(),
                tenant_id="tenant-1",
                actor_id="user-1",
                authorization_header="Bearer x",
            )
        assert exc_info.value.code in {"INTEGRATION_CHAT_FAILED"}

    @pytest.mark.asyncio
    async def test_transport_error_raises_integration_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        class _RaisingClient:
            async def __aenter__(self) -> "_RaisingClient":
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

            async def post(self, *_args: object, **_kwargs: object) -> None:
                raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(
            "backend.infrastructure.model_gateway.hyeve_integration_client.httpx.AsyncClient",
            lambda **_kwargs: _RaisingClient(),
        )
        with pytest.raises(HyeveIntegrationChatError) as exc_info:
            await chat_completion(
                messages=_messages(),
                tenant_id="tenant-1",
                actor_id="user-1",
                authorization_header="Bearer x",
            )
        assert exc_info.value.code == "INTEGRATION_UNAVAILABLE"
