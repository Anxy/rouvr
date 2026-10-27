"""
AixEscalationPolicies API Router
Generated FastAPI router with DAL-backed CRUD endpoints.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from backend.dependencies.aix_escalation_policies_service_dependencies import (
    ensure_valid_payload,
    get_aix_escalation_policies_service,
    get_pack_bundle,
    get_pack_context,
    require_permission,
    validate_tenant_access,
)
from backend.models.aix_escalation_policies_service_models import (
    AixEscalationPoliciesCreate,
    AixEscalationPoliciesUpdate,
)
from backend.models.aix_escalation_policies_service_models import (
    AixEscalationPoliciesResponse as AixEscalationPoliciesModel,
)
from backend.services.aix_escalation_policies_service import AixEscalationPoliciesService
from backend.shared.hyeve_base_service import ServiceError
from hyeve_core.core_libs.types import PackBundle, PackContext

router = APIRouter(
    prefix="/api/aix-escalation-policies/audit",
    tags=["aix escalation policies"],
    responses={404: {"description": "Not found"}},
)


class AixEscalationPoliciesAPIResponse(BaseModel):
    success: bool = True
    message: str = "AixEscalationPolicies operation completed successfully"
    data: AixEscalationPoliciesModel | None = None


class AixEscalationPoliciesListAPIResponse(BaseModel):
    success: bool = True
    message: str = "List retrieved successfully"
    data: list[AixEscalationPoliciesModel] = []
    total: int = 0
    limit: int = 100
    offset: int = 0
    has_more: bool = False


@router.get(
    "/metadata/workflows",
    response_model=dict,
    summary="List aix_escalation_policies workflows",
    description="Expose workflow identifiers defined in the current pack bundle",
)
async def get_aix_escalation_policies_workflows(
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
    response_model=AixEscalationPoliciesAPIResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create AixEscalationPolicies",
    description="Create a new aix_escalation_policies record",
)
async def create_aix_escalation_policies(
    data: AixEscalationPoliciesCreate,
    aix_escalation_policies_service: AixEscalationPoliciesService = Depends(get_aix_escalation_policies_service),
    current_user: dict = Depends(require_permission("aix_escalation_policies:create")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Create a new aix_escalation_policies."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="create")
        created = await aix_escalation_policies_service.create(
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        return AixEscalationPoliciesAPIResponse(
            message="AixEscalationPolicies created successfully",
            data=created,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create aix_escalation_policies: {str(exc)}",
        ) from exc


@router.get(
    "/{record_id}",
    response_model=AixEscalationPoliciesAPIResponse,
    summary="Get AixEscalationPolicies",
    description="Retrieve a aix_escalation_policies by ID",
)
async def get_aix_escalation_policies(
    record_id: str,
    aix_escalation_policies_service: AixEscalationPoliciesService = Depends(get_aix_escalation_policies_service),
    current_user: dict = Depends(require_permission("aix_escalation_policies:read")),
):
    """Get aix_escalation_policies by ID."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        result = await aix_escalation_policies_service.get_by_id(record_id, tenant_id=tenant_id)
        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AixEscalationPolicies not found")
        return AixEscalationPoliciesAPIResponse(
            message="AixEscalationPolicies retrieved successfully",
            data=result,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve aix_escalation_policies: {str(exc)}",
        ) from exc


@router.get(
    "/",
    response_model=AixEscalationPoliciesListAPIResponse,
    summary="List aix escalation policies",
    description="Retrieve a paginated list of aix escalation policies",
)
async def list_aix_escalation_policies(
    limit: int = Query(default=100, ge=1, le=1000, description="Number of items to return"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
    aix_escalation_policies_service: AixEscalationPoliciesService = Depends(get_aix_escalation_policies_service),
    current_user: dict = Depends(require_permission("aix_escalation_policies:read")),
    project_id: str | None = Query(None, description="Filter by project_id"),
    policy_key: str | None = Query(None, description="Filter by policy_key"),
    task_type: str | None = Query(None, description="Filter by task_type"),
    risk_level: str | None = Query(None, description="Filter by risk_level"),
    retry_threshold: int | None = Query(None, description="Filter by retry_threshold"),
    escalate_to_provider: str | None = Query(None, description="Filter by escalate_to_provider"),
    block_on_provider_unhealthy: bool | None = Query(None, description="Filter by block_on_provider_unhealthy"),
    status_filter: str | None = Query(None, description="Filter by status"),
    created_by: str | None = Query(None, description="Filter by created_by"),
    updated_by: str | None = Query(None, description="Filter by updated_by"),
    deleted_at: str | None = Query(None, description="Filter by deleted_at"),
):
    """List aix escalation policies with pagination and filters."""
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
        if retry_threshold is not None:
            filters["retry_threshold"] = retry_threshold
        if escalate_to_provider is not None:
            filters["escalate_to_provider"] = escalate_to_provider
        if block_on_provider_unhealthy is not None:
            filters["block_on_provider_unhealthy"] = block_on_provider_unhealthy
        if status_filter is not None:
            filters["status"] = status_filter
        if created_by is not None:
            filters["created_by"] = created_by
        if updated_by is not None:
            filters["updated_by"] = updated_by
        if deleted_at is not None:
            filters["deleted_at"] = deleted_at
        records = await aix_escalation_policies_service.list(
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
            filters=filters or None,
        )
        return AixEscalationPoliciesListAPIResponse(
            message="aix escalation policies retrieved successfully",
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
            detail=f"Failed to list aix escalation policies records: {str(exc)}",
        ) from exc


@router.patch(
    "/{record_id}",
    response_model=AixEscalationPoliciesAPIResponse,
    summary="Update AixEscalationPolicies",
    description="Update an existing aix_escalation_policies",
)
async def update_aix_escalation_policies(
    record_id: str,
    data: AixEscalationPoliciesUpdate,
    aix_escalation_policies_service: AixEscalationPoliciesService = Depends(get_aix_escalation_policies_service),
    current_user: dict = Depends(require_permission("aix_escalation_policies:update")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Update a aix_escalation_policies record."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="update")
        updated = await aix_escalation_policies_service.update(
            record_id=record_id,
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AixEscalationPolicies not found")
        return AixEscalationPoliciesAPIResponse(
            message="AixEscalationPolicies updated successfully",
            data=updated,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update aix_escalation_policies: {str(exc)}",
        ) from exc


@router.delete(
    "/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete AixEscalationPolicies",
    description="Delete a aix_escalation_policies record",
)
async def delete_aix_escalation_policies(
    record_id: str,
    aix_escalation_policies_service: AixEscalationPoliciesService = Depends(get_aix_escalation_policies_service),
    current_user: dict = Depends(require_permission("aix_escalation_policies:delete")),
):
    """Delete a aix_escalation_policies."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        deleted = await aix_escalation_policies_service.delete(
            record_id=record_id,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
        )
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AixEscalationPolicies not found")
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete aix_escalation_policies: {str(exc)}",
        ) from exc
