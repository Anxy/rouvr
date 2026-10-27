from __future__ import annotations

import importlib

import httpx
import pytest


@pytest.mark.asyncio
async def test_add_task_communication_posts_typed_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, json: dict, headers: dict):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            request = httpx.Request("POST", url)
            return httpx.Response(201, request=request, json={"success": True})

    module = importlib.import_module("backend.clients.workhub_task_communication_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeAsyncClient)
    client = module.WorkhubTaskCommunicationClient(
        base_url="http://workhub.local",
        timeout_seconds=9,
        service_token="token-123",
    )

    ok, err, data = await client.add_task_communication(
        tenant_id="default",
        project_id="proj-1",
        task_number=17,
        correlation_id="corr-17",
        actor_id="agent-1",
        body="Need planner clarification on grounded symbol scope",
        communication_channel="coordination",
        communication_type="guidance_request",
        communication_thread_key="thread-17",
        communication_summary="planner clarification needed",
        communication_refs=["workhub://tasks/17"],
        metadata_json={"source": "plannerai"},
    )

    assert ok is True
    assert err is None
    assert data is None
    assert captured["url"] == "http://workhub.local/api/v1/projects/proj-1/tasks/by-number/17/communication"
    assert captured["json"] == {
        "project_id": "proj-1",
        "body": "Need planner clarification on grounded symbol scope",
        "metadata_json": {"source": "plannerai"},
        "communication_channel": "coordination",
        "communication_type": "guidance_request",
        "communication_thread_key": "thread-17",
        "communication_in_reply_to": None,
        "communication_summary": "planner clarification needed",
        "communication_refs": ["workhub://tasks/17"],
    }


@pytest.mark.asyncio
async def test_add_task_communication_requires_token() -> None:
    module = importlib.import_module("backend.clients.workhub_task_communication_client")
    client = module.WorkhubTaskCommunicationClient(
        base_url="http://workhub.local",
        timeout_seconds=9,
        service_token=None,
    )

    ok, err, data = await client.add_task_communication(
        tenant_id="default",
        project_id="proj-1",
        task_number=17,
        correlation_id="corr-17",
        actor_id="agent-1",
        body="note",
        communication_channel="communication",
        communication_type="status_note",
    )

    assert ok is False
    assert err == "WORKHUB_API_TOKEN_MISSING"
    assert data is None


@pytest.mark.asyncio
async def test_list_task_coordination_returns_actionable_items(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, params: dict, headers: dict):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            request = httpx.Request("GET", url)
            return httpx.Response(
                200,
                request=request,
                json={
                    "success": True,
                    "data": {
                        "items": [
                            {
                                "id": "comment-1",
                                "body": "Need exact target file",
                                "coordination": {
                                    "schema_version": "task.coordination.projection.current",
                                    "actionable": True,
                                    "coordination_action": "request_guidance",
                                    "thread_key": "assist-loop:17",
                                    "summary": "Need exact target file",
                                    "message_type": "guidance_request",
                                    "refs": ["workhub://tasks/17/assist_loop"],
                                },
                                "communication": {"channel": "coordination", "message_type": "guidance_request"},
                                "created_at": "2026-03-27T10:00:00+00:00",
                            },
                            {
                                "id": "comment-2",
                                "body": "chat only",
                                "coordination": {
                                    "schema_version": "task.coordination.projection.current",
                                    "actionable": False,
                                    "coordination_action": "informational",
                                    "thread_key": "assist-loop:17",
                                    "summary": "chat only",
                                    "message_type": "status_note",
                                    "refs": [],
                                },
                            },
                        ]
                    },
                },
            )

    module = importlib.import_module("backend.clients.workhub_task_communication_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeAsyncClient)
    client = module.WorkhubTaskCommunicationClient(
        base_url="http://workhub.local",
        timeout_seconds=9,
        service_token="token-123",
    )

    items, err = await client.list_task_coordination(
        tenant_id="default",
        project_id="proj-1",
        task_number=17,
        correlation_id="corr-17",
        actor_id="agent-1",
    )

    assert err is None
    assert captured["url"] == "http://workhub.local/api/v1/projects/proj-1/tasks/communication"
    assert captured["params"] == {
        "task_number": 17,
        "communication_channel": "coordination",
        "communication_correlation_id": None,
        "communication_thread_key": None,
        "limit": 20,
        "offset": 0,
    }
    assert len(items) == 1
    assert items[0]["coordination"]["coordination_action"] == "request_guidance"


@pytest.mark.asyncio
async def test_list_task_communication_returns_typed_items(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, params: dict, headers: dict):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            request = httpx.Request("GET", url)
            return httpx.Response(
                200,
                request=request,
                json={
                    "success": True,
                    "data": {
                        "items": [
                            {
                                "id": "comment-9",
                                "body": "Planner asked for a narrower file target",
                                "communication": {
                                    "channel": "communication",
                                    "message_type": "status_note",
                                    "thread_key": "planner:2190",
                                },
                                "communication_publication": {
                                    "attempted": True,
                                    "published": False,
                                    "stream_ref": "workhub://projects/proj-1/tasks/17/communication/stream",
                                    "error": "redis unavailable",
                                },
                                "created_at": "2026-03-27T10:05:00+00:00",
                            },
                            {
                                "id": "comment-10",
                                "body": "ignore me",
                                "created_at": "2026-03-27T10:06:00+00:00",
                            },
                        ]
                    },
                },
            )

    module = importlib.import_module("backend.clients.workhub_task_communication_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeAsyncClient)
    client = module.WorkhubTaskCommunicationClient(
        base_url="http://workhub.local",
        timeout_seconds=9,
        service_token="token-123",
    )

    items, err = await client.list_task_communication(
        tenant_id="default",
        project_id="proj-1",
        task_number=17,
        correlation_id="corr-17",
        actor_id="agent-1",
        communication_correlation_id="corr-filter-17",
        communication_thread_key="planner:2190",
    )

    assert err is None
    assert captured["url"] == "http://workhub.local/api/v1/projects/proj-1/tasks/communication"
    assert captured["params"] == {
        "task_number": 17,
        "communication_channel": None,
        "communication_correlation_id": "corr-filter-17",
        "communication_thread_key": "planner:2190",
        "limit": 20,
        "offset": 0,
    }
    assert items == [
        {
            "id": "comment-9",
            "body": "Planner asked for a narrower file target",
            "communication": {
                "channel": "communication",
                "message_type": "status_note",
                "thread_key": "planner:2190",
            },
            "coordination": None,
            "communication_publication": {
                "attempted": True,
                "published": False,
                "stream_ref": "workhub://projects/proj-1/tasks/17/communication/stream",
                "error": "redis unavailable",
            },
            "created_at": "2026-03-27T10:05:00+00:00",
        }
    ]
