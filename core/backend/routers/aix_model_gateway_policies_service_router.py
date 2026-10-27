"""
AixModelGatewayPolicies API Router
Generated FastAPI router with DAL-backed CRUD endpoints.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from backend.dependencies.aix_model_gateway_policies_service_dependencies import (
    ensure_valid_payload,
    get_aix_model_gateway_policies_service,
    get_pack_bundle,
    get_pack_context,
    require_permission,
    validate_tenant_access,
)
from backend.models.aix_model_gateway_policies_service_models import (
    AixModelGatewayPoliciesCreate,
    AixModelGatewayPoliciesUpdate,
)
from backend.models.aix_model_gateway_policies_service_models import (
    AixModelGatewayPoliciesResponse as AixModelGatewayPoliciesModel,
)
from backend.services.aix_model_gateway_policies_service import AixModelGatewayPoliciesService
from backend.shared.hyeve_base_service import ServiceError
from hyeve_core.core_libs.types import PackBundle, PackContext

router = APIRouter(
    prefix="/api/aix-model-gateway-policies/audit",
    tags=["aix model gateway policies"],
    responses={404: {"description": "Not found"}},
)


class AixModelGatewayPoliciesAPIResponse(BaseModel):
    success: bool = True
    message: str = "AixModelGatewayPolicies operation completed successfully"
    data: AixModelGatewayPoliciesModel | None = None


class AixModelGatewayPoliciesListAPIResponse(BaseModel):
    success: bool = True
    message: str = "List retrieved successfully"
    data: list[AixModelGatewayPoliciesModel] = []
    total: int = 0
    limit: int = 100
    offset: int = 0
    has_more: bool = False


@router.get(
    "/metadata/workflows",
    response_model=dict,
    summary="List aix_model_gateway_policies workflows",
    description="Expose workflow identifiers defined in the current pack bundle",
)
async def get_aix_model_gateway_policies_workflows(
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
    response_model=AixModelGatewayPoliciesAPIResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create AixModelGatewayPolicies",
    description="Create a new aix_model_gateway_policies record",
)
async def create_aix_model_gateway_policies(
    data: AixModelGatewayPoliciesCreate,
    aix_model_gateway_policies_service: AixModelGatewayPoliciesService = Depends(
        get_aix_model_gateway_policies_service,
    ),
    current_user: dict = Depends(require_permission("aix_model_gateway_policies:create")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Create a new aix_model_gateway_policies."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="create")
        created = await aix_model_gateway_policies_service.create(
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        return AixModelGatewayPoliciesAPIResponse(
            message="AixModelGatewayPolicies created successfully",
            data=created,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create aix_model_gateway_policies: {str(exc)}",
        ) from exc


@router.get(
    "/{record_id}",
    response_model=AixModelGatewayPoliciesAPIResponse,
    summary="Get AixModelGatewayPolicies",
    description="Retrieve a aix_model_gateway_policies by ID",
)
async def get_aix_model_gateway_policies(
    record_id: str,
    aix_model_gateway_policies_service: AixModelGatewayPoliciesService = Depends(
        get_aix_model_gateway_policies_service,
    ),
    current_user: dict = Depends(require_permission("aix_model_gateway_policies:read")),
):
    """Get aix_model_gateway_policies by ID."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        result = await aix_model_gateway_policies_service.get_by_id(record_id, tenant_id=tenant_id)
        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AixModelGatewayPolicies not found")
        return AixModelGatewayPoliciesAPIResponse(
            message="AixModelGatewayPolicies retrieved successfully",
            data=result,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve aix_model_gateway_policies: {str(exc)}",
        ) from exc


