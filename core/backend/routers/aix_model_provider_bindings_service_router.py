"""
AixModelProviderBindings API Router
Generated FastAPI router with DAL-backed CRUD endpoints.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from backend.dependencies.aix_model_provider_bindings_service_dependencies import (
    ensure_valid_payload,
    get_aix_model_provider_bindings_service,
    get_pack_bundle,
    get_pack_context,
    require_permission,
    validate_tenant_access,
)
from backend.models.aix_model_provider_bindings_service_models import (
    AixModelProviderBindingsCreate,
    AixModelProviderBindingsUpdate,
)
from backend.models.aix_model_provider_bindings_service_models import (
    AixModelProviderBindingsResponse as AixModelProviderBindingsModel,
)
from backend.services.aix_model_provider_bindings_service import AixModelProviderBindingsService
from backend.shared.hyeve_base_service import ServiceError
from hyeve_core.core_libs.types import PackBundle, PackContext

router = APIRouter(
    prefix="/api/aix-model-provider-bindings/audit",
    tags=["aix model provider bindings"],
    responses={404: {"description": "Not found"}},
)


class AixModelProviderBindingsAPIResponse(BaseModel):
    success: bool = True
    message: str = "AixModelProviderBindings operation completed successfully"
    data: AixModelProviderBindingsModel | None = None


class AixModelProviderBindingsListAPIResponse(BaseModel):
    success: bool = True
    message: str = "List retrieved successfully"
    data: list[AixModelProviderBindingsModel] = []
    total: int = 0
    limit: int = 100
    offset: int = 0
    has_more: bool = False


@router.get(
    "/metadata/workflows",
    response_model=dict,
    summary="List aix_model_provider_bindings workflows",
    description="Expose workflow identifiers defined in the current pack bundle",
)
async def get_aix_model_provider_bindings_workflows(
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
    response_model=AixModelProviderBindingsAPIResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create AixModelProviderBindings",
    description="Create a new aix_model_provider_bindings record",
)
async def create_aix_model_provider_bindings(
    data: AixModelProviderBindingsCreate,
    aix_model_provider_bindings_service: AixModelProviderBindingsService = Depends(
        get_aix_model_provider_bindings_service,
    ),
    current_user: dict = Depends(require_permission("aix_model_provider_bindings:create")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Create a new aix_model_provider_bindings."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="create")
        created = await aix_model_provider_bindings_service.create(
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        return AixModelProviderBindingsAPIResponse(
            message="AixModelProviderBindings created successfully",
            data=created,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create aix_model_provider_bindings: {str(exc)}",
        ) from exc


@router.get(
    "/{record_id}",
    response_model=AixModelProviderBindingsAPIResponse,
    summary="Get AixModelProviderBindings",
    description="Retrieve a aix_model_provider_bindings by ID",
)
async def get_aix_model_provider_bindings(
    record_id: str,
    aix_model_provider_bindings_service: AixModelProviderBindingsService = Depends(
        get_aix_model_provider_bindings_service,
    ),
    current_user: dict = Depends(require_permission("aix_model_provider_bindings:read")),
):
    """Get aix_model_provider_bindings by ID."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        result = await aix_model_provider_bindings_service.get_by_id(record_id, tenant_id=tenant_id)
        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AixModelProviderBindings not found")
        return AixModelProviderBindingsAPIResponse(
            message="AixModelProviderBindings retrieved successfully",
            data=result,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve aix_model_provider_bindings: {str(exc)}",
        ) from exc


@router.get(
    "/",
    response_model=AixModelProviderBindingsListAPIResponse,
    summary="List aix model provider bindings",
    description="Retrieve a paginated list of aix model provider bindings",
)
async def list_aix_model_provider_bindings(
    limit: int = Query(default=100, ge=1, le=1000, description="Number of items to return"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
    aix_model_provider_bindings_service: AixModelProviderBindingsService = Depends(
        get_aix_model_provider_bindings_service,
    ),
    current_user: dict = Depends(require_permission("aix_model_provider_bindings:read")),
    project_id: str | None = Query(None, description="Filter by project_id"),
    binding_key: str | None = Query(None, description="Filter by binding_key"),
    provider: str | None = Query(None, description="Filter by provider"),
    model_id: str | None = Query(None, description="Filter by model_id"),
    model_name: str | None = Query(None, description="Filter by model_name"),
    route_class: str | None = Query(None, description="Filter by route_class"),
    is_enabled: bool | None = Query(None, description="Filter by is_enabled"),
    created_by: str | None = Query(None, description="Filter by created_by"),
    updated_by: str | None = Query(None, description="Filter by updated_by"),
    deleted_at: str | None = Query(None, description="Filter by deleted_at"),
):
    """List aix model provider bindings with pagination and filters."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        filters: dict[str, Any] = {}
        if project_id is not None:
            filters["project_id"] = project_id
        if binding_key is not None:
            filters["binding_key"] = binding_key
        if provider is not None:
            filters["provider"] = provider
        if model_id is not None:
            filters["model_id"] = model_id
        if model_name is not None:
            filters["model_name"] = model_name
        if route_class is not None:
            filters["route_class"] = route_class
        if is_enabled is not None:
            filters["is_enabled"] = is_enabled
        if created_by is not None:
            filters["created_by"] = created_by
        if updated_by is not None:
            filters["updated_by"] = updated_by
        if deleted_at is not None:
            filters["deleted_at"] = deleted_at
        records = await aix_model_provider_bindings_service.list(
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
            filters=filters or None,
        )
        return AixModelProviderBindingsListAPIResponse(
            message="aix model provider bindings retrieved successfully",
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
            detail=f"Failed to list aix model provider bindings records: {str(exc)}",
        ) from exc


@router.patch(
    "/{record_id}",
    response_model=AixModelProviderBindingsAPIResponse,
    summary="Update AixModelProviderBindings",
    description="Update an existing aix_model_provider_bindings",
)
async def update_aix_model_provider_bindings(
    record_id: str,
    data: AixModelProviderBindingsUpdate,
    aix_model_provider_bindings_service: AixModelProviderBindingsService = Depends(
        get_aix_model_provider_bindings_service,
    ),
    current_user: dict = Depends(require_permission("aix_model_provider_bindings:update")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Update a aix_model_provider_bindings record."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="update")
        updated = await aix_model_provider_bindings_service.update(
            record_id=record_id,
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AixModelProviderBindings not found")
        return AixModelProviderBindingsAPIResponse(
            message="AixModelProviderBindings updated successfully",
            data=updated,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update aix_model_provider_bindings: {str(exc)}",
        ) from exc


@router.delete(
    "/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete AixModelProviderBindings",
    description="Delete a aix_model_provider_bindings record",
)
async def delete_aix_model_provider_bindings(
    record_id: str,
    aix_model_provider_bindings_service: AixModelProviderBindingsService = Depends(
        get_aix_model_provider_bindings_service,
    ),
    current_user: dict = Depends(require_permission("aix_model_provider_bindings:delete")),
):
    """Delete a aix_model_provider_bindings."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        deleted = await aix_model_provider_bindings_service.delete(
            record_id=record_id,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
        )
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AixModelProviderBindings not found")
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete aix_model_provider_bindings: {str(exc)}",
        ) from exc
