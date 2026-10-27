"""
ServiceConfig API Router
Generated FastAPI router with DAL-backed CRUD endpoints.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from backend.dependencies.service_config_service_dependencies import (
    ensure_valid_payload,
    get_pack_bundle,
    get_pack_context,
    get_service_config_service,
    require_permission,
    validate_tenant_access,
)
from backend.models.service_config_service_models import (
    ServiceConfigCreate,
    ServiceConfigUpdate,
)
from backend.models.service_config_service_models import (
    ServiceConfigResponse as ServiceConfigModel,
)
from backend.services.service_config_service import ServiceConfigService
from backend.shared.hyeve_base_service import ServiceError
from hyeve_core.core_libs.types import PackBundle, PackContext

router = APIRouter(
    prefix="/api/service-config/audit",
    tags=["service config"],
    responses={404: {"description": "Not found"}},
)


class ServiceConfigAPIResponse(BaseModel):
    success: bool = True
    message: str = "ServiceConfig operation completed successfully"
    data: ServiceConfigModel | None = None


class ServiceConfigListAPIResponse(BaseModel):
    success: bool = True
    message: str = "List retrieved successfully"
    data: list[ServiceConfigModel] = []
    total: int = 0
    limit: int = 100
    offset: int = 0
    has_more: bool = False


@router.get(
    "/metadata/workflows",
    response_model=dict,
    summary="List service_config workflows",
    description="Expose workflow identifiers defined in the current pack bundle",
)
async def get_service_config_workflows(
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
    response_model=ServiceConfigAPIResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create ServiceConfig",
    description="Create a new service_config record",
)
async def create_service_config(
    data: ServiceConfigCreate,
    service_config_service: ServiceConfigService = Depends(get_service_config_service),
    current_user: dict = Depends(require_permission("service_config:create")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Create a new service_config."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="create")
        created = await service_config_service.create(
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        return ServiceConfigAPIResponse(
            message="ServiceConfig created successfully",
            data=created,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create service_config: {str(exc)}",
        ) from exc


@router.get(
    "/{record_id}",
    response_model=ServiceConfigAPIResponse,
    summary="Get ServiceConfig",
    description="Retrieve a service_config by ID",
)
async def get_service_config(
    record_id: str,
    service_config_service: ServiceConfigService = Depends(get_service_config_service),
    current_user: dict = Depends(require_permission("service_config:read")),
):
    """Get service_config by ID."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        result = await service_config_service.get_by_id(record_id, tenant_id=tenant_id)
        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ServiceConfig not found")
        return ServiceConfigAPIResponse(
            message="ServiceConfig retrieved successfully",
            data=result,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve service_config: {str(exc)}",
        ) from exc


@router.get(
    "/",
    response_model=ServiceConfigListAPIResponse,
    summary="List service config",
    description="Retrieve a paginated list of service config",
)
async def list_service_config(
    limit: int = Query(default=100, ge=1, le=1000, description="Number of items to return"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
    service_config_service: ServiceConfigService = Depends(get_service_config_service),
    current_user: dict = Depends(require_permission("service_config:read")),
    record_version: int | None = Query(None, description="Filter by record_version"),
    project_id: str | None = Query(None, description="Filter by project_id"),
    config_key: str | None = Query(None, description="Filter by config_key"),
    created_by: str | None = Query(None, description="Filter by created_by"),
    updated_by: str | None = Query(None, description="Filter by updated_by"),
    deleted_at: str | None = Query(None, description="Filter by deleted_at"),
):
    """List service config with pagination and filters."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        filters: dict[str, Any] = {}
        if record_version is not None:
            filters["record_version"] = record_version
        if project_id is not None:
            filters["project_id"] = project_id
        if config_key is not None:
            filters["config_key"] = config_key
        if created_by is not None:
            filters["created_by"] = created_by
        if updated_by is not None:
            filters["updated_by"] = updated_by
        if deleted_at is not None:
            filters["deleted_at"] = deleted_at
        records = await service_config_service.list(
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
            filters=filters or None,
        )
        return ServiceConfigListAPIResponse(
            message="service config retrieved successfully",
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
            detail=f"Failed to list service config records: {str(exc)}",
        ) from exc


@router.patch(
    "/{record_id}",
    response_model=ServiceConfigAPIResponse,
    summary="Update ServiceConfig",
    description="Update an existing service_config",
)
async def update_service_config(
    record_id: str,
    data: ServiceConfigUpdate,
    service_config_service: ServiceConfigService = Depends(get_service_config_service),
    current_user: dict = Depends(require_permission("service_config:update")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Update a service_config record."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="update")
        updated = await service_config_service.update(
            record_id=record_id,
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ServiceConfig not found")
        return ServiceConfigAPIResponse(
            message="ServiceConfig updated successfully",
            data=updated,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update service_config: {str(exc)}",
        ) from exc


@router.delete(
    "/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete ServiceConfig",
    description="Delete a service_config record",
)
async def delete_service_config(
    record_id: str,
    service_config_service: ServiceConfigService = Depends(get_service_config_service),
    current_user: dict = Depends(require_permission("service_config:delete")),
):
    """Delete a service_config."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        deleted = await service_config_service.delete(
            record_id=record_id,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
        )
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ServiceConfig not found")
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete service_config: {str(exc)}",
        ) from exc
