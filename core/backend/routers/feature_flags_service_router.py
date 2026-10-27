"""
FeatureFlags API Router
Generated FastAPI router with DAL-backed CRUD endpoints.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from backend.dependencies.feature_flags_service_dependencies import (
    ensure_valid_payload,
    get_feature_flags_service,
    get_pack_bundle,
    get_pack_context,
    require_permission,
    validate_tenant_access,
)
from backend.models.feature_flags_service_models import (
    FeatureFlagsCreate,
    FeatureFlagsUpdate,
)
from backend.models.feature_flags_service_models import (
    FeatureFlagsResponse as FeatureFlagsModel,
)
from backend.services.feature_flags_service import FeatureFlagsService
from backend.shared.hyeve_base_service import ServiceError
from hyeve_core.core_libs.types import PackBundle, PackContext

router = APIRouter(
    prefix="/api/feature-flags/audit",
    tags=["feature flags"],
    responses={404: {"description": "Not found"}},
)


class FeatureFlagsAPIResponse(BaseModel):
    success: bool = True
    message: str = "FeatureFlags operation completed successfully"
    data: FeatureFlagsModel | None = None


class FeatureFlagsListAPIResponse(BaseModel):
    success: bool = True
    message: str = "List retrieved successfully"
    data: list[FeatureFlagsModel] = []
    total: int = 0
    limit: int = 100
    offset: int = 0
    has_more: bool = False


@router.get(
    "/metadata/workflows",
    response_model=dict,
    summary="List feature_flags workflows",
    description="Expose workflow identifiers defined in the current pack bundle",
)
async def get_feature_flags_workflows(
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
    response_model=FeatureFlagsAPIResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create FeatureFlags",
    description="Create a new feature_flags record",
)
async def create_feature_flags(
    data: FeatureFlagsCreate,
    feature_flags_service: FeatureFlagsService = Depends(get_feature_flags_service),
    current_user: dict = Depends(require_permission("feature_flags:create")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Create a new feature_flags."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="create")
        created = await feature_flags_service.create(
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        return FeatureFlagsAPIResponse(
            message="FeatureFlags created successfully",
            data=created,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create feature_flags: {str(exc)}",
        ) from exc


@router.get(
    "/{record_id}",
    response_model=FeatureFlagsAPIResponse,
    summary="Get FeatureFlags",
    description="Retrieve a feature_flags by ID",
)
async def get_feature_flags(
    record_id: str,
    feature_flags_service: FeatureFlagsService = Depends(get_feature_flags_service),
    current_user: dict = Depends(require_permission("feature_flags:read")),
):
    """Get feature_flags by ID."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        result = await feature_flags_service.get_by_id(record_id, tenant_id=tenant_id)
        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="FeatureFlags not found")
        return FeatureFlagsAPIResponse(
            message="FeatureFlags retrieved successfully",
            data=result,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve feature_flags: {str(exc)}",
        ) from exc


@router.get(
    "/",
    response_model=FeatureFlagsListAPIResponse,
    summary="List feature flags",
    description="Retrieve a paginated list of feature flags",
)
async def list_feature_flags(
    limit: int = Query(default=100, ge=1, le=1000, description="Number of items to return"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
    feature_flags_service: FeatureFlagsService = Depends(get_feature_flags_service),
    current_user: dict = Depends(require_permission("feature_flags:read")),
    record_version: int | None = Query(None, description="Filter by record_version"),
    project_id: str | None = Query(None, description="Filter by project_id"),
    flag_key: str | None = Query(None, description="Filter by flag_key"),
    enabled: bool | None = Query(None, description="Filter by enabled"),
    created_by: str | None = Query(None, description="Filter by created_by"),
    updated_by: str | None = Query(None, description="Filter by updated_by"),
    deleted_at: str | None = Query(None, description="Filter by deleted_at"),
):
    """List feature flags with pagination and filters."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        filters: dict[str, Any] = {}
        if record_version is not None:
            filters["record_version"] = record_version
        if project_id is not None:
            filters["project_id"] = project_id
        if flag_key is not None:
            filters["flag_key"] = flag_key
        if enabled is not None:
            filters["enabled"] = enabled
        if created_by is not None:
            filters["created_by"] = created_by
        if updated_by is not None:
            filters["updated_by"] = updated_by
        if deleted_at is not None:
            filters["deleted_at"] = deleted_at
        records = await feature_flags_service.list(
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
            filters=filters or None,
        )
        return FeatureFlagsListAPIResponse(
            message="feature flags retrieved successfully",
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
            detail=f"Failed to list feature flags records: {str(exc)}",
        ) from exc


@router.patch(
    "/{record_id}",
    response_model=FeatureFlagsAPIResponse,
    summary="Update FeatureFlags",
    description="Update an existing feature_flags",
)
async def update_feature_flags(
    record_id: str,
    data: FeatureFlagsUpdate,
    feature_flags_service: FeatureFlagsService = Depends(get_feature_flags_service),
    current_user: dict = Depends(require_permission("feature_flags:update")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Update a feature_flags record."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="update")
        updated = await feature_flags_service.update(
            record_id=record_id,
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="FeatureFlags not found")
        return FeatureFlagsAPIResponse(
            message="FeatureFlags updated successfully",
            data=updated,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update feature_flags: {str(exc)}",
        ) from exc


@router.delete(
    "/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete FeatureFlags",
    description="Delete a feature_flags record",
)
async def delete_feature_flags(
    record_id: str,
    feature_flags_service: FeatureFlagsService = Depends(get_feature_flags_service),
    current_user: dict = Depends(require_permission("feature_flags:delete")),
):
    """Delete a feature_flags."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        deleted = await feature_flags_service.delete(
            record_id=record_id,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
        )
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="FeatureFlags not found")
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete feature_flags: {str(exc)}",
        ) from exc
