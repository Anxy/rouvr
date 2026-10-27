"""
IdempotencyKeys API Router
Generated FastAPI router with DAL-backed CRUD endpoints.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from backend.dependencies.idempotency_keys_service_dependencies import (
    ensure_valid_payload,
    get_idempotency_keys_service,
    get_pack_bundle,
    get_pack_context,
    require_permission,
    validate_tenant_access,
)
from backend.models.idempotency_keys_service_models import (
    IdempotencyKeysCreate,
    IdempotencyKeysUpdate,
)
from backend.models.idempotency_keys_service_models import (
    IdempotencyKeysResponse as IdempotencyKeysModel,
)
from backend.services.idempotency_keys_service import IdempotencyKeysService
from backend.shared.hyeve_base_service import ServiceError
from hyeve_core.core_libs.types import PackBundle, PackContext

router = APIRouter(
    prefix="/api/idempotency-keys/audit",
    tags=["idempotency keys"],
    responses={404: {"description": "Not found"}},
)


class IdempotencyKeysAPIResponse(BaseModel):
    success: bool = True
    message: str = "IdempotencyKeys operation completed successfully"
    data: IdempotencyKeysModel | None = None


class IdempotencyKeysListAPIResponse(BaseModel):
    success: bool = True
    message: str = "List retrieved successfully"
    data: list[IdempotencyKeysModel] = []
    total: int = 0
    limit: int = 100
    offset: int = 0
    has_more: bool = False


@router.get(
    "/metadata/workflows",
    response_model=dict,
    summary="List idempotency_keys workflows",
    description="Expose workflow identifiers defined in the current pack bundle",
)
async def get_idempotency_keys_workflows(
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
    response_model=IdempotencyKeysAPIResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create IdempotencyKeys",
    description="Create a new idempotency_keys record",
)
async def create_idempotency_keys(
    data: IdempotencyKeysCreate,
    idempotency_keys_service: IdempotencyKeysService = Depends(get_idempotency_keys_service),
    current_user: dict = Depends(require_permission("idempotency_keys:create")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Create a new idempotency_keys."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="create")
        created = await idempotency_keys_service.create(
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        return IdempotencyKeysAPIResponse(
            message="IdempotencyKeys created successfully",
            data=created,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create idempotency_keys: {str(exc)}",
        ) from exc


@router.get(
    "/{record_id}",
    response_model=IdempotencyKeysAPIResponse,
    summary="Get IdempotencyKeys",
    description="Retrieve a idempotency_keys by ID",
)
async def get_idempotency_keys(
    record_id: str,
    idempotency_keys_service: IdempotencyKeysService = Depends(get_idempotency_keys_service),
    current_user: dict = Depends(require_permission("idempotency_keys:read")),
):
    """Get idempotency_keys by ID."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        result = await idempotency_keys_service.get_by_id(record_id, tenant_id=tenant_id)
        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IdempotencyKeys not found")
        return IdempotencyKeysAPIResponse(
            message="IdempotencyKeys retrieved successfully",
            data=result,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve idempotency_keys: {str(exc)}",
        ) from exc


@router.get(
    "/",
    response_model=IdempotencyKeysListAPIResponse,
    summary="List idempotency keys",
    description="Retrieve a paginated list of idempotency keys",
)
async def list_idempotency_keys(
    limit: int = Query(default=100, ge=1, le=1000, description="Number of items to return"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
    idempotency_keys_service: IdempotencyKeysService = Depends(get_idempotency_keys_service),
    current_user: dict = Depends(require_permission("idempotency_keys:read")),
    record_version: int | None = Query(None, description="Filter by record_version"),
    project_id: str | None = Query(None, description="Filter by project_id"),
    idempotency_key: str | None = Query(None, description="Filter by idempotency_key"),
    request_hash: str | None = Query(None, description="Filter by request_hash"),
    status_code: int | None = Query(None, description="Filter by status_code"),
    expires_at: str | None = Query(None, description="Filter by expires_at"),
    created_by: str | None = Query(None, description="Filter by created_by"),
    updated_by: str | None = Query(None, description="Filter by updated_by"),
    deleted_at: str | None = Query(None, description="Filter by deleted_at"),
):
    """List idempotency keys with pagination and filters."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        filters: dict[str, Any] = {}
        if record_version is not None:
            filters["record_version"] = record_version
        if project_id is not None:
            filters["project_id"] = project_id
        if idempotency_key is not None:
            filters["idempotency_key"] = idempotency_key
        if request_hash is not None:
            filters["request_hash"] = request_hash
        if status_code is not None:
            filters["status_code"] = status_code
        if expires_at is not None:
            filters["expires_at"] = expires_at
        if created_by is not None:
            filters["created_by"] = created_by
        if updated_by is not None:
            filters["updated_by"] = updated_by
        if deleted_at is not None:
            filters["deleted_at"] = deleted_at
        records = await idempotency_keys_service.list(
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
            filters=filters or None,
        )
        return IdempotencyKeysListAPIResponse(
            message="idempotency keys retrieved successfully",
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
            detail=f"Failed to list idempotency keys records: {str(exc)}",
        ) from exc


@router.patch(
    "/{record_id}",
    response_model=IdempotencyKeysAPIResponse,
    summary="Update IdempotencyKeys",
    description="Update an existing idempotency_keys",
)
async def update_idempotency_keys(
    record_id: str,
    data: IdempotencyKeysUpdate,
    idempotency_keys_service: IdempotencyKeysService = Depends(get_idempotency_keys_service),
    current_user: dict = Depends(require_permission("idempotency_keys:update")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Update a idempotency_keys record."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="update")
        updated = await idempotency_keys_service.update(
            record_id=record_id,
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IdempotencyKeys not found")
        return IdempotencyKeysAPIResponse(
            message="IdempotencyKeys updated successfully",
            data=updated,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update idempotency_keys: {str(exc)}",
        ) from exc


@router.delete(
    "/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete IdempotencyKeys",
    description="Delete a idempotency_keys record",
)
async def delete_idempotency_keys(
    record_id: str,
    idempotency_keys_service: IdempotencyKeysService = Depends(get_idempotency_keys_service),
    current_user: dict = Depends(require_permission("idempotency_keys:delete")),
):
    """Delete a idempotency_keys."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        deleted = await idempotency_keys_service.delete(
            record_id=record_id,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
        )
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IdempotencyKeys not found")
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete idempotency_keys: {str(exc)}",
        ) from exc
