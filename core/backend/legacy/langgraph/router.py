from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.dependencies.aix_gateway_decisions_service_dependencies import require_permission, validate_tenant_access
from backend.hyeve_aix_service.config import settings
from backend.legacy.langgraph.service import execute_langgraph_orchestration
from backend.legacy.langgraph.state import LangGraphExecutionState

router = APIRouter(
    prefix="/api/aix/langgraph",
    tags=["aix langgraph orchestration"],
)

# C004-T02: production writes retired. Even if this router is remounted, mutation fails closed.
LANGGRAPH_WRITES_RETIRED = True
LANGGRAPH_RETIREMENT_CODE = "LANGGRAPH_WRITES_RETIRED"


def _retired_langgraph_write_guard() -> None:
    """Return the retirement contract before legacy request-body validation."""
    raise HTTPException(
        status_code=410,
        detail={
            "code": LANGGRAPH_RETIREMENT_CODE,
            "message": (
                "LangGraph production write path retired by C004. "
                "Use canonical /aix/objectives + /aix/cycles (objective_cycle)."
            ),
            "canonical_surface": "/aix/objectives/admit",
        },
    )


class LangGraphRunRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    task_number: int = Field(..., ge=1)
    correlation_id: str = Field(..., min_length=1)
    user_goal: str = Field(..., min_length=1)
    routing_profile: str = Field(default="B", min_length=1)
    envelope_version: str = Field(default="v1", min_length=1)
    prompt_profile: str = Field(default="default", min_length=1)
    approval: dict[str, Any] = Field(default_factory=dict)
    workhub_task_id: str | None = None
    opsbus_run_id: str | None = None


class LangGraphRunResponse(BaseModel):
    success: bool = True
    message: str = "LangGraph orchestration run completed"
    data: dict[str, Any]


@router.post(
    "/run",
    response_model=LangGraphRunResponse,
    summary="Execute LangGraph planner/coder chain (RETIRED — C004)",
    dependencies=[Depends(_retired_langgraph_write_guard)],
)
async def run_langgraph_chain(
    body: Any = None,
    current_user: dict[str, Any] = Depends(require_permission("gateway_decisions:create")),
):
    if LANGGRAPH_WRITES_RETIRED:
        raise HTTPException(
            status_code=410,
            detail={
                "code": LANGGRAPH_RETIREMENT_CODE,
                "message": (
                    "LangGraph production write path retired by C004. "
                    "Use canonical /aix/objectives + /aix/cycles (objective_cycle)."
                ),
                "canonical_surface": "/aix/objectives/admit",
            },
        )
    if not settings.LANGGRAPH_ENABLED:
        raise HTTPException(status_code=503, detail="LANGGRAPH_DISABLED")
    assert isinstance(body, LangGraphRunRequest)  # unreachable while retired
    tenant_id = await validate_tenant_access(current_user)
    actor_id = str(current_user.get("id") or "system")
    state = LangGraphExecutionState(
        project_id=body.project_id,
        task_number=body.task_number,
        correlation_id=body.correlation_id,
        routing_profile=body.routing_profile,
        envelope_version=body.envelope_version,
        prompt_profile=body.prompt_profile,
        user_goal=body.user_goal,
        approval=dict(body.approval),
    )
    result = await execute_langgraph_orchestration(
        tenant_id=tenant_id,
        actor_id=actor_id,
        state=state,
        workhub_task_id=body.workhub_task_id,
        opsbus_run_id=body.opsbus_run_id,
    )
    return LangGraphRunResponse(data=result)
