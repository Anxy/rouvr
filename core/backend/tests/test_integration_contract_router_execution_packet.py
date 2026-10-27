from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.routers.integration_contract_router import (
    _forward_headers,
    _resolve_correlation_id,
    _resolve_project_id,
    _validate_execution_packet_if_present,
)


def _valid_execution_packet() -> dict:
    return {
        "schema_name": "aix_execution_packet_current",
        "schema_version": "v1",
        "packet_id": "pkt-1",
        "correlation_id": "corr-1",
        "causation_id": "pkt-0",
        "tenant_id": "default",
        "project_id": "bh-migrated-7-aix",
        "prompt_profile": "planner.default.current",
        "sender": {"agent_id": "planner-1", "role": "planner"},
        "target": {"role": "coder"},
        "step": {
            "index": 1,
            "status": "queued",
            "summary": "Ship feature",
            "evidence_refs": [],
        },
    }


def test_validate_execution_packet_if_present_accepts_valid_payload() -> None:
    schema_name = _validate_execution_packet_if_present(_valid_execution_packet(), locus="ingress")
    assert schema_name == "aix_execution_packet_current"


def test_validate_execution_packet_if_present_rejects_unknown_schema() -> None:
    payload = _valid_execution_packet()
    payload["schema_name"] = "unknown.current"
    with pytest.raises(HTTPException) as exc:
        _validate_execution_packet_if_present(payload, locus="ingress")
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "execution_packet_schema_unknown"


def test_validate_execution_packet_rejects_invalid_step_index() -> None:
    payload = _valid_execution_packet()
    payload["step"]["index"] = 0
    with pytest.raises(HTTPException) as exc:
        _validate_execution_packet_if_present(payload, locus="ingress")
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "execution_packet_invalid"


def test_validate_execution_packet_if_present_ignores_non_packet_payload_on_egress() -> None:
    schema_name = _validate_execution_packet_if_present({"hello": "world"}, locus="egress")
    assert schema_name is None


def test_validate_execution_packet_requires_payload_on_ingress() -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_execution_packet_if_present({"hello": "world"}, locus="ingress")
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "execution_packet_required"


def _build_request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/aix/integrations/wfe/tasks",
        "headers": headers or [],
    }
    return Request(scope)


def test_resolve_project_id_from_body_when_header_missing() -> None:
    resolved = _resolve_project_id(
        project_id=None,
        body={"project_id": "bh-migrated-7-aix"},
        query=None,
    )
    assert resolved == "bh-migrated-7-aix"


def test_resolve_project_id_from_query_when_header_missing() -> None:
    resolved = _resolve_project_id(
        project_id=None,
        body=None,
        query={"project_id": "bh-migrated-7-aix"},
    )
    assert resolved == "bh-migrated-7-aix"


def test_resolve_correlation_id_uses_request_id_fallback() -> None:
    request = _build_request(headers=[(b"x-request-id", b"req-123")])
    resolved = _resolve_correlation_id(request=request, correlation_id=None)
    assert resolved == "req-123"


def test_forward_headers_emits_project_and_correlation_by_default() -> None:
    request = _build_request(headers=[(b"x-request-id", b"req-456")])
    headers = _forward_headers(
        request=request,
        authorization="Bearer token-1",
        tenant_id="default",
        correlation_id=None,
        idempotency_key=None,
        project_id=None,
        body={"project_id": "bh-migrated-7-aix"},
    )
    assert headers["Authorization"] == "Bearer token-1"
    assert headers["X-Tenant-ID"] == "default"
    assert headers["X-Project-ID"] == "bh-migrated-7-aix"
    assert headers["X-Correlation-ID"] == "req-456"


def test_forward_headers_accepts_legacy_project_header_alias() -> None:
    request = _build_request(headers=[(b"x-request-id", b"req-789"), (b"x-project-id", b"proj-ignored")])
    headers = _forward_headers(
        request=request,
        authorization="Bearer token-1",
        tenant_id="default",
        correlation_id=None,
        idempotency_key=None,
        project_id="proj-legacy",
    )
    assert headers["X-Project-ID"] == "proj-legacy"