@router.get(
    "/",
    response_model=AixModelGatewayPoliciesListAPIResponse,
    summary="List aix model gateway policies",
    description="Retrieve a paginated list of aix model gateway policies",
)
async def list_aix_model_gateway_policies(
    limit: int = Query(default=100, ge=1, le=1000, description="Number of items to return"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
    aix_model_gateway_policies_service: AixModelGatewayPoliciesService = Depends(
        get_aix_model_gateway_policies_service,
    ),
    current_user: dict = Depends(require_permission("aix_model_gateway_policies:read")),
    project_id: str | None = Query(None, description="Filter by project_id"),
    policy_key: str | None = Query(None, description="Filter by policy_key"),
    task_type: str | None = Query(None, description="Filter by task_type"),
    risk_level: str | None = Query(None, description="Filter by risk_level"),
    model_policy: str | None = Query(None, description="Filter by model_policy"),
    default_provider: str | None = Query(None, description="Filter by default_provider"),
    escalation_provider: str | None = Query(None, description="Filter by escalation_provider"),
    max_retry_before_escalate: int | None = Query(None, description="Filter by max_retry_before_escalate"),
    status_filter: str | None = Query(None, description="Filter by status"),
    created_by: str | None = Query(None, description="Filter by created_by"),
    updated_by: str | None = Query(None, description="Filter by updated_by"),
    deleted_at: str | None = Query(None, description="Filter by deleted_at"),
):
    """List aix model gateway policies with pagination and filters."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        filters: dict[str, Any] = {}
        if project_id is not None:
            filters["project_id"] = project_id
        if policy_key is not None:
            filters["policy_key"] = policy_key
        if task_type is not None:
            filters["task_type"] = task_type
        if risk_level is not None:
            filters["risk_level"] = risk_level
        if model_policy is not None:
            filters["model_policy"] = model_policy
        if default_provider is not None:
            filters["default_provider"] = default_provider
        if escalation_provider is not None:
            filters["escalation_provider"] = escalation_provider
        if max_retry_before_escalate is not None:
            filters["max_retry_before_escalate"] = max_retry_before_escalate
        if status_filter is not None:
            filters["status"] = status_filter
        if created_by is not None:
            filters["created_by"] = created_by
        if updated_by is not None:
            filters["updated_by"] = updated_by
        if deleted_at is not None:
            filters["deleted_at"] = deleted_at
        records = await aix_model_gateway_policies_service.list(
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
            filters=filters or None,
        )
        return AixModelGatewayPoliciesListAPIResponse(
            message="aix model gateway policies retrieved successfully",
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
            detail=f"Failed to list aix model gateway policies records: {str(exc)}",
        ) from exc


@router.patch(
    "/{record_id}",
    response_model=AixModelGatewayPoliciesAPIResponse,
    summary="Update AixModelGatewayPolicies",
    description="Update an existing aix_model_gateway_policies",
)
async def update_aix_model_gateway_policies(
    record_id: str,
    data: AixModelGatewayPoliciesUpdate,
    aix_model_gateway_policies_service: AixModelGatewayPoliciesService = Depends(
        get_aix_model_gateway_policies_service,
    ),
    current_user: dict = Depends(require_permission("aix_model_gateway_policies:update")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Update a aix_model_gateway_policies record."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="update")
        updated = await aix_model_gateway_policies_service.update(
            record_id=record_id,
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AixModelGatewayPolicies not found")
        return AixModelGatewayPoliciesAPIResponse(
            message="AixModelGatewayPolicies updated successfully",
            data=updated,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update aix_model_gateway_policies: {str(exc)}",
        ) from exc


@router.delete(
    "/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete AixModelGatewayPolicies",
    description="Delete a aix_model_gateway_policies record",
)
async def delete_aix_model_gateway_policies(
    record_id: str,
    aix_model_gateway_policies_service: AixModelGatewayPoliciesService = Depends(
        get_aix_model_gateway_policies_service,
    ),
    current_user: dict = Depends(require_permission("aix_model_gateway_policies:delete")),
):
    """Delete a aix_model_gateway_policies."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        deleted = await aix_model_gateway_policies_service.delete(
            record_id=record_id,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
        )
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AixModelGatewayPolicies not found")
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete aix_model_gateway_policies: {str(exc)}",
        ) from exc
