from __future__ import annotations

import importlib
from typing import cast

import httpx
import pytest

from backend.clients.codeintel_query_client import CodeintelQueryClient, _codeintel_transport_headers


def test_codeintel_transport_headers_mark_remote_http_as_forwarded_https() -> None:
    assert _codeintel_transport_headers("http://82.112.234.42:8331") == {"X-Forwarded-Proto": "https"}


def test_codeintel_transport_headers_leave_local_http_unmarked() -> None:
    assert _codeintel_transport_headers("http://127.0.0.1:8331") == {}


@pytest.mark.asyncio
async def test_get_context_calls_index_jobs_with_project_and_secure_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_calls: list[dict[str, object]] = []

    class _Resp:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params, headers):
            captured_calls.append({"url": url, "params": params, "headers": headers})
            if url.endswith("/api/v1/index-jobs"):
                return _Resp({"success": True, "data": [], "pagination": {"total": 0}})
            if url.endswith("/api/v1/repos"):
                return _Resp({"success": True, "data": [], "pagination": {"total": 0}})
            return _Resp({"success": True, "data": [], "pagination": {"total": 0}})

    module = importlib.import_module("backend.clients.codeintel_query_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = CodeintelQueryClient(base_url="http://82.112.234.42:8331/", timeout_seconds=5, service_token="token")
    payload, err = await client.get_context(
        tenant_id="default",
        project_id="bh-migrated-7-aix",
        entity_query=None,
        limit=6,
    )

    assert err is None
    assert payload["index_jobs_total"] == 0
    assert captured_calls[0]["url"] == "http://82.112.234.42:8331/api/v1/index-jobs"
    assert captured_calls[0]["params"] == {"limit": 6, "offset": 0}
    headers = cast(dict[str, object], captured_calls[0]["headers"])
    assert headers["X-Project-ID"] == "bh-migrated-7-aix"
    assert headers["X-Forwarded-Proto"] == "https"
    assert headers["Authorization"] == "Bearer token"


@pytest.mark.asyncio
async def test_get_context_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_calls: list[dict[str, object]] = []

    class _Resp:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params, headers):
            captured_calls.append({"url": url, "params": params, "headers": headers})
            if url.endswith("/api/v1/index-jobs"):
                return _Resp({"success": True, "data": [{"id": "job-1"}], "pagination": {"total": 1}})
            if url.endswith("/api/v1/repos"):
                return _Resp({"success": True, "data": [{"id": "repo-1"}], "pagination": {"total": 1}})
            if url.endswith("/api/v1/answers/context-pack"):
                return _Resp(
                    {
                        "success": True,
                        "data": {
                            "query": "code_change",
                            "repo_id": "repo-1",
                            "snapshot_id": "snap-1",
                            "commit_hash": "commit-1",
                            "primary_entity": {
                                "entity_id": "ent-1",
                                "repo_id": "repo-1",
                                "file_path": "src/app.py",
                                "entity_type": "function",
                                "name": "code_change",
                                "qualified_name": "pkg.code_change",
                                "signature": "code_change()",
                                "start_line": 42,
                                "end_line": 52,
                                "confidence_score": 0.95,
                                "freshness_status": "fresh",
                                "warnings": [],
                                "why_ranked": ["exact_symbol_match"],
                            },
                            "matched_entities": [
                                {
                                    "entity_id": "ent-1",
                                    "repo_id": "repo-1",
                                    "file_path": "src/app.py",
                                    "entity_type": "function",
                                    "name": "code_change",
                                    "qualified_name": "pkg.code_change",
                                    "signature": "code_change()",
                                    "start_line": 42,
                                    "end_line": 52,
                                    "confidence_score": 0.95,
                                    "freshness_status": "fresh",
                                    "warnings": [],
                                    "why_ranked": ["exact_symbol_match"],
                                }
                            ],
                            "callers": [
                                {
                                    "entity_id": "caller-1",
                                    "repo_id": "repo-1",
                                    "file_path": "src/runner.py",
                                    "entity_type": "function",
                                    "name": "run_change",
                                    "qualified_name": "pkg.run_change",
                                    "signature": "run_change()",
                                    "start_line": 12,
                                    "end_line": 24,
                                    "confidence_score": 0.83,
                                    "freshness_status": "fresh",
                                    "warnings": [],
                                    "why_ranked": ["caller_relation"],
                                }
                            ],
                            "likely_tests": ["tests/test_app.py"],
                            "adjacent_files": ["src/app.py", "src/runner.py"],
                            "artifacts": [
                                {
                                    "id": "artifact-1",
                                    "tenant_id": "default",
                                    "project_id": "proj-1",
                                    "repo_id": "repo-1",
                                    "snapshot_id": "snap-1",
                                    "commit_hash": "commit-1",
                                    "artifact_kind": "context_pack",
                                    "title": "Context pack: code_change",
                                    "summary": "Deterministic planner/verifier grounding bundle.",
                                    "metadata_json": {"query": "code_change"},
                                    "created_by": "codeintel_context_pack",
                                    "created_at": "2026-03-21T00:00:00+00:00",
                                }
                            ],
                            "citations": [
                                {
                                    "id": "citation-1",
                                    "tenant_id": "default",
                                    "project_id": "proj-1",
                                    "knowledge_artifact_id": "artifact-1",
                                    "query_text": "code_change",
                                    "citation_kind": "entity",
                                    "cited_file_path": "src/app.py",
                                    "cited_entity_id": "ent-1",
                                    "citation_start_line": 42,
                                    "citation_end_line": 52,
                                    "snapshot_id": "snap-1",
                                    "commit_hash": "commit-1",
                                    "evidence_payload": {"confidence_score": 0.95},
                                    "created_by": "codeintel_context_pack",
                                    "created_at": "2026-03-21T00:00:00+00:00",
                                }
                            ],
                        },
                    }
                )
            return _Resp({"success": True, "data": [], "pagination": {"total": 0}})

    module = importlib.import_module("backend.clients.codeintel_query_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = CodeintelQueryClient(base_url="http://codeintel.local", timeout_seconds=5, service_token="token")
    payload, err = await client.get_context(
        tenant_id="default",
        project_id="proj-1",
        entity_query="code_change",
        retrieval_policy={"requested_mode": "codeintel_first", "effective_mode": "codeintel_first"},
    )
    assert err is None
    assert payload["index_jobs_total"] == 1
    assert payload["entity_query"] == "code_change"
    assert payload["source"] == "hyeve-codeintel-service"
    assert payload["index_job_ids"] == ["job-1"]
    assert payload["repo_ids"] == ["repo-1"]
    assert payload["entity_ids"] == ["ent-1"]
    assert payload["retrieval_mode"] == "deterministic_context_pack"
    assert payload["query_identity"]["entity_query"] == "code_change"
    assert payload["query_identity"]["query_fingerprint"]
    assert payload["latency_ms"] >= 0
    assert payload["citations"] == [
        {
            "repo_id": "repo-1",
            "file": "src/app.py",
            "line": 42,
            "snapshot": "snap-1",
            "file_path": "src/app.py",
            "start_line": 42,
            "end_line": 52,
            "snapshot_id": "snap-1",
            "commit_sha": "commit-1",
            "confidence": 0.95,
        }
    ]
    assert payload["confidence_score"] == 0.95
    assert payload["fallback_policy"] is None
    assert payload["primary_repo_id"] == "repo-1"
    assert payload["repo_entities_total"] == 1
    assert payload["repo_relations_total"] == 0
    assert payload["entities"][0]["snapshot_id"] == "snap-1"
    assert payload["entities"][0]["commit_sha"] == "commit-1"
    assert payload["entities"][0]["signature"] == "code_change()"
    assert payload["entities"][0]["start_line"] == 42
    assert payload["entities"][0]["end_line"] == 52
    assert payload["impact"]["root_entity_id"] == "ent-1"
    assert payload["grounding"]["matched_entities"][0]["snapshot_id"] == "snap-1"
    assert payload["grounding"]["matched_entities"][0]["commit_sha"] == "commit-1"
    assert payload["grounding"]["callers"] == [
        {
            "id": "caller-1",
            "qualified_name": "pkg.run_change",
            "file_path": "src/runner.py",
            "entity_type": "function",
        }
    ]
    assert payload["grounding"]["likely_tests"][0]["file_path"] == "tests/test_app.py"
    assert payload["grounding"]["endpoint_entities"] == []
    assert payload["retrieval_policy"]["requested_mode"] == "codeintel_first"
    assert payload["context_pack"]["commit_hash"] == "commit-1"
    assert len(captured_calls) == 3
    assert captured_calls[0]["url"] == "http://codeintel.local/api/v1/index-jobs"
    assert captured_calls[1]["url"] == "http://codeintel.local/api/v1/repos"
    assert captured_calls[2]["url"] == "http://codeintel.local/api/v1/answers/context-pack"
    assert cast(dict[str, object], captured_calls[0]["headers"])["X-Project-ID"] == "proj-1"
    assert cast(dict[str, object], captured_calls[0]["headers"])["X-Forwarded-Proto"] == "https"


@pytest.mark.asyncio
async def test_get_context_normalizes_duplicate_test_prefix_from_context_pack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Resp:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params, headers):
            if url.endswith("/api/v1/index-jobs"):
                return _Resp({"success": True, "data": [{"id": "job-1"}], "pagination": {"total": 1}})
            if url.endswith("/api/v1/repos"):
                return _Resp({"success": True, "data": [{"id": "repo-1"}], "pagination": {"total": 1}})
            if url.endswith("/api/v1/answers/context-pack"):
                return _Resp(
                    {
                        "success": True,
                        "data": {
                            "query": "code_change",
                            "repo_id": "repo-1",
                            "snapshot_id": "snap-1",
                            "commit_hash": "commit-1",
                            "primary_entity": {
                                "entity_id": "ent-1",
                                "repo_id": "repo-1",
                                "file_path": "src/app.py",
                                "entity_type": "function",
                                "name": "code_change",
                                "qualified_name": "pkg.code_change",
                                "signature": "code_change()",
                                "start_line": 42,
                                "end_line": 52,
                                "confidence_score": 0.95,
                                "freshness_status": "fresh",
                                "warnings": [],
                                "why_ranked": ["exact_symbol_match"],
                            },
                            "matched_entities": [],
                            "callers": [],
                            "likely_tests": ["tests/test_test_app.py"],
                            "adjacent_files": ["src/app.py"],
                            "artifacts": [],
                            "citations": [],
                        },
                    }
                )
            return _Resp({"success": True, "data": [], "pagination": {"total": 0}})

    module = importlib.import_module("backend.clients.codeintel_query_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = CodeintelQueryClient(base_url="http://codeintel.local", timeout_seconds=5, service_token="token")
    payload, err = await client.get_context(
        tenant_id="default",
        project_id="proj-1",
        entity_query="code_change",
        retrieval_policy={"requested_mode": "codeintel_first", "effective_mode": "codeintel_first"},
    )

    assert err is None
    assert payload["grounding"]["likely_tests"] == [
        {"file_path": "tests/test_app.py", "score": 1, "provenance": "context_pack"}
    ]


@pytest.mark.asyncio
async def test_get_context_threads_task_identity_into_context_pack_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_calls: list[dict[str, object]] = []

    class _Resp:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params, headers):
            captured_calls.append({"url": url, "params": params, "headers": headers})
            if url.endswith("/api/v1/index-jobs"):
                return _Resp({"success": True, "data": [], "pagination": {"total": 0}})
            if url.endswith("/api/v1/repos"):
                return _Resp({"success": True, "data": [{"id": "repo-1"}], "pagination": {"total": 1}})
            if url.endswith("/api/v1/answers/context-pack"):
                return _Resp(
                    {
                        "success": True,
                        "data": {
                            "query": "code_change",
                            "repo_id": "repo-1",
                            "snapshot_id": "snap-1",
                            "commit_hash": "commit-1",
                            "primary_entity": {
                                "entity_id": "ent-1",
                                "repo_id": "repo-1",
                                "file_path": "src/app.py",
                                "entity_type": "function",
                                "name": "code_change",
                                "qualified_name": "pkg.code_change",
                                "signature": "code_change()",
                                "start_line": 42,
                                "end_line": 52,
                                "confidence_score": 0.95,
                                "freshness_status": "fresh",
                                "warnings": [],
                                "why_ranked": ["exact_symbol_match"],
                            },
                            "matched_entities": [],
                            "callers": [],
                            "likely_tests": [],
                            "adjacent_files": ["src/app.py"],
                            "artifacts": [],
                            "citations": [],
                        },
                    }
                )
            return _Resp({"success": True, "data": [], "pagination": {"total": 0}})

    module = importlib.import_module("backend.clients.codeintel_query_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = CodeintelQueryClient(base_url="http://codeintel.local", timeout_seconds=5, service_token="token")
    payload, err = await client.get_context(
        tenant_id="default",
        project_id="proj-1",
        entity_query="code_change",
        retrieval_policy={"task_number": 2413, "task_project_id": "bh-migrated-7-aix"},
    )

    assert err is None
    assert payload["query_identity"]["entity_query"] == "code_change"
    context_pack_call = next(
        item for item in captured_calls if str(item["url"]).endswith("/api/v1/answers/context-pack")
    )
    params = cast(dict[str, object], context_pack_call["params"])
    assert params["task_number"] == 2413
    assert params["task_project_id"] == "bh-migrated-7-aix"


@pytest.mark.asyncio
async def test_get_context_omits_invalid_task_identity_from_context_pack_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_calls: list[dict[str, object]] = []

    class _Resp:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params, headers):
            captured_calls.append({"url": url, "params": params, "headers": headers})
            if url.endswith("/api/v1/index-jobs"):
                return _Resp({"success": True, "data": [], "pagination": {"total": 0}})
            if url.endswith("/api/v1/repos"):
                return _Resp({"success": True, "data": [{"id": "repo-1"}], "pagination": {"total": 1}})
            if url.endswith("/api/v1/answers/context-pack"):
                return _Resp(
                    {
                        "success": True,
                        "data": {
                            "query": "code_change",
                            "repo_id": "repo-1",
                            "snapshot_id": "snap-1",
                            "commit_hash": "commit-1",
                            "primary_entity": {
                                "entity_id": "ent-1",
                                "repo_id": "repo-1",
                                "file_path": "src/app.py",
                                "entity_type": "function",
                                "name": "code_change",
                                "qualified_name": "pkg.code_change",
                                "signature": "code_change()",
                                "start_line": 42,
                                "end_line": 52,
                                "confidence_score": 0.95,
                                "freshness_status": "fresh",
                                "warnings": [],
                                "why_ranked": ["exact_symbol_match"],
                            },
                            "matched_entities": [],
                            "callers": [],
                            "likely_tests": [],
                            "adjacent_files": ["src/app.py"],
                            "artifacts": [],
                            "citations": [],
                        },
                    }
                )
            return _Resp({"success": True, "data": [], "pagination": {"total": 0}})

    module = importlib.import_module("backend.clients.codeintel_query_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = CodeintelQueryClient(base_url="http://codeintel.local", timeout_seconds=5, service_token="token")
    payload, err = await client.get_context(
        tenant_id="default",
        project_id="proj-1",
        entity_query="code_change",
        retrieval_policy={"task_number": "not-a-number", "task_project_id": "bh-migrated-7-aix"},
    )

    assert err is None
    assert payload["query_identity"]["entity_query"] == "code_change"
    context_pack_call = next(
        item for item in captured_calls if str(item["url"]).endswith("/api/v1/answers/context-pack")
    )
    params = cast(dict[str, object], context_pack_call["params"])
    assert "task_number" not in params
    assert "task_project_id" not in params


@pytest.mark.asyncio
async def test_get_context_hydrates_context_pack_entities_from_top_level_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Resp:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params, headers):
            if url.endswith("/api/v1/index-jobs"):
                return _Resp({"success": True, "data": [{"id": "job-1"}], "pagination": {"total": 1}})
            if url.endswith("/api/v1/repos"):
                return _Resp({"success": True, "data": [{"id": "repo-1"}], "pagination": {"total": 1}})
            if url.endswith("/api/v1/answers/context-pack"):
                return _Resp(
                    {
                        "success": True,
                        "data": {
                            "query": "code_change",
                            "repo_id": "repo-1",
                            "snapshot_id": "snap-9",
                            "commit_hash": "commit-9",
                            "primary_entity": {
                                "entity_id": "ent-9",
                                "file_path": "src/app.py",
                                "entity_type": "function",
                                "name": "code_change",
                                "qualified_name": "pkg.code_change",
                                "signature": "code_change(repo_id: str | None = None)",
                                "start_line": 90,
                                "end_line": 120,
                            },
                            "matched_entities": [],
                            "callers": [],
                            "likely_tests": [],
                            "adjacent_files": ["src/app.py"],
                            "artifacts": [],
                            "citations": [],
                        },
                    }
                )
            return _Resp({"success": True, "data": [], "pagination": {"total": 0}})

    module = importlib.import_module("backend.clients.codeintel_query_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = CodeintelQueryClient(base_url="http://codeintel.local", timeout_seconds=5, service_token="token")
    payload, err = await client.get_context(
        tenant_id="default",
        project_id="proj-1",
        entity_query="code_change",
    )

    assert err is None
    assert payload["entities"][0]["snapshot_id"] == "snap-9"
    assert payload["entities"][0]["commit_sha"] == "commit-9"
    assert payload["entities"][0]["signature"] == "code_change(repo_id: str | None = None)"
    assert payload["grounding"]["matched_entities"][0]["snapshot_id"] == "snap-9"
    assert payload["grounding"]["matched_entities"][0]["commit_sha"] == "commit-9"
    assert payload["citations"][0]["snapshot_id"] == "snap-9"
    assert payload["citations"][0]["commit_sha"] == "commit-9"
    assert payload["citations"][0]["start_line"] is None
    assert payload["citations"][0]["end_line"] is None
    assert payload["query_identity"]["entity_query"] == "code_change"
    assert payload["query_identity"]["query_fingerprint"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dominant_reason_code", "expected_reason", "can_retry", "retry_action"),
    [
        ("DOCS_ONLY_COVERAGE", "codeintel_docs_only_coverage", False, None),
        ("QUERY_TOO_BROAD", "codeintel_query_too_broad", True, "narrow_query"),
    ],
)
async def test_get_context_returns_bounded_refusal_context_without_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
    dominant_reason_code: str,
    expected_reason: str,
    can_retry: bool,
    retry_action: str | None,
) -> None:
    captured_calls: list[str] = []

    class _Resp:
        def __init__(self, payload: dict, *, status_code: int = 200, url: str = "http://codeintel.local") -> None:
            self._payload = payload
            self.status_code = status_code
            self.request = httpx.Request("GET", url)
            self.response = httpx.Response(status_code, request=self.request)

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("error", request=self.request, response=self.response)

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params, headers):
            captured_calls.append(url)
            if url.endswith("/api/v1/index-jobs"):
                return _Resp({"success": True, "data": [{"id": "job-1"}], "pagination": {"total": 1}}, url=url)
            if url.endswith("/api/v1/repos"):
                return _Resp({"success": True, "data": [{"id": "repo-1"}], "pagination": {"total": 1}}, url=url)
            if url.endswith("/api/v1/answers/context-pack"):
                return _Resp(
                    {
                        "success": True,
                        "data": {
                            "query": "proposal workspace",
                            "repo_id": "repo-1",
                            "snapshot_id": "snap-1",
                            "commit_hash": "commit-1",
                            "primary_entity": None,
                            "matched_entities": [],
                            "callers": [],
                            "likely_tests": [],
                            "adjacent_files": [],
                            "artifacts": [],
                            "citations": [],
                            "resolution": {
                                "status": "bounded_refusal",
                                "dominant_reason_code": dominant_reason_code,
                                "reason_codes": [dominant_reason_code],
                                "coverage_state": "docs_only"
                                if dominant_reason_code == "DOCS_ONLY_COVERAGE"
                                else "none",
                                "query_intent": "planner_phrase",
                                "matched_entity_count": 0,
                                "code_entity_count": 0,
                                "documentation_entity_count": 2 if dominant_reason_code == "DOCS_ONLY_COVERAGE" else 0,
                                "caller_count": 0,
                                "can_retry": can_retry,
                                "retry_action": retry_action,
                                "reason_summary": "Planner query is not grounded to executable code.",
                            },
                        },
                    },
                    url=url,
                )
            return _Resp({"success": True, "data": [], "pagination": {"total": 0}}, url=url)

    module = importlib.import_module("backend.clients.codeintel_query_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = CodeintelQueryClient(base_url="http://codeintel.local", timeout_seconds=5, service_token="token")
    payload, err = await client.get_context(tenant_id="default", project_id="proj-1", entity_query="proposal workspace")

    assert err is None
    assert payload["retrieval_mode"] == "deterministic_context_pack"
    assert payload["entities"] == []
    assert payload["citations"] == []
    assert payload["fallback_policy"]["mode"] == "bounded_refusal"
    assert payload["fallback_policy"]["reason"] == expected_reason
    assert payload["fallback_policy"]["reason_code"] == dominant_reason_code
    assert payload["fallback_policy"]["can_retry"] is can_retry
    assert payload["fallback_policy"]["retry_action"] == retry_action
    assert payload["resolution"]["status"] == "bounded_refusal"
    assert "http://codeintel.local/api/v1/answers/context-pack" in captured_calls
    assert "http://codeintel.local/api/v1/repos/repo-1/files" not in captured_calls


@pytest.mark.asyncio
async def test_get_context_requires_token() -> None:
    client = CodeintelQueryClient(base_url="http://codeintel.local", timeout_seconds=5, service_token=None)
    payload, err = await client.get_context(
        tenant_id="default",
        project_id="proj-1",
        entity_query="code_change",
    )
    assert payload == {}
    assert err == "CODEINTEL_API_TOKEN_MISSING"


@pytest.mark.asyncio
async def test_get_context_falls_back_to_legacy_when_context_pack_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_calls: list[str] = []

    class _Resp:
        def __init__(self, payload: dict, *, status_code: int = 200, url: str = "http://codeintel.local") -> None:
            self._payload = payload
            self.status_code = status_code
            self.request = httpx.Request("GET", url)
            self.response = httpx.Response(status_code, request=self.request)

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("error", request=self.request, response=self.response)

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params, headers):
            captured_calls.append(url)
            if url.endswith("/api/v1/index-jobs"):
                return _Resp({"success": True, "data": [{"id": "job-1"}], "pagination": {"total": 1}}, url=url)
            if url.endswith("/api/v1/repos"):
                return _Resp({"success": True, "data": [{"id": "repo-1"}], "pagination": {"total": 1}}, url=url)
            if url.endswith("/api/v1/answers/context-pack"):
                return _Resp({}, status_code=404, url=url)
            if url.endswith("/api/v1/repos/repo-1/files"):
                return _Resp(
                    {
                        "success": True,
                        "data": [
                            {"id": "file-1", "file_path": "src/app.py", "snapshot_id": "snap-1"},
                            {"id": "file-2", "file_path": "tests/test_app.py", "snapshot_id": "snap-1"},
                        ],
                        "pagination": {"total": 2},
                    },
                    url=url,
                )
            if url.endswith("/api/v1/entities"):
                return _Resp(
                    {
                        "success": True,
                        "data": [
                            {
                                "id": "ent-1",
                                "repo_id": "repo-1",
                                "file_id": "file-1",
                                "snapshot_id": "snap-1",
                                "start_line": 42,
                                "name": "code_change",
                                "qualified_name": "pkg.code_change",
                            }
                        ],
                        "pagination": {"total": 1},
                    },
                    url=url,
                )
            if url.endswith("/api/v1/repos/repo-1/entities"):
                return _Resp(
                    {
                        "success": True,
                        "data": [
                            {
                                "id": "ent-1",
                                "repo_id": "repo-1",
                                "file_id": "file-1",
                                "file_path": "src/app.py",
                                "snapshot_id": "snap-1",
                                "start_line": 42,
                                "end_line": 52,
                                "name": "code_change",
                                "qualified_name": "pkg.code_change",
                                "entity_type": "function",
                                "confidence_score": 0.95,
                                "freshness_status": "fresh",
                                "warnings": [],
                            }
                        ],
                        "pagination": {"total": 1},
                    },
                    url=url,
                )
            if url.endswith("/api/v1/repos/repo-1/relations"):
                return _Resp(
                    {
                        "success": True,
                        "data": [
                            {
                                "id": "rel-1",
                                "relation_type": "calls",
                                "source_entity_id": "caller-1",
                                "target_entity_id": "ent-1",
                            }
                        ],
                        "pagination": {"total": 1},
                    },
                    url=url,
                )
            if url.endswith("/api/v1/entities/ent-1/impact"):
                return _Resp(
                    {
                        "success": True,
                        "data": {
                            "root_entity_id": "ent-1",
                            "nodes": [
                                {
                                    "id": "ent-1",
                                    "repo_id": "repo-1",
                                    "file_path": "src/app.py",
                                    "entity_type": "function",
                                    "name": "code_change",
                                    "qualified_name": "pkg.code_change",
                                }
                            ],
                            "edges": [],
                        },
                    },
                    url=url,
                )
            return _Resp({"success": True, "data": [], "pagination": {"total": 0}}, url=url)

    module = importlib.import_module("backend.clients.codeintel_query_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = CodeintelQueryClient(base_url="http://codeintel.local", timeout_seconds=5, service_token="token")
    payload, err = await client.get_context(tenant_id="default", project_id="proj-1", entity_query="code_change")

    assert err is None
    assert payload["retrieval_mode"] == "codeintel_first"
    assert payload["primary_repo_id"] == "repo-1"
    assert "http://codeintel.local/api/v1/answers/context-pack" in captured_calls
    assert "http://codeintel.local/api/v1/repos/repo-1/files" in captured_calls


@pytest.mark.asyncio
async def test_get_context_failure_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        request = httpx.Request("GET", "http://codeintel.local/api/v1/index-jobs")

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError("server error", request=self.request, response=httpx.Response(500))

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params, headers):
            return _Resp()

    module = importlib.import_module("backend.clients.codeintel_query_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = CodeintelQueryClient(base_url="http://codeintel.local", timeout_seconds=5, service_token="token")
    payload, err = await client.get_context(
        tenant_id="default",
        project_id="proj-1",
        entity_query="code_change",
    )
    assert payload == {}
    assert err is not None


@pytest.mark.asyncio
async def test_get_context_sets_fallback_policy_when_confidence_low(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params, headers):
            if url.endswith("/api/v1/index-jobs"):
                return _Resp({"success": True, "data": [], "pagination": {"total": 0}})
            if url.endswith("/api/v1/repos"):
                return _Resp({"success": True, "data": [{"id": "repo-1"}], "pagination": {"total": 1}})
            if url.endswith("/api/v1/repos/repo-1/files"):
                return _Resp({"success": True, "data": [], "pagination": {"total": 0}})
            if url.endswith("/api/v1/entities"):
                return _Resp({"success": True, "data": [], "pagination": {"total": 0}})
            return _Resp({"success": True, "data": [], "pagination": {"total": 0}})

    module = importlib.import_module("backend.clients.codeintel_query_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    client = CodeintelQueryClient(base_url="http://codeintel.local", timeout_seconds=5, service_token="token")
    payload, err = await client.get_context(
        tenant_id="default",
        project_id="proj-1",
        entity_query="unknown_query",
    )
    assert err is None
    assert payload["confidence_score"] == 0.35
    assert payload["latency_ms"] >= 0
    assert payload["fallback_policy"]["mode"] == "fallback"
    assert payload["fallback_policy"]["reason"] == "codeintel_low_confidence"


@pytest.mark.asyncio
async def test_get_context_applies_haystack_adapter_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params, headers):
            if url.endswith("/api/v1/index-jobs"):
                return _Resp({"success": True, "data": [{"id": "job-1"}], "pagination": {"total": 1}})
            if url.endswith("/api/v1/repos"):
                return _Resp({"success": True, "data": [{"id": "repo-1"}], "pagination": {"total": 1}})
            if url.endswith("/api/v1/repos/repo-1/files"):
                return _Resp(
                    {
                        "success": True,
                        "data": [{"id": "file-1", "file_path": "src/app.py", "snapshot_id": "snap-1"}],
                        "pagination": {"total": 1},
                    }
                )
            if url.endswith("/api/v1/entities"):
                return _Resp(
                    {
                        "success": True,
                        "data": [
                            {
                                "id": "ent-1",
                                "repo_id": "repo-1",
                                "file_id": "file-1",
                                "snapshot_id": "snap-1",
                                "start_line": 5,
                                "name": "app",
                                "qualified_name": "src.app",
                            }
                        ],
                        "pagination": {"total": 1},
                    }
                )
            return _Resp({"success": True, "data": [], "pagination": {"total": 0}})

    module = importlib.import_module("backend.clients.codeintel_query_client")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(module.settings, "HAYSTACK_RETRIEVAL_ENABLED", False)

    client = CodeintelQueryClient(base_url="http://codeintel.local", timeout_seconds=5, service_token="token")
    payload, err = await client.get_context(
        tenant_id="default",
        project_id="proj-1",
        entity_query="app",
        retrieval_policy={"adapter": "haystack"},
    )

    assert err is None
    assert payload["retrieval_adapter"] in {"haystack_fallback", "haystack"}
    assert payload["topk_returned"] >= 1
    assert isinstance(payload["ranked_chunks"], list)
    assert isinstance(payload["source_refs"], list)
