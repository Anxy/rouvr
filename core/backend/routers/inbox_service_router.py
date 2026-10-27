"""
Inbox API Router
Generated FastAPI router with DAL-backed CRUD endpoints.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from backend.dependencies.inbox_service_dependencies import (
    ensure_valid_payload,
    get_inbox_service,
    get_pack_bundle,
    get_pack_context,
    require_permission,
    validate_tenant_access,
)
from backend.models.inbox_service_models import (
    InboxCreate,
    InboxUpdate,
)
from backend.models.inbox_service_models import (
    InboxResponse as InboxModel,
)
from backend.services.inbox_service import InboxService
from backend.shared.hyeve_base_service import ServiceError
from hyeve_core.core_libs.types import PackBundle, PackContext

router = APIRouter(
    prefix="/api/inbox/audit",
    tags=["inbox"],
    responses={404: {"description": "Not found"}},
)


class InboxAPIResponse(BaseModel):
    success: bool = True
    message: str = "Inbox operation completed successfully"
    data: InboxModel | None = None


class InboxListAPIResponse(BaseModel):
    success: bool = True
    message: str = "List retrieved successfully"
    data: list[InboxModel] = []
    total: int = 0
    limit: int = 100
    offset: int = 0
    has_more: bool = False


@router.get(
    "/metadata/workflows",
    response_model=dict,
    summary="List inbox workflows",
    description="Expose workflow identifiers defined in the current pack bundle",
)
async def get_inbox_workflows(
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
    response_model=InboxAPIResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Inbox",
    description="Create a new inbox record",
)
async def create_inbox(
    data: InboxCreate,
    inbox_service: InboxService = Depends(get_inbox_service),
    current_user: dict = Depends(require_permission("inbox:create")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Create a new inbox."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="create")
        created = await inbox_service.create(
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        return InboxAPIResponse(
            message="Inbox created successfully",
            data=created,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create inbox: {str(exc)}",
        ) from exc


@router.get(
    "/{record_id}",
    response_model=InboxAPIResponse,
    summary="Get Inbox",
    description="Retrieve a inbox by ID",
)
async def get_inbox(
    record_id: str,
    inbox_service: InboxService = Depends(get_inbox_service),
    current_user: dict = Depends(require_permission("inbox:read")),
):
    """Get inbox by ID."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        result = await inbox_service.get_by_id(record_id, tenant_id=tenant_id)
        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inbox not found")
        return InboxAPIResponse(
            message="Inbox retrieved successfully",
            data=result,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve inbox: {str(exc)}",
        ) from exc


@router.get(
    "/",
    response_model=InboxListAPIResponse,
    summary="List inbox",
    description="Retrieve a paginated list of inbox",
)
async def list_inbox(
    limit: int = Query(default=100, ge=1, le=1000, description="Number of items to return"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
    inbox_service: InboxService = Depends(get_inbox_service),
    current_user: dict = Depends(require_permission("inbox:read")),
    record_version: int | None = Query(None, description="Filter by record_version"),
    project_id: str | None = Query(None, description="Filter by project_id"),
    event_type: str | None = Query(None, description="Filter by event_type"),
    causation_id: str | None = Query(None, description="Filter by causation_id"),
    processing_status: str | None = Query(None, description="Filter by processing_status"),
    error_message: str | None = Query(None, description="Filter by error_message"),
    processed_at: str | None = Query(None, description="Filter by processed_at"),
    created_by: str | None = Query(None, description="Filter by created_by"),
    updated_by: str | None = Query(None, description="Filter by updated_by"),
    deleted_at: str | None = Query(None, description="Filter by deleted_at"),
):
    """List inbox with pagination and filters."""
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
        if processing_status is not None:
            filters["processing_status"] = processing_status
        if error_message is not None:
            filters["error_message"] = error_message
        if processed_at is not None:
            filters["processed_at"] = processed_at
        if created_by is not None:
            filters["created_by"] = created_by
        if updated_by is not None:
            filters["updated_by"] = updated_by
        if deleted_at is not None:
            filters["deleted_at"] = deleted_at
        records = await inbox_service.list(
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
            filters=filters or None,
        )
        return InboxListAPIResponse(
            message="inbox retrieved successfully",
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
            detail=f"Failed to list inbox records: {str(exc)}",
        ) from exc


@router.patch(
    "/{record_id}",
    response_model=InboxAPIResponse,
    summary="Update Inbox",
    description="Update an existing inbox",
)
async def update_inbox(
    record_id: str,
    data: InboxUpdate,
    inbox_service: InboxService = Depends(get_inbox_service),
    current_user: dict = Depends(require_permission("inbox:update")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Update a inbox record."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="update")
        updated = await inbox_service.update(
            record_id=record_id,
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inbox not found")
        return InboxAPIResponse(
            message="Inbox updated successfully",
            data=updated,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update inbox: {str(exc)}",
        ) from exc


@router.delete(
    "/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Inbox",
    description="Delete a inbox record",
)
async def delete_inbox(
    record_id: str,
    inbox_service: InboxService = Depends(get_inbox_service),
    current_user: dict = Depends(require_permission("inbox:delete")),
):
    """Delete a inbox."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        deleted = await inbox_service.delete(
            record_id=record_id,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
        )
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inbox not found")
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete inbox: {str(exc)}",
        ) from exc
