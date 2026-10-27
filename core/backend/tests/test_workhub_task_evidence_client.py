from __future__ import annotations

import importlib
from typing import Any, cast

import httpx
import pytest

from backend.clients.workhub_task_evidence_client import WorkhubTaskEvidenceClient


@pytest.mark.asyncio
async def test_add_model_usage_evidence_success(monkeypatch: pytest.MonkeyPatch) -> None:
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

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Resp()

    module = importlib.import_module("backend.clients.workhub_task_evidence_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = WorkhubTaskEvidenceClient(base_url="http://workhub.local", timeout_seconds=5, service_token="token")
    ok, err = await client.add_model_usage_evidence(
        tenant_id="default",
        project_id="proj-1",
        task_id="task-1",
        correlation_id="corr-1",
        actor_id="user-1",
        usage_block={"tokens": 123},
    )
    assert ok is True
    assert err is None
    headers = cast(dict[str, str], captured["headers"])
    payload = cast(dict[str, Any], captured["json"])
    assert captured["url"] == "http://workhub.local/api/v1/tasks/task-1/evidence"
    assert headers["X-Project-ID"] == "proj-1"
    assert headers["X-Correlation-ID"] == "corr-1"
    assert "X-Tenant-Id" not in headers
    assert payload["project_id"] == "proj-1"


@pytest.mark.asyncio
async def test_add_model_usage_evidence_token_missing_returns_false() -> None:
    client = WorkhubTaskEvidenceClient(base_url="http://workhub.local", timeout_seconds=5, service_token=None)
    ok, err = await client.add_model_usage_evidence(
        tenant_id="default",
        project_id="proj-1",
        task_id="task-1",
        correlation_id="corr-1",
        actor_id="user-1",
        usage_block={"tokens": 123},
    )
    assert ok is False
    assert err == "WORKHUB_API_TOKEN_MISSING"


@pytest.mark.asyncio
async def test_add_model_usage_evidence_prefers_service_token_over_override_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

        async def post(self, url, json, headers):
            captured["headers"] = headers
            return _Resp()

    module = importlib.import_module("backend.clients.workhub_task_evidence_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = WorkhubTaskEvidenceClient(
        base_url="http://workhub.local", timeout_seconds=5, service_token="service-token"
    )
    ok, err = await client.add_model_usage_evidence(
        tenant_id="default",
        project_id="proj-1",
        task_id="task-1",
        correlation_id="corr-1",
        actor_id="user-1",
        usage_block={"tokens": 123},
        service_token_override="fresh-token",
    )
    assert ok is True
    assert err is None
    headers = cast(dict[str, str], captured["headers"])
    assert headers["Authorization"] == "Bearer service-token"


@pytest.mark.asyncio
async def test_add_model_usage_evidence_rejects_override_when_service_token_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

        async def post(self, url, json, headers):
            captured["headers"] = headers
            return _Resp()

    module = importlib.import_module("backend.clients.workhub_task_evidence_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = WorkhubTaskEvidenceClient(base_url="http://workhub.local", timeout_seconds=5, service_token=None)
    ok, err = await client.add_model_usage_evidence(
        tenant_id="default",
        project_id="proj-1",
        task_id="task-1",
        correlation_id="corr-1",
        actor_id="user-1",
        usage_block={"tokens": 123},
        service_token_override="fresh-token",
    )
    assert ok is False
    assert err == "WORKHUB_API_TOKEN_MISSING"
    assert "headers" not in captured


@pytest.mark.asyncio
async def test_add_model_usage_evidence_failure_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        request = httpx.Request("POST", "http://workhub.local/api/v1/tasks/task-1/evidence")

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

    module = importlib.import_module("backend.clients.workhub_task_evidence_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = WorkhubTaskEvidenceClient(base_url="http://workhub.local", timeout_seconds=5, service_token="token")
    ok, err = await client.add_model_usage_evidence(
        tenant_id="default",
        project_id="proj-1",
        task_id="task-1",
        correlation_id="corr-1",
        actor_id="user-1",
        usage_block={"tokens": 123},
    )
    assert ok is False
    assert err is not None


@pytest.mark.asyncio
async def test_add_model_routing_decision_evidence_success(monkeypatch: pytest.MonkeyPatch) -> None:
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

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Resp()

    module = importlib.import_module("backend.clients.workhub_task_evidence_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = WorkhubTaskEvidenceClient(base_url="http://workhub.local", timeout_seconds=5, service_token="token")
    ok, err = await client.add_model_routing_decision_evidence(
        tenant_id="default",
        project_id="proj-1",
        task_id="task-1",
        correlation_id="corr-1",
        actor_id="user-1",
        decision_payload={"correlation_id": "corr-1"},
    )
    assert ok is True
    assert err is None
    payload = cast(dict[str, Any], captured["json"])
    evidence_json = cast(dict[str, Any], payload["evidence_json"])
    assert payload["evidence_type"] == "model_routing_decision"
    assert evidence_json["correlation_id"] == "corr-1"
    assert evidence_json["selected_model"] == "unknown"
    assert isinstance(evidence_json["thresholds"], dict)
    assert isinstance(evidence_json["escalation"], dict)


@pytest.mark.asyncio
async def test_add_redaction_report_evidence_success(monkeypatch: pytest.MonkeyPatch) -> None:
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

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Resp()

    module = importlib.import_module("backend.clients.workhub_task_evidence_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = WorkhubTaskEvidenceClient(base_url="http://workhub.local", timeout_seconds=5, service_token="token")
    ok, err = await client.add_redaction_report_evidence(
        tenant_id="default",
        project_id="proj-1",
        task_id="task-1",
        correlation_id="corr-1",
        actor_id="user-1",
        report_payload={"redaction_applied": True, "masked_count": 2},
    )
    assert ok is True
    assert err is None
    payload = cast(dict[str, Any], captured["json"])
    assert payload["evidence_type"] == "redaction_report"


@pytest.mark.asyncio
async def test_add_codeintel_provenance_evidence_success(monkeypatch: pytest.MonkeyPatch) -> None:
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

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Resp()

    module = importlib.import_module("backend.clients.workhub_task_evidence_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = WorkhubTaskEvidenceClient(base_url="http://workhub.local", timeout_seconds=5, service_token="token")
    ok, err = await client.add_codeintel_provenance_evidence(
        tenant_id="default",
        project_id="proj-1",
        task_id="task-1",
        correlation_id="corr-1",
        actor_id="user-1",
        provenance_payload={
            "source": "hyeve-codeintel-service",
            "retrieval_mode": "deterministic_context_pack",
            "query_identity": {"entity_query": "Example.run", "query_fingerprint": "fp-1"},
            "primary_repo_id": "repo-1",
            "confidence_score": 0.92,
            "citations": [
                {
                    "repo_id": "repo-1",
                    "file_path": "backend/example.py",
                    "start_line": 10,
                    "end_line": 22,
                    "snapshot_id": "snap-1",
                    "commit_sha": "commit-1",
                    "confidence": 0.92,
                }
            ],
        },
    )
    assert ok is True
    assert err is None
    payload = cast(dict[str, Any], captured["json"])
    evidence_json = cast(dict[str, Any], payload["evidence_json"])
    assert payload["evidence_type"] == "codeintel_provenance"
    assert evidence_json["codeintel"]["query_identity"]["query_fingerprint"] == "fp-1"
    assert evidence_json["codeintel"]["citations_count"] == 1
    assert evidence_json["codeintel"]["citations"][0]["file_path"] == "backend/example.py"


@pytest.mark.asyncio
async def test_add_codeintel_provenance_evidence_preserves_empty_bundle_shape(monkeypatch: pytest.MonkeyPatch) -> None:
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

        async def post(self, url, json, headers):
            captured["json"] = json
            return _Resp()

    module = importlib.import_module("backend.clients.workhub_task_evidence_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = WorkhubTaskEvidenceClient(base_url="http://workhub.local", timeout_seconds=5, service_token="token")
    ok, err = await client.add_codeintel_provenance_evidence(
        tenant_id="default",
        project_id="proj-1",
        task_id="task-1",
        correlation_id="corr-1",
        actor_id="user-1",
        provenance_payload={},
    )
    assert ok is True
    assert err is None
    payload = cast(dict[str, Any], captured["json"])
    evidence_json = cast(dict[str, Any], payload["evidence_json"])
    assert evidence_json["codeintel"]["query_identity"] == {
        "entity_query": None,
        "query_fingerprint": None,
    }
    assert evidence_json["codeintel"]["citations"] == []
    assert evidence_json["codeintel"]["citations_count"] == 0
