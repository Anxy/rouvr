from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from prometheus_client import Counter

from backend.hyeve_aix_service.config import settings
from backend.services.aix_execution_packet_validator import (
    AixExecutionPacketValidationError,
    validate_aix_execution_packet,
)
from backend.shared.request_header_utils import build_service_headers, resolve_project_header, resolve_tenant_header

router = APIRouter(prefix="/api/aix/integrations", tags=["aix integrations"])
_EXECUTION_PACKET_VALIDATION_TOTAL = Counter(
    "hyeve_aix_execution_packet_validation_total",
    "Execution packet validation outcomes at AIX integration boundary.",
    ["locus", "schema_name", "outcome"],
)


def _observe_execution_packet_validation(*, locus: str, schema_name: str | None, outcome: str) -> None:
    schema_label = schema_name if schema_name else "unknown"
    _EXECUTION_PACKET_VALIDATION_TOTAL.labels(locus=locus, schema_name=schema_label, outcome=outcome).inc()


def _require_auth_header(authorization: str | None) -> str:
    if not authorization or not authorization.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization header required")
    return authorization


def _validate_execution_packet_if_present(payload: dict[str, Any], *, locus: str) -> str | None:
    """Validate canonical execution packets at the AIX integration boundary."""
    try:
        schema_name = validate_aix_execution_packet(payload, locus=locus)
        _observe_execution_packet_validation(locus=locus, schema_name=schema_name, outcome="valid")
        return schema_name
    except AixExecutionPacketValidationError as exc:
        if exc.code == "execution_packet_required" and locus == "egress":
            _observe_execution_packet_validation(locus=locus, schema_name=None, outcome="non_packet_ignored")
            return None
        if exc.code == "execution_packet_required":
            outcome = "required_missing"
        elif exc.code == "execution_packet_schema_unknown":
            outcome = "schema_unknown"
        else:
            outcome = "invalid"
        _observe_execution_packet_validation(locus=locus, schema_name=exc.schema_name, outcome=outcome)
        detail = exc.to_dict()
        detail["status"] = status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail) from exc


def _forward_headers(
    *,
    request: Request,
    authorization: str,
    tenant_id: str,
    correlation_id: str | None,
    idempotency_key: str | None,
    project_id: str | None,
    body: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
) -> dict[str, str]:
    resolved_project_id = _resolve_project_id(project_id=project_id, body=body, query=query)
    resolved_correlation_id = _resolve_correlation_id(request=request, correlation_id=correlation_id)
    return build_service_headers(
        authorization=authorization,
        tenant_id=tenant_id,
        correlation_id=resolved_correlation_id,
        fallback_correlation_id=resolved_correlation_id,
        idempotency_key=idempotency_key,
        project_id=resolved_project_id,
        fallback_project_id=resolved_project_id,
        auto_generate_correlation=True,
        require_auth=True,
        require_tenant=True,
    )


def _resolve_project_id(
    *,
    project_id: str | None,
    body: dict[str, Any] | None,
    query: dict[str, str] | None,
) -> str | None:
    explicit = str(project_id or "").strip()
    if explicit:
        return explicit
    if isinstance(body, dict):
        payload_project = str(body.get("project_id") or "").strip()
        if payload_project:
            return payload_project
    if isinstance(query, dict):
        query_project = str(query.get("project_id") or "").strip()
        if query_project:
            return query_project
    return None


def _resolve_correlation_id(*, request: Request, correlation_id: str | None) -> str:
    explicit = str(correlation_id or "").strip()
    if explicit:
        return explicit
    request_id = str(request.headers.get("X-Request-ID") or "").strip()
    if request_id:
        return request_id
    state_correlation = str(getattr(request.state, "correlation_id", "") or "").strip()
    if state_correlation:
        return state_correlation
    return f"aix-int-{uuid4().hex}"


async def _proxy_json(
    *,
    method: str,
    url: str,
    timeout_seconds: int,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
) -> JSONResponse:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.request(method, url, headers=headers, json=body, params=query)
    try:
        payload = response.json()
    except Exception:
        payload = {"success": False, "detail": response.text}
    if isinstance(payload, dict):
        _validate_execution_packet_if_present(payload, locus="egress")
    return JSONResponse(payload, status_code=response.status_code)


async def _safe_json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


