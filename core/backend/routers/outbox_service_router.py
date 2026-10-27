"""
Outbox API Router
Generated FastAPI router with DAL-backed CRUD endpoints.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from backend.dependencies.outbox_service_dependencies import (
    ensure_valid_payload,
    get_outbox_service,
    get_pack_bundle,
    get_pack_context,
    require_permission,
    validate_tenant_access,
)
from backend.models.outbox_service_models import (
    OutboxCreate,
    OutboxUpdate,
)
from backend.models.outbox_service_models import (
    OutboxResponse as OutboxModel,
)
from backend.services.outbox_service import OutboxService
from backend.shared.hyeve_base_service import ServiceError
from hyeve_core.core_libs.types import PackBundle, PackContext

router = APIRouter(
    prefix="/api/outbox/audit",
    tags=["outbox"],
    responses={404: {"description": "Not found"}},
)


class OutboxAPIResponse(BaseModel):
    success: bool = True
    message: str = "Outbox operation completed successfully"
    data: OutboxModel | None = None


class OutboxListAPIResponse(BaseModel):
    success: bool = True
    message: str = "List retrieved successfully"
    data: list[OutboxModel] = []
    total: int = 0
    limit: int = 100
    offset: int = 0
    has_more: bool = False


@router.get(
    "/metadata/workflows",
    response_model=dict,
    summary="List outbox workflows",
    description="Expose workflow identifiers defined in the current pack bundle",
)
async def get_outbox_workflows(
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
    response_model=OutboxAPIResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Outbox",
    description="Create a new outbox record",
)
async def create_outbox(
    data: OutboxCreate,
    outbox_service: OutboxService = Depends(get_outbox_service),
    current_user: dict = Depends(require_permission("outbox:create")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Create a new outbox."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="create")
        created = await outbox_service.create(
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        return OutboxAPIResponse(
            message="Outbox created successfully",
            data=created,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create outbox: {str(exc)}",
        ) from exc


@router.get(
    "/{record_id}",
    response_model=OutboxAPIResponse,
    summary="Get Outbox",
    description="Retrieve a outbox by ID",
)
async def get_outbox(
    record_id: str,
    outbox_service: OutboxService = Depends(get_outbox_service),
    current_user: dict = Depends(require_permission("outbox:read")),
):
    """Get outbox by ID."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        result = await outbox_service.get_by_id(record_id, tenant_id=tenant_id)
        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outbox not found")
        return OutboxAPIResponse(
            message="Outbox retrieved successfully",
            data=result,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve outbox: {str(exc)}",
        ) from exc


@router.get(
    "/",
    response_model=OutboxListAPIResponse,
    summary="List outbox",
    description="Retrieve a paginated list of outbox",
)
async def list_outbox(
    limit: int = Query(default=100, ge=1, le=1000, description="Number of items to return"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
    outbox_service: OutboxService = Depends(get_outbox_service),
    current_user: dict = Depends(require_permission("outbox:read")),
    record_version: int | None = Query(None, description="Filter by record_version"),
    project_id: str | None = Query(None, description="Filter by project_id"),
    event_type: str | None = Query(None, description="Filter by event_type"),
    causation_id: str | None = Query(None, description="Filter by causation_id"),
    idempotency_key: str | None = Query(None, description="Filter by idempotency_key"),
    status_filter: str | None = Query(None, description="Filter by status"),
    published_at: str | None = Query(None, description="Filter by published_at"),
    created_by: str | None = Query(None, description="Filter by created_by"),
    updated_by: str | None = Query(None, description="Filter by updated_by"),
    deleted_at: str | None = Query(None, description="Filter by deleted_at"),
):
    """List outbox with pagination and filters."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        filters: dict[str, Any] = {}
        if record_version is not None:
            filters["record_version"] = record_version
        if project_id is not None:
            filters["project_id"] = project_id
        if event_type is not None:
            filters["event_type"] = event_type
        if causation_id is not None:
            filters["causation_id"] = causation_id
        if idempotency_key is not None:
            filters["idempotency_key"] = idempotency_key
        if status_filter is not None:
            filters["status"] = status_filter
        if published_at is not None:
            filters["published_at"] = published_at
        if created_by is not None:
            filters["created_by"] = created_by
        if updated_by is not None:
            filters["updated_by"] = updated_by
        if deleted_at is not None:
            filters["deleted_at"] = deleted_at
        records = await outbox_service.list(
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
            filters=filters or None,
        )
        return OutboxListAPIResponse(
            message="outbox retrieved successfully",
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
            detail=f"Failed to list outbox records: {str(exc)}",
        ) from exc


@router.patch(
    "/{record_id}",
    response_model=OutboxAPIResponse,
    summary="Update Outbox",
    description="Update an existing outbox",
)
async def update_outbox(
    record_id: str,
    data: OutboxUpdate,
    outbox_service: OutboxService = Depends(get_outbox_service),
    current_user: dict = Depends(require_permission("outbox:update")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Update a outbox record."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="update")
        updated = await outbox_service.update(
            record_id=record_id,
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outbox not found")
        return OutboxAPIResponse(
            message="Outbox updated successfully",
            data=updated,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update outbox: {str(exc)}",
        ) from exc


@router.delete(
    "/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Outbox",
    description="Delete a outbox record",
)
async def delete_outbox(
    record_id: str,
    outbox_service: OutboxService = Depends(get_outbox_service),
    current_user: dict = Depends(require_permission("outbox:delete")),
):
    """Delete a outbox."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        deleted = await outbox_service.delete(
            record_id=record_id,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
        )
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outbox not found")
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete outbox: {str(exc)}",
        ) from exc
