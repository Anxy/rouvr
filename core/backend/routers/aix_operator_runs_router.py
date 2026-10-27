from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, Header, status

from backend.errors.aix_errors import aix_http_error
from backend.services.aix_operator_run_api_service import (
    OperatorRunApiError,
    get_operator_run_detail,
    get_operator_run_evidence_bundle,
    get_operator_run_replay_comparison,
    list_operator_runs,
)

router = APIRouter(prefix="/api/aix/runs", tags=["aix operator runs"])


def _require_auth_header(authorization: str | None) -> None:
    if not authorization or not authorization.strip():
        raise aix_http_error(
            http_status=status.HTTP_401_UNAUTHORIZED,
            code="AUTH_FAILED",
            sub_code="AUTH_MISSING",
            detail="Authorization header required",
        )


def _raise_run_api_error(error: OperatorRunApiError) -> NoReturn:
    http_status = (
        status.HTTP_400_BAD_REQUEST
        if error.reason in {"TENANT_ID_REQUIRED", "RUN_ID_REQUIRED"}
        else status.HTTP_502_BAD_GATEWAY
    )
    raise aix_http_error(
        http_status=http_status,
        code="AIX_RUN_API_FAILED",
        sub_code=error.reason,
        detail=error.detail,
        context={"failure_class": error.failure_class},
    )


@router.get("")
async def list_aix_operator_runs(
    authorization: str | None = Header(default=None, alias="Authorization"),
    tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    hyeve_tenant_id: str | None = Header(default=None, alias="X-Hyeve-Tenant-Id"),
    limit: int = 100,
) -> dict[str, Any]:
    _require_auth_header(authorization)
    try:
        return list_operator_runs(tenant_id=tenant_id or hyeve_tenant_id or "", limit=limit).payload
    except OperatorRunApiError as exc:
        _raise_run_api_error(exc)


@router.get("/{run_id}")
async def get_aix_operator_run(
    run_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    hyeve_tenant_id: str | None = Header(default=None, alias="X-Hyeve-Tenant-Id"),
) -> dict[str, Any]:
    _require_auth_header(authorization)
    try:
        result = get_operator_run_detail(tenant_id=tenant_id or hyeve_tenant_id or "", run_id=run_id)
    except OperatorRunApiError as exc:
        _raise_run_api_error(exc)
    if result.status == "not_found":
        raise aix_http_error(
            http_status=status.HTTP_404_NOT_FOUND,
            code="AIX_RUN_NOT_FOUND",
            sub_code="RUN_ID_NOT_FOUND",
            detail="Run not found.",
        )
    return result.payload


@router.get("/{run_id}/evidence")
async def get_aix_operator_run_evidence(
    run_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    hyeve_tenant_id: str | None = Header(default=None, alias="X-Hyeve-Tenant-Id"),
) -> dict[str, Any]:
    _require_auth_header(authorization)
    try:
        result = get_operator_run_evidence_bundle(tenant_id=tenant_id or hyeve_tenant_id or "", run_id=run_id)
    except OperatorRunApiError as exc:
        _raise_run_api_error(exc)
    if result.status == "not_found":
        raise aix_http_error(
            http_status=status.HTTP_404_NOT_FOUND,
            code="AIX_RUN_NOT_FOUND",
            sub_code="RUN_ID_NOT_FOUND",
            detail="Run not found.",
        )
    return result.payload


@router.get("/{run_id}/replay-comparison")
async def get_aix_operator_run_replay_comparison(
    run_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    hyeve_tenant_id: str | None = Header(default=None, alias="X-Hyeve-Tenant-Id"),
) -> dict[str, Any]:
    _require_auth_header(authorization)
    try:
        result = get_operator_run_replay_comparison(tenant_id=tenant_id or hyeve_tenant_id or "", run_id=run_id)
    except OperatorRunApiError as exc:
        _raise_run_api_error(exc)
    if result.status == "not_found":
        raise aix_http_error(
            http_status=status.HTTP_404_NOT_FOUND,
            code="AIX_RUN_NOT_FOUND",
            sub_code="RUN_ID_NOT_FOUND",
            detail="Run not found.",
        )
    return result.payload
