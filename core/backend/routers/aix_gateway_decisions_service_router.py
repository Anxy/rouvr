"""
AixGatewayDecisions API Router
Generated FastAPI router with DAL-backed CRUD endpoints.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from backend.dependencies.aix_gateway_decisions_service_dependencies import (
    ensure_valid_payload,
    get_aix_gateway_decisions_service,
    get_pack_bundle,
    get_pack_context,
    require_permission,
    validate_tenant_access,
)
from backend.models.aix_gateway_decisions_service_models import (
    AixGatewayDecisionsCreate,
    AixGatewayDecisionsUpdate,
)
from backend.models.aix_gateway_decisions_service_models import (
    AixGatewayDecisionsResponse as AixGatewayDecisionsModel,
)
from backend.services.aix_gateway_decisions_service import AixGatewayDecisionsService
from backend.shared.hyeve_base_service import ServiceError
from hyeve_core.core_libs.types import PackBundle, PackContext

router = APIRouter(
    prefix="/api/aix/model-gateway-policies/audit",
    tags=["aix gateway decisions"],
    responses={404: {"description": "Not found"}},
)


class AixGatewayDecisionsAPIResponse(BaseModel):
    success: bool = True
    message: str = "AixGatewayDecisions operation completed successfully"
    data: AixGatewayDecisionsModel | None = None


class AixGatewayDecisionsListAPIResponse(BaseModel):
    success: bool = True
    message: str = "List retrieved successfully"
    data: list[AixGatewayDecisionsModel] = []
    total: int = 0
    limit: int = 100
    offset: int = 0
    has_more: bool = False


@router.get(
    "/metadata/workflows",
    response_model=dict,
    summary="List aix_gateway_decisions workflows",
    description="Expose workflow identifiers defined in the current pack bundle",
)
async def get_aix_gateway_decisions_workflows(
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
    response_model=AixGatewayDecisionsAPIResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create AixGatewayDecisions",
    description="Create a new aix_gateway_decisions record",
)
async def create_aix_gateway_decisions(
    data: AixGatewayDecisionsCreate,
    aix_gateway_decisions_service: AixGatewayDecisionsService = Depends(get_aix_gateway_decisions_service),
    current_user: dict = Depends(require_permission("aix_gateway_decisions:create")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Create a new aix_gateway_decisions."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="create")
        created = await aix_gateway_decisions_service.create(
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        return AixGatewayDecisionsAPIResponse(
            message="AixGatewayDecisions created successfully",
            data=created,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create aix_gateway_decisions: {str(exc)}",
        ) from exc


@router.get(
    "/{record_id}",
    response_model=AixGatewayDecisionsAPIResponse,
    summary="Get AixGatewayDecisions",
    description="Retrieve a aix_gateway_decisions by ID",
)
async def get_aix_gateway_decisions(
    record_id: str,
    aix_gateway_decisions_service: AixGatewayDecisionsService = Depends(get_aix_gateway_decisions_service),
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    """Get aix_gateway_decisions by ID."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        result = await aix_gateway_decisions_service.get_by_id(record_id, tenant_id=tenant_id)
        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AixGatewayDecisions not found")
        return AixGatewayDecisionsAPIResponse(
            message="AixGatewayDecisions retrieved successfully",
            data=result,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve aix_gateway_decisions: {str(exc)}",
        ) from exc


