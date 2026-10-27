from __future__ import annotations

import importlib
from typing import Any, cast

import httpx
import pytest

from backend.clients.workhub_knowledge_client import WorkhubKnowledgeClient


@pytest.mark.asyncio
async def test_update_knowledge_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def patch(self, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Resp()

    module = importlib.import_module("backend.clients.workhub_knowledge_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = WorkhubKnowledgeClient(base_url="http://workhub.local", timeout_seconds=5, service_token="token")
    ok, err = await client.update_knowledge(
        tenant_id="default",
        project_id="proj-1",
        knowledge_id="knowledge-1",
        correlation_id="corr-1",
        actor_id="user-1",
        title="Weekly report",
        content="draft content",
        kind="runbook",
        tags=["aix"],
        uri="wh://aix/weekly",
        metadata_json={"knowledge": {"status": "draft"}},
    )
    assert ok is True
    assert err is None
    payload = cast(dict[str, Any], captured["json"])
    assert captured["url"] == "http://workhub.local/api/v1/projects/proj-1/knowledge/knowledge-1"
    assert payload["project_id"] == "proj-1"
    assert payload["title"] == "Weekly report"


@pytest.mark.asyncio
async def test_update_knowledge_requires_id() -> None:
    client = WorkhubKnowledgeClient(base_url="http://workhub.local", timeout_seconds=5, service_token="token")
    ok, err = await client.update_knowledge(
        tenant_id="default",
        project_id="proj-1",
        knowledge_id="",
        correlation_id="corr-1",
        actor_id="user-1",
    )
    assert ok is False
    assert err == "WORKHUB_KNOWLEDGE_ID_MISSING"


@pytest.mark.asyncio
async def test_update_knowledge_failure_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        request = httpx.Request("PATCH", "http://workhub.local/api/v1/projects/proj-1/knowledge/knowledge-1")

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError("server error", request=self.request, response=httpx.Response(500))

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def patch(self, url, json, headers):
            return _Resp()

    module = importlib.import_module("backend.clients.workhub_knowledge_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = WorkhubKnowledgeClient(base_url="http://workhub.local", timeout_seconds=5, service_token="token")
    ok, err = await client.update_knowledge(
        tenant_id="default",
        project_id="proj-1",
        knowledge_id="knowledge-1",
        correlation_id="corr-1",
        actor_id="user-1",
        content="draft content",
    )
    assert ok is False
    assert err is not None
