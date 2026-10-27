from __future__ import annotations

import importlib

import httpx
import pytest

from backend.clients.ayrpack_client import AyrpackClient


@pytest.mark.asyncio
async def test_submit_packet_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"success": True, "data": {"packet_id": "pkt-1"}}

    class _Client:
        def __init__(self, *args, **kwargs) -> None:  # noqa: D401
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, headers, params=None, json=None):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            captured["params"] = params
            captured["json"] = json
            return _Resp()

    module = importlib.import_module("backend.clients.ayrpack_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = AyrpackClient(base_url="http://ayrpack.local", timeout_seconds=5, service_token="token")
    data, err = await client.submit_packet(
        tenant_id="default",
        project_id="proj-1",
        correlation_id="corr-1",
        packet_payload={"packet_id": "pkt-1"},
    )
    assert err is None
    assert data is not None
    assert captured["url"] == "http://ayrpack.local/v1/packets"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    typed_headers = headers
    assert typed_headers["X-Tenant-ID"] == "default"
    assert typed_headers["X-Project-ID"] == "proj-1"
    assert typed_headers["X-Correlation-ID"] == "corr-1"


@pytest.mark.asyncio
async def test_token_missing_returns_error() -> None:
    client = AyrpackClient(base_url="http://ayrpack.local", timeout_seconds=5, service_token=None)
    data, err = await client.get_packet(
        tenant_id="default",
        project_id="proj-1",
        correlation_id="corr-1",
        packet_id="pkt-1",
    )
    assert data is None
    assert err == "AYRPACK_API_TOKEN_MISSING"


@pytest.mark.asyncio
async def test_retry_on_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"count": 0}

    class _RespFail:
        request = httpx.Request("GET", "http://ayrpack.local/v1/packets/pkt-1")

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError("server error", request=self.request, response=httpx.Response(500))

        def json(self) -> dict[str, object]:
            return {}

    class _RespOk:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"success": True, "data": {"packet_id": "pkt-1"}}

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, headers, params=None, json=None):  # noqa: ARG002
            attempts["count"] += 1
            if attempts["count"] == 1:
                return _RespFail()
            return _RespOk()

    module = importlib.import_module("backend.clients.ayrpack_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(module.asyncio, "sleep", lambda *_args, **_kwargs: _noop())

    client = AyrpackClient(
        base_url="http://ayrpack.local",
        timeout_seconds=5,
        service_token="token",
        max_retries=2,
        retry_backoff_seconds=0.0,
    )
    data, err = await client.get_packet(
        tenant_id="default",
        project_id="proj-1",
        correlation_id="corr-1",
        packet_id="pkt-1",
    )
    assert err is None
    assert data is not None
    assert attempts["count"] == 2


async def _noop():
    return None
