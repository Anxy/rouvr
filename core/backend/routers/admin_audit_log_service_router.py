"""
AdminAuditLog API Router
Generated FastAPI router with DAL-backed CRUD endpoints.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from backend.dependencies.admin_audit_log_service_dependencies import (
    ensure_valid_payload,
    get_admin_audit_log_service,
    get_pack_bundle,
    get_pack_context,
    require_permission,
    validate_tenant_access,
)
from backend.models.admin_audit_log_service_models import (
    AdminAuditLogCreate,
    AdminAuditLogUpdate,
)
from backend.models.admin_audit_log_service_models import (
    AdminAuditLogResponse as AdminAuditLogModel,
)
from backend.services.admin_audit_log_service import AdminAuditLogService
from backend.shared.hyeve_base_service import ServiceError
from hyeve_core.core_libs.types import PackBundle, PackContext

router = APIRouter(
    prefix="/api/admin-audit-log/audit",
    tags=["admin audit log"],
    responses={404: {"description": "Not found"}},
)


class AdminAuditLogAPIResponse(BaseModel):
    success: bool = True
    message: str = "AdminAuditLog operation completed successfully"
    data: AdminAuditLogModel | None = None


class AdminAuditLogListAPIResponse(BaseModel):
    success: bool = True
    message: str = "List retrieved successfully"
    data: list[AdminAuditLogModel] = []
    total: int = 0
    limit: int = 100
    offset: int = 0
    has_more: bool = False


@router.get(
    "/metadata/workflows",
    response_model=dict,
    summary="List admin_audit_log workflows",
    description="Expose workflow identifiers defined in the current pack bundle",
)
async def get_admin_audit_log_workflows(
    bundle: PackBundle = Depends(get_pack_bundle),
):
    """Expose workflow identifiers defined by the current pack."""
    return bundle.workflows or {}


def _raise_service_error(exc: ServiceError) -> None:
    """Translate ServiceError codes into HTTP errors."""
    code = getattr(exc, "code", "UNKNOWN")
    detail = {"code": code, "params": getattr(exc, "params", {})}
    if code.startswith("VAL."):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)
    if "NOT_FOUND" in code:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    if "PERMISSION" in code:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    if "DAL." in code:
        raise HTTPException(status_code=500, detail=detail)
    raise HTTPException(status_code=500, detail=detail)


@router.post(
    "/",
    response_model=AdminAuditLogAPIResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create AdminAuditLog",
    description="Create a new admin_audit_log record",
)
async def create_admin_audit_log(
    data: AdminAuditLogCreate,
    admin_audit_log_service: AdminAuditLogService = Depends(get_admin_audit_log_service),
    current_user: dict = Depends(require_permission("admin_audit_log:create")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Create a new admin_audit_log."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="create")
        created = await admin_audit_log_service.create(
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        return AdminAuditLogAPIResponse(
            message="AdminAuditLog created successfully",
            data=created,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create admin_audit_log: {str(exc)}",
        ) from exc


@router.get(
    "/{record_id}",
    response_model=AdminAuditLogAPIResponse,
    summary="Get AdminAuditLog",
    description="Retrieve a admin_audit_log by ID",
)
async def get_admin_audit_log(
    record_id: str,
    admin_audit_log_service: AdminAuditLogService = Depends(get_admin_audit_log_service),
    current_user: dict = Depends(require_permission("admin_audit_log:read")),
):
    """Get admin_audit_log by ID."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        result = await admin_audit_log_service.get_by_id(record_id, tenant_id=tenant_id)
        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AdminAuditLog not found")
        return AdminAuditLogAPIResponse(
            message="AdminAuditLog retrieved successfully",
            data=result,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve admin_audit_log: {str(exc)}",
        ) from exc


@router.get(
    "/",
    response_model=AdminAuditLogListAPIResponse,
    summary="List admin audit log",
    description="Retrieve a paginated list of admin audit log",
)
async def list_admin_audit_log(
    limit: int = Query(default=100, ge=1, le=1000, description="Number of items to return"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
    admin_audit_log_service: AdminAuditLogService = Depends(get_admin_audit_log_service),
    current_user: dict = Depends(require_permission("admin_audit_log:read")),
    record_version: int | None = Query(None, description="Filter by record_version"),
    project_id: str | None = Query(None, description="Filter by project_id"),
    actor_sub: str | None = Query(None, description="Filter by actor_sub"),
    action: str | None = Query(None, description="Filter by action"),
    resource_type: str | None = Query(None, description="Filter by resource_type"),
    resource_id: str | None = Query(None, description="Filter by resource_id"),
    request_id: str | None = Query(None, description="Filter by request_id"),
    created_by: str | None = Query(None, description="Filter by created_by"),
    updated_by: str | None = Query(None, description="Filter by updated_by"),
    deleted_at: str | None = Query(None, description="Filter by deleted_at"),
):
    """List admin audit log with pagination and filters."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        filters: dict[str, Any] = {}
        if record_version is not None:
            filters["record_version"] = record_version
        if project_id is not None:
            filters["project_id"] = project_id
        if actor_sub is not None:
            filters["actor_sub"] = actor_sub
        if action is not None:
            filters["action"] = action
        if resource_type is not None:
            filters["resource_type"] = resource_type
        if resource_id is not None:
            filters["resource_id"] = resource_id
        if request_id is not None:
            filters["request_id"] = request_id
        if created_by is not None:
            filters["created_by"] = created_by
        if updated_by is not None:
            filters["updated_by"] = updated_by
        if deleted_at is not None:
            filters["deleted_at"] = deleted_at
        records = await admin_audit_log_service.list(
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
            filters=filters or None,
        )
        return AdminAuditLogListAPIResponse(
            message="admin audit log retrieved successfully",
            data=records.get("items", []),
            total=records.get("total", 0),
            limit=records.get("limit", limit),
            offset=records.get("offset", offset),
            has_more=records.get("has_more", False),
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list admin audit log records: {str(exc)}",
        ) from exc


@router.patch(
    "/{record_id}",
    response_model=AdminAuditLogAPIResponse,
    summary="Update AdminAuditLog",
    description="Update an existing admin_audit_log",
)
async def update_admin_audit_log(
    record_id: str,
    data: AdminAuditLogUpdate,
    admin_audit_log_service: AdminAuditLogService = Depends(get_admin_audit_log_service),
    current_user: dict = Depends(require_permission("admin_audit_log:update")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Update a admin_audit_log record."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="update")
        updated = await admin_audit_log_service.update(
            record_id=record_id,
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AdminAuditLog not found")
        return AdminAuditLogAPIResponse(
            message="AdminAuditLog updated successfully",
            data=updated,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update admin_audit_log: {str(exc)}",
        ) from exc


@router.delete(
    "/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete AdminAuditLog",
    description="Delete a admin_audit_log record",
)
async def delete_admin_audit_log(
    record_id: str,
    admin_audit_log_service: AdminAuditLogService = Depends(get_admin_audit_log_service),
    current_user: dict = Depends(require_permission("admin_audit_log:delete")),
):
    """Delete a admin_audit_log."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        deleted = await admin_audit_log_service.delete(
            record_id=record_id,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
        )
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AdminAuditLog not found")
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete admin_audit_log: {str(exc)}",
        ) from exc