@router.get(
    "/",
    response_model=AixGatewayDecisionsListAPIResponse,
    summary="List aix gateway decisions",
    description="Retrieve a paginated list of aix gateway decisions",
)
async def list_aix_gateway_decisions(
    limit: int = Query(default=100, ge=1, le=1000, description="Number of items to return"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
    aix_gateway_decisions_service: AixGatewayDecisionsService = Depends(get_aix_gateway_decisions_service),
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
    project_id: str | None = Query(None, description="Filter by project_id"),
    workhub_task_id: str | None = Query(None, description="Filter by workhub_task_id"),
    workhub_task_number: int | None = Query(None, description="Filter by workhub_task_number"),
    opsbus_run_id: str | None = Query(None, description="Filter by opsbus_run_id"),
    task_type: str | None = Query(None, description="Filter by task_type"),
    risk_level: str | None = Query(None, description="Filter by risk_level"),
    model_policy: str | None = Query(None, description="Filter by model_policy"),
    selected_provider: str | None = Query(None, description="Filter by selected_provider"),
    selected_model_id: str | None = Query(None, description="Filter by selected_model_id"),
    selected_model_name: str | None = Query(None, description="Filter by selected_model_name"),
    decision_status: str | None = Query(None, description="Filter by decision_status"),
    decision_reason: str | None = Query(None, description="Filter by decision_reason"),
    retry_count: int | None = Query(None, description="Filter by retry_count"),
    escalation_attempted: bool | None = Query(None, description="Filter by escalation_attempted"),
    latency_ms: int | None = Query(None, description="Filter by latency_ms"),
    tokens_used: int | None = Query(None, description="Filter by tokens_used"),
    estimated_cost: str | None = Query(None, description="Filter by estimated_cost"),
    created_by: str | None = Query(None, description="Filter by created_by"),
    updated_by: str | None = Query(None, description="Filter by updated_by"),
    deleted_at: str | None = Query(None, description="Filter by deleted_at"),
):
    """List aix gateway decisions with pagination and filters."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        filters: dict[str, Any] = {}
        if project_id is not None:
            filters["project_id"] = project_id
        if workhub_task_id is not None:
            filters["workhub_task_id"] = workhub_task_id
        if workhub_task_number is not None:
            filters["workhub_task_number"] = workhub_task_number
        if opsbus_run_id is not None:
            filters["opsbus_run_id"] = opsbus_run_id
        if task_type is not None:
            filters["task_type"] = task_type
        if risk_level is not None:
            filters["risk_level"] = risk_level
        if model_policy is not None:
            filters["model_policy"] = model_policy
        if selected_provider is not None:
            filters["selected_provider"] = selected_provider
        if selected_model_id is not None:
            filters["selected_model_id"] = selected_model_id
        if selected_model_name is not None:
            filters["selected_model_name"] = selected_model_name
        if decision_status is not None:
            filters["decision_status"] = decision_status
        if decision_reason is not None:
            filters["decision_reason"] = decision_reason
        if retry_count is not None:
            filters["retry_count"] = retry_count
        if escalation_attempted is not None:
            filters["escalation_attempted"] = escalation_attempted
        if latency_ms is not None:
            filters["latency_ms"] = latency_ms
        if tokens_used is not None:
            filters["tokens_used"] = tokens_used
        if estimated_cost is not None:
            filters["estimated_cost"] = estimated_cost
        if created_by is not None:
            filters["created_by"] = created_by
        if updated_by is not None:
            filters["updated_by"] = updated_by
        if deleted_at is not None:
            filters["deleted_at"] = deleted_at
        records = await aix_gateway_decisions_service.list(
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
            filters=filters or None,
        )
        return AixGatewayDecisionsListAPIResponse(
            message="aix gateway decisions retrieved successfully",
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
            detail=f"Failed to list aix gateway decisions records: {str(exc)}",
        ) from exc


@router.patch(
    "/{record_id}",
    response_model=AixGatewayDecisionsAPIResponse,
    summary="Update AixGatewayDecisions",
    description="Update an existing aix_gateway_decisions",
)
async def update_aix_gateway_decisions(
    record_id: str,
    data: AixGatewayDecisionsUpdate,
    aix_gateway_decisions_service: AixGatewayDecisionsService = Depends(get_aix_gateway_decisions_service),
    current_user: dict = Depends(require_permission("aix_gateway_decisions:update")),
    bundle: PackBundle = Depends(get_pack_bundle),
    pack_context: PackContext = Depends(get_pack_context),
):
    """Update a aix_gateway_decisions record."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
        ensure_valid_payload(bundle, payload, operation="update")
        updated = await aix_gateway_decisions_service.update(
            record_id=record_id,
            data=data,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
            pack_context=pack_context,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AixGatewayDecisions not found")
        return AixGatewayDecisionsAPIResponse(
            message="AixGatewayDecisions updated successfully",
            data=updated,
        )
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update aix_gateway_decisions: {str(exc)}",
        ) from exc


@router.delete(
    "/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete AixGatewayDecisions",
    description="Delete a aix_gateway_decisions record",
)
async def delete_aix_gateway_decisions(
    record_id: str,
    aix_gateway_decisions_service: AixGatewayDecisionsService = Depends(get_aix_gateway_decisions_service),
    current_user: dict = Depends(require_permission("aix_gateway_decisions:delete")),
):
    """Delete a aix_gateway_decisions."""
    try:
        tenant_id = await validate_tenant_access(current_user)
        deleted = await aix_gateway_decisions_service.delete(
            record_id=record_id,
            tenant_id=tenant_id,
            user_id=current_user.get("id"),
        )
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AixGatewayDecisions not found")
    except ServiceError as exc:
        _raise_service_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete aix_gateway_decisions: {str(exc)}",
        ) from exc
