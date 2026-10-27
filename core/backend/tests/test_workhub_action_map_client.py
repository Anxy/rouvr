from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from backend.clients.workhub_action_map_client import WorkhubActionMapClient


@pytest.mark.asyncio
async def test_get_active_action_map_returns_highest_version(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "data": {
                    "items": [
                        {"map_name": "default_dispatch", "map_version": 1},
                        {"map_name": "default_dispatch", "map_version": 3},
                        {"map_name": "default_dispatch", "map_version": 2},
                    ]
                }
            }

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            return _Resp()

    module = importlib.import_module("backend.clients.workhub_action_map_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)
    client = WorkhubActionMapClient(base_url="http://workhub.local", timeout_seconds=5, service_token="token")
    item, err = await client.get_active_action_map(
        tenant_id="default",
        project_id="proj-1",
        map_name="default_dispatch",
        map_version=None,
    )
    assert err is None
    assert item is not None
    assert int(item["map_version"]) == 3


@pytest.mark.asyncio
async def test_get_active_action_map_version_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"data": {"items": [{"map_name": "default_dispatch", "map_version": 2}]}}

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            return _Resp()

    module = importlib.import_module("backend.clients.workhub_action_map_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)
    client = WorkhubActionMapClient(base_url="http://workhub.local", timeout_seconds=5, service_token="token")
    item, err = await client.get_active_action_map(
        tenant_id="default",
        project_id="proj-1",
        map_name="default_dispatch",
        map_version=9,
    )
    assert item is None
    assert err == "ACTION_MAP_VERSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_proposed_action_map_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"data": {"id": "am-2", "map_name": "default_dispatch", "map_version": 2, "status": "proposed"}}

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, *args, **kwargs):
            return _Resp()

    module = importlib.import_module("backend.clients.workhub_action_map_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)
    client = WorkhubActionMapClient(base_url="http://workhub.local", timeout_seconds=5, service_token="token")
    item, err = await client.create_proposed_action_map(
        tenant_id="default",
        project_id="proj-1",
        correlation_id="corr-1",
        map_name="default_dispatch",
        definition_json={"capabilities": {}},
        metadata_json={"kind": "drift"},
    )
    assert err is None
    assert item is not None
    assert item["status"] == "proposed"


@pytest.mark.asyncio
async def test_create_proposed_action_map_requires_token() -> None:
    client = WorkhubActionMapClient(base_url="http://workhub.local", timeout_seconds=5, service_token=None)
    item, err = await client.create_proposed_action_map(
        tenant_id="default",
        project_id="proj-1",
        correlation_id="corr-1",
        map_name="default_dispatch",
        definition_json={},
        metadata_json=None,
    )
    assert item is None
    assert err == "WORKHUB_API_TOKEN_MISSING"


@pytest.mark.asyncio
async def test_worker_query_cache_hit_and_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = SimpleNamespace(count=0)

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"data": {"items": [{"map_name": "default_dispatch", "map_version": 3, "status": "active"}]}}

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            calls.count += 1
            return _Resp()

    module = importlib.import_module("backend.clients.workhub_action_map_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)
    client = WorkhubActionMapClient(base_url="http://workhub.local", timeout_seconds=5, service_token="token")
    first, err1, trace1 = await client.get_action_map_for_worker(
        tenant_id="default",
        project_id="proj-1",
        map_name="default_dispatch",
        pinned_version=None,
    )
    second, err2, trace2 = await client.get_action_map_for_worker(
        tenant_id="default",
        project_id="proj-1",
        map_name="default_dispatch",
        pinned_version=None,
    )
    assert err1 is None and err2 is None
    assert first is not None and second is not None
    assert trace1["cache_hit"] is False
    assert trace2["cache_hit"] is True
    assert calls.count == 1


@pytest.mark.asyncio
async def test_worker_query_pinned_version_uses_explicit_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "data": {
                    "items": [
                        {"map_name": "default_dispatch", "map_version": 2, "status": "active"},
                        {"map_name": "default_dispatch", "map_version": 3, "status": "active"},
                    ]
                }
            }

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            return _Resp()

    module = importlib.import_module("backend.clients.workhub_action_map_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)
    client = WorkhubActionMapClient(base_url="http://workhub.local", timeout_seconds=5, service_token="token")
    item, err, trace = await client.get_action_map_for_worker(
        tenant_id="default",
        project_id="proj-1",
        map_name="default_dispatch",
        pinned_version=2,
    )
    assert err is None
    assert item is not None
    assert int(item["map_version"]) == 2
    assert trace["pinned_version"] == 2


@pytest.mark.asyncio
async def test_worker_query_tenant_safe_cache_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = SimpleNamespace(count=0)

    class _Resp:
        def __init__(self, tenant: str):
            self._tenant = tenant

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"data": {"items": [{"map_name": "default_dispatch", "map_version": 1, "tenant": self._tenant}]}}

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            calls.count += 1
            tenant = kwargs.get("headers", {}).get("X-Tenant-ID", "default")
            return _Resp(tenant)

    module = importlib.import_module("backend.clients.workhub_action_map_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)
    client = WorkhubActionMapClient(base_url="http://workhub.local", timeout_seconds=5, service_token="token")
    item_a, err_a, _ = await client.get_action_map_for_worker(
        tenant_id="tenant-a",
        project_id="proj-1",
        map_name="default_dispatch",
        pinned_version=None,
    )
    item_b, err_b, _ = await client.get_action_map_for_worker(
        tenant_id="tenant-b",
        project_id="proj-1",
        map_name="default_dispatch",
        pinned_version=None,
    )
    assert err_a is None and err_b is None
    assert item_a is not None and item_b is not None
    assert item_a["tenant"] == "tenant-a"
    assert item_b["tenant"] == "tenant-b"
    assert calls.count == 2