@router.post("/bre/evaluate")
async def bre_evaluate(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    auth = _require_auth_header(authorization)
    body = await _safe_json_body(request)
    tenant_id = resolve_tenant_header(request.headers, default="default") or "default"
    project_id = resolve_project_header(request.headers)
    _validate_execution_packet_if_present(body, locus="ingress")
    base = str(settings.BRE_API_URL).rstrip("/")
    return await _proxy_json(
        method="POST",
        url=f"{base}/api/bre/evaluate",
        timeout_seconds=int(settings.BRE_API_TIMEOUT_SECONDS),
        headers=_forward_headers(
            request=request,
            authorization=auth,
            tenant_id=tenant_id,
            correlation_id=x_correlation_id,
            idempotency_key=idempotency_key,
            project_id=project_id,
            body=body,
        ),
        body=body,
    )


@router.post("/wfe/instances")
async def wfe_create_instance(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    auth = _require_auth_header(authorization)
    body = await _safe_json_body(request)
    tenant_id = resolve_tenant_header(request.headers, default="default") or "default"
    project_id = resolve_project_header(request.headers)
    _validate_execution_packet_if_present(body, locus="ingress")
    base = str(settings.WFE_API_URL).rstrip("/")
    return await _proxy_json(
        method="POST",
        url=f"{base}/api/wfe/instances",
        timeout_seconds=int(settings.WFE_API_TIMEOUT_SECONDS),
        headers=_forward_headers(
            request=request,
            authorization=auth,
            tenant_id=tenant_id,
            correlation_id=x_correlation_id,
            idempotency_key=idempotency_key,
            project_id=project_id,
            body=body,
        ),
        body=body,
    )


@router.get("/wfe/instances/{instance_id}")
async def wfe_get_instance(
    instance_id: str,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
):
    auth = _require_auth_header(authorization)
    tenant_id = resolve_tenant_header(request.headers, default="default") or "default"
    project_id = resolve_project_header(request.headers)
    base = str(settings.WFE_API_URL).rstrip("/")
    return await _proxy_json(
        method="GET",
        url=f"{base}/api/wfe/instances/{instance_id}",
        timeout_seconds=int(settings.WFE_API_TIMEOUT_SECONDS),
        headers=_forward_headers(
            request=request,
            authorization=auth,
            tenant_id=tenant_id,
            correlation_id=x_correlation_id,
            idempotency_key=None,
            project_id=project_id,
        ),
    )


@router.post("/wfe/instances/{instance_id}/signal")
async def wfe_signal_instance(
    request: Request,
    instance_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    auth = _require_auth_header(authorization)
    body = await _safe_json_body(request)
    tenant_id = resolve_tenant_header(request.headers, default="default") or "default"
    project_id = resolve_project_header(request.headers)
    _validate_execution_packet_if_present(body, locus="ingress")
    base = str(settings.WFE_API_URL).rstrip("/")
    return await _proxy_json(
        method="POST",
        url=f"{base}/api/wfe/instances/{instance_id}/signal",
        timeout_seconds=int(settings.WFE_API_TIMEOUT_SECONDS),
        headers=_forward_headers(
            request=request,
            authorization=auth,
            tenant_id=tenant_id,
            correlation_id=x_correlation_id,
            idempotency_key=idempotency_key,
            project_id=project_id,
            body=body,
        ),
        body=body,
    )


@router.get("/wfe/tasks")
async def wfe_list_tasks(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
):
    auth = _require_auth_header(authorization)
    base = str(settings.WFE_API_URL).rstrip("/")
    query = dict(request.query_params.multi_items())
    tenant_id = resolve_tenant_header(request.headers, default="default") or "default"
    project_id = resolve_project_header(request.headers)
    return await _proxy_json(
        method="GET",
        url=f"{base}/api/wfe/tasks",
        timeout_seconds=int(settings.WFE_API_TIMEOUT_SECONDS),
        headers=_forward_headers(
            request=request,
            authorization=auth,
            tenant_id=tenant_id,
            correlation_id=x_correlation_id,
            idempotency_key=None,
            project_id=project_id,
            query=query,
        ),
        query=query,
    )


@router.post("/wfe/tasks/{task_id}/complete")
async def wfe_complete_task(
    request: Request,
    task_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    auth = _require_auth_header(authorization)
    body = await _safe_json_body(request)
    tenant_id = resolve_tenant_header(request.headers, default="default") or "default"
    project_id = resolve_project_header(request.headers)
    _validate_execution_packet_if_present(body, locus="ingress")
    base = str(settings.WFE_API_URL).rstrip("/")
    return await _proxy_json(
        method="POST",
        url=f"{base}/api/wfe/tasks/{task_id}/complete",
        timeout_seconds=int(settings.WFE_API_TIMEOUT_SECONDS),
        headers=_forward_headers(
            request=request,
            authorization=auth,
            tenant_id=tenant_id,
            correlation_id=x_correlation_id,
            idempotency_key=idempotency_key,
            project_id=project_id,
            body=body,
        ),
        body=body,
    )
