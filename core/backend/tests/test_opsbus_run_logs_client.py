from __future__ import annotations

import importlib
from typing import Any

import httpx
import pytest

from backend.clients.opsbus_run_logs_client import OpsbusRunLogsClient


@pytest.mark.asyncio
async def test_log_model_usage_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Resp:
        status_code = 201

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"success": True}

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Resp()

    module = importlib.import_module("backend.clients.opsbus_run_logs_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = OpsbusRunLogsClient(base_url="http://opsbus.local", timeout_seconds=5, service_token="token")
    ok, err = await client.log_model_usage(
        tenant_id="default",
        project_id="proj-1",
        run_id="run-1",
        correlation_id="corr-1",
        actor_id="user-1",
        usage_block={"tokens": 123},
    )
    assert ok is True
    assert err is None
    assert captured["url"] == "http://opsbus.local/api/v1/runs/run-1/logs"
    assert captured["json"]["project_id"] == "proj-1"
    assert captured["json"]["message"] == "AIX model usage telemetry"
    assert "X-Tenant-Id" not in captured["headers"]
    assert captured["headers"]["X-Project-ID"] == "proj-1"
    assert captured["headers"]["X-Correlation-ID"] == "corr-1"


@pytest.mark.asyncio
async def test_log_model_usage_token_missing_returns_false() -> None:
    client = OpsbusRunLogsClient(base_url="http://opsbus.local", timeout_seconds=5, service_token=None)
    ok, err = await client.log_model_usage(
        tenant_id="default",
        project_id="proj-1",
        run_id="run-1",
        correlation_id="corr-1",
        actor_id="user-1",
        usage_block={"tokens": 123},
    )
    assert ok is False
    assert err == "OPSBUS_API_TOKEN_MISSING"


@pytest.mark.asyncio
async def test_log_model_usage_failure_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        request = httpx.Request("POST", "http://opsbus.local/api/v1/runs/run-1/logs")

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError("server error", request=self.request, response=httpx.Response(500))

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers):
            return _Resp()

    module = importlib.import_module("backend.clients.opsbus_run_logs_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = OpsbusRunLogsClient(base_url="http://opsbus.local", timeout_seconds=5, service_token="token")
    ok, err = await client.log_model_usage(
        tenant_id="default",
        project_id="proj-1",
        run_id="run-1",
        correlation_id="corr-1",
        actor_id="user-1",
        usage_block={"tokens": 123},
    )
    assert ok is False
    assert err is not None


@pytest.mark.asyncio
async def test_log_run_event_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Resp:
        status_code = 201

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Resp()

    module = importlib.import_module("backend.clients.opsbus_run_logs_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = OpsbusRunLogsClient(base_url="http://opsbus.local", timeout_seconds=5, service_token="token")
    ok, err = await client.log_run_event(
        tenant_id="default",
        project_id="proj-1",
        run_id="run-1",
        correlation_id="corr-1",
        event_name="aix.devexecutor.run.closed",
        event_payload={"event_schema_version": "1.0", "tool_sequence": ["read_range", "edit_file"]},
    )
    assert ok is True
    assert err is None
    assert captured["json"]["message"] == "aix.devexecutor.run.closed"
    assert captured["json"]["metadata_json"]["event_name"] == "aix.devexecutor.run.closed"
    assert captured["json"]["metadata_json"]["event_payload"]["event_schema_version"] == "1.0"


@pytest.mark.asyncio
async def test_log_model_usage_fallback_dispatch_then_retry_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict]] = []

    class _Resp:
        def __init__(self, *, status_code: int, payload: dict | None = None, url: str = "http://opsbus.local"):
            self.status_code = status_code
            self._payload = payload or {}
            self.request = httpx.Request("POST", url)

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("error", request=self.request, response=httpx.Response(self.status_code))

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers):
            calls.append(("POST", url, json))
            if url.endswith("/api/v1/runs/run-1/logs"):
                return _Resp(status_code=404, url=url)
            if url.endswith("/api/v1/dispatch"):
                return _Resp(status_code=201, payload={"success": True, "data": {"id": "shadow-run-1"}}, url=url)
            if url.endswith("/api/v1/runs/shadow-run-1/logs"):
                return _Resp(status_code=201, payload={"success": True}, url=url)
            return _Resp(status_code=500, url=url)

    module = importlib.import_module("backend.clients.opsbus_run_logs_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = OpsbusRunLogsClient(base_url="http://opsbus.local", timeout_seconds=5, service_token="token")
    ok, err = await client.log_model_usage(
        tenant_id="default",
        project_id="proj-1",
        run_id="run-1",
        correlation_id="corr-1",
        actor_id="user-1",
        usage_block={"tokens": 123},
    )
    assert ok is True
    assert err is None
    assert len(calls) == 3
    assert calls[0][1].endswith("/api/v1/runs/run-1/logs")
    assert calls[1][1].endswith("/api/v1/dispatch")
    assert calls[2][1].endswith("/api/v1/runs/shadow-run-1/logs")


@pytest.mark.asyncio
async def test_list_run_logs_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "success": True,
                "data": {
                    "run_id": "run-1",
                    "items": [{"message": "aix.devexecutor.run.closed", "metadata_json": {"event_payload": {}}}],
                    "pagination": {"limit": 100, "offset": 0, "total": 1},
                },
            }

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params, headers):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return _Resp()

    module = importlib.import_module("backend.clients.opsbus_run_logs_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = OpsbusRunLogsClient(base_url="http://opsbus.local", timeout_seconds=5, service_token="token")
    payload, err = await client.list_run_logs(
        tenant_id="default",
        project_id="proj-1",
        run_id="run-1",
        correlation_id="corr-1",
    )
    assert err is None
    assert payload["run_id"] == "run-1"
    assert captured["url"] == "http://opsbus.local/api/v1/runs/run-1/logs"
    assert captured["params"]["project_id"] == "proj-1"
    assert captured["headers"]["X-Project-ID"] == "proj-1"


@pytest.mark.asyncio
async def test_list_run_logs_token_missing_returns_error() -> None:
    client = OpsbusRunLogsClient(base_url="http://opsbus.local", timeout_seconds=5, service_token=None)
    payload, err = await client.list_run_logs(
        tenant_id="default",
        project_id="proj-1",
        run_id="run-1",
        correlation_id="corr-1",
    )
    assert payload == {}
    assert err == "OPSBUS_API_TOKEN_MISSING"
