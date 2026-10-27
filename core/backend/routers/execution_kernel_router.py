"""EX2-S005/S007/S008: Execution Kernel API (Packet 5–8 surfaces).

Gateway HMAC via the same CycleApiSecurityPolicy as objective_cycle when
configured; tenant fail-closed always. Backed by KernelService or PostgresKernelService.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.aix_core.runtime.executor.execution_kernel_adapter.adapters import AdapterOutcome
from backend.aix_core.runtime.executor.execution_kernel_adapter.bootstrap import BootstrapSpec, BootstrapWorkItem
from backend.aix_core.runtime.executor.execution_kernel_adapter.cycle_adapter import AdapterHandle
from backend.aix_core.runtime.executor.context_builder import capsule_to_contract_dict
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.aix_core.runtime.executor.execution_kernel_adapter.postgres_service import PostgresKernelService
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService, require_tenant
from backend.aix_core.runtime.objective.api_backend import ObjectiveCycleApiBackend
from backend.aix_core.runtime.objective.api_security import (
    CycleApiSecurityContext,
    CycleApiSecurityError,
    CyclePermission,
    GatewayAssertion,
)

KernelBackend = KernelService | PostgresKernelService

router = APIRouter(prefix="/aix/execution-kernel", tags=["execution kernel"])
logger = structlog.get_logger("hyeve_aix_service.execution_kernel.audit")


class AppendEventRequest(BaseModel):
    aggregate_type: str
    aggregate_id: str
    expected_sequence_number: int = Field(ge=1)
    event_type: str
    command_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class DependencyRequest(BaseModel):
    work_item_id: str
    depends_on_work_item_id: str
    command_id: str


class CheckpointRequest(BaseModel):
    problem_id: str
    repository_state: dict[str, Any]


class CapsuleRequest(BaseModel):
    work_item_id: str
    max_tokens: int = Field(default=4000, ge=1)


class KnowledgeRequest(BaseModel):
    knowledge_entry_id: str
    knowledge_kind: str
    content: str
    command_id: str
    capability_id: str | None = None
    problem_id: str | None = None
    project_id: str | None = None
    applicable_capability_ids: list[str] = Field(default_factory=list)
    archive: bool = False
    valid_from: str | None = None
    valid_until: str | None = None
    superseded_by_entry_id: str | None = None
    confidence: Literal["unknown", "low", "medium", "high"] | None = None
    review_due_at: str | None = None


class DispatchRequestBody(BaseModel):
    work_item_id: str
    max_tokens: int = Field(default=4000, ge=1)
    timeout_budget_seconds: int = Field(default=600, ge=1)
    live: bool = False
    workhub_task_id: str | None = None
    task_class: str = "bounded_repair"
    candidate_sha: str | None = None
    repository_id: str | None = None
    definition_of_done: list[str] = Field(default_factory=list)
    allowed_files: list[str] = Field(default_factory=list)
    project_id: str | None = None
    budgets: dict[str, int] = Field(default_factory=dict)
    expected_sequence_number: int | None = Field(default=None, ge=1)
    mark_in_progress: bool = True
    repository: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    model_routing: dict[str, Any] | None = None
    include_verifier_route: bool = False
    require_independent_verifier: bool = False


class PollDispatchRequest(BaseModel):
    cycle_id: str
    objective_id: str
    phase: str = ""
    work_item_id: str
    capsule_content_sha256: str
    candidate_sha: str = "0000000"


class VerifyRequest(BaseModel):
    outcome_kind: Literal["SUCCEEDED", "FAILED", "BLOCKED", "TIMED_OUT"]
    evidence_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    reason_code: str | None = None
    cycle_id: str | None = None
    decision: Literal["ACCEPT", "REJECT", "WAIVE"]
    expected_sequence_number: int = Field(ge=1)
    command_id: str
    waiver_id: str | None = None
    waiver_rationale: str | None = None
    waiver_approver_actor_id: str | None = None
    waiver_approver_role: Literal["planner", "human"] | None = None
    waiver_expires_at: str | None = None
    required_evidence: bool = True


class DistillRequest(BaseModel):
    decision: Literal["ACCEPT", "REJECT", "WAIVE"]
    evidence_refs: list[str] = Field(default_factory=list)
    lesson_summary: str | None = None
    promote_pattern: bool = False
    command_id_prefix: str | None = None


class ProblemStepCompletion(BaseModel):
    work_item_id: str
    outcome_kind: Literal["SUCCEEDED", "FAILED", "BLOCKED", "TIMED_OUT"]
    decision: Literal["ACCEPT", "REJECT", "WAIVE"]
    evidence_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    reason_code: str | None = None
    cycle_id: str | None = None
    waiver_id: str | None = None
    waiver_rationale: str | None = None
    waiver_approver_actor_id: str | None = None
    waiver_approver_role: Literal["planner", "human"] | None = None
    waiver_expires_at: str | None = None
    required_evidence: bool = True


class ProposalRaiseBody(BaseModel):
    reason: str
    level: int = Field(default=2, ge=1, le=4)
    work_item_id: str | None = None
    affected_capabilities: list[str] = Field(default_factory=list)
    estimated_work_item_delta: int = 0
    risk: Literal["low", "medium", "high"] = "low"
    proposal_id: str | None = None
    command_id: str | None = None
    requests_new_capability: bool = False
    charter_change: bool = False


class ProposalResolveBody(BaseModel):
    decision: Literal["RESOLVED", "DENIED", "WITHDRAWN"] = "RESOLVED"
    command_id: str | None = None


class BootstrapWorkItemBody(BaseModel):
    title: str
    work_item_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class BootstrapRequest(BaseModel):
    vision_summary: str
    requirements_summary: str
    capability_name: str
    charter_summary: str
    work_items: list[BootstrapWorkItemBody] = Field(min_length=1)
    project_id: str | None = None
    capability_id: str | None = None
    problem_id: str | None = None
    roadmap_position: int = 1
    idempotency_key: str | None = None


class ProblemStepRequest(BaseModel):
    mode: Literal[
        "tick_dry",
        "tick_live",
        "complete",
        "accept_problem",
        "rework_problem",
        "complete_problem",
        "archive_problem",
    ] = "tick_dry"
    repository_state: dict[str, Any]
    max_tokens: int = Field(default=4000, ge=1)
    timeout_budget_seconds: int = Field(default=600, ge=1)
    distill: bool = True
    lesson_summary: str | None = None
    promote_pattern: bool = False
    dry_auto_verify: bool = True
    # live
    workhub_task_id: str | None = None
    task_class: str = "bounded_repair"
    candidate_sha: str | None = None
    repository_id: str | None = None
    definition_of_done: list[str] = Field(default_factory=list)
    allowed_files: list[str] = Field(default_factory=list)
    project_id: str | None = None
    budgets: dict[str, int] = Field(default_factory=dict)
    repository: dict[str, Any] = Field(default_factory=dict)
    # complete
    completion: ProblemStepCompletion | None = None
    proposal: ProposalRaiseBody | None = None
    idempotency_key: str | None = None


def _kernel(request: Request) -> KernelBackend:
    service = getattr(request.app.state, "execution_kernel_service", None)
    if service is None:
        service = KernelService()
        request.app.state.execution_kernel_service = service
    return service


def _cycle_backend(request: Request) -> ObjectiveCycleApiBackend:
    backend = getattr(request.app.state, "objective_cycle_api_backend", None)
    if backend is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "CYCLE_DURABLE_BACKEND_NOT_CONFIGURED"},
        )
    return backend


async def _require_command(request: Request, context: CycleApiSecurityContext) -> None:
    if CyclePermission.COMMAND.value not in context.roles and CyclePermission.ADMIN.value not in context.roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "KERNEL_RBAC_FORBIDDEN"})
    # AIX-EXECUTION-006-S5: actor-level audit — best-effort, never blocks the
    # real mutation on an audit-write hiccup. Durable only when Postgres-
    # backed; the in-memory dev backend has no persistence story for this
    # either, consistent with everything else it doesn't persist.
    service = getattr(request.app.state, "execution_kernel_service", None)
    if isinstance(service, PostgresKernelService):
        try:
            await service.record_actor_mutation(
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                roles=context.roles,
                action=f"{request.method} {request.url.path}",
                request_id=context.request_id,
            )
        except Exception:  # noqa: BLE001 - audit is best-effort, never blocks the request
            logger.warning("execution_kernel.actor_audit.write_failed", path=str(request.url.path))


async def get_kernel_security_context(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    gateway_id: Annotated[str | None, Header(alias="X-AIX-Gateway-Identity")] = None,
    tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    actor_id: Annotated[str | None, Header(alias="X-AIX-Actor-ID")] = None,
    roles: Annotated[str | None, Header(alias="X-AIX-Roles")] = None,
    issued_at: Annotated[int | None, Header(alias="X-AIX-Gateway-Issued-At")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
    signature: Annotated[str | None, Header(alias="X-AIX-Gateway-Signature")] = None,
) -> CycleApiSecurityContext:
    """Authorize via shared objective-cycle gateway policy, or tenant-only when not required."""
    policy = getattr(request.app.state, "execution_kernel_security_policy", None) or getattr(
        request.app.state, "objective_cycle_security_policy", None
    )
    require_gateway = bool(getattr(request.app.state, "execution_kernel_require_gateway", False))
    if policy is None:
        if require_gateway:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "KERNEL_SECURITY_POLICY_NOT_CONFIGURED"},
            )
        try:
            tid = require_tenant(tenant_id)
        except ExecutionKernelRepositoryError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "TENANT_REQUIRED", "message": str(exc)},
            ) from exc
        return CycleApiSecurityContext(
            gateway_id="local",
            tenant_id=tid,
            actor_id=str(actor_id or "local").strip() or "local",
            roles=(CyclePermission.READ.value, CyclePermission.COMMAND.value, CyclePermission.ADMIN.value),
            request_id=str(request_id or "local").strip() or "local",
        )
    try:
        assertion = GatewayAssertion(
            authorization=str(authorization or ""),
            gateway_id=str(gateway_id or ""),
            tenant_id=str(tenant_id or ""),
            actor_id=str(actor_id or ""),
            roles=tuple(item.strip() for item in str(roles or "").split(",") if item.strip()),
            issued_at_epoch=int(issued_at or 0),
            request_id=str(request_id or ""),
            signature=str(signature or ""),
        )
        return policy.authorize(assertion, required_permission=CyclePermission.READ)
    except (CycleApiSecurityError, ValueError) as exc:
        code = getattr(exc, "code", "KERNEL_SECURITY_ASSERTION_INVALID")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": code}) from exc


@router.get("/health")
async def kernel_health(request: Request) -> dict[str, Any]:
    service = _kernel(request)
    require_gateway = bool(getattr(request.app.state, "execution_kernel_require_gateway", False))
    policy = getattr(request.app.state, "execution_kernel_security_policy", None) or getattr(
        request.app.state, "objective_cycle_security_policy", None
    )
    return {
        "ok": True,
        "mode": service.mode,
        "surface": "execution_kernel",
        "gateway_required": require_gateway,
        "gateway_policy": policy is not None,
    }


@router.post("/events")
async def append_event(
    body: AppendEventRequest,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    try:
        event = await service.append_and_project(
            tenant_id=context.tenant_id,
            aggregate_type=body.aggregate_type,
            aggregate_id=body.aggregate_id,
            expected_sequence_number=body.expected_sequence_number,
            event_type=body.event_type,
            command_id=body.command_id,
            payload=body.payload,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "KERNEL_APPEND_FAILED", "message": str(exc)},
        ) from exc
    return {
        "ok": True,
        "event": {
            "id": event.id,
            "tenant_id": event.tenant_id,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "sequence_number": event.sequence_number,
            "event_type": event.event_type,
            "command_id": event.command_id,
            "payload": event.payload,
            "replayed": event.replayed,
        },
    }


@router.get("/projection/{aggregate_type}/{aggregate_id}")
async def get_projection(
    aggregate_type: str,
    aggregate_id: str,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    service = _kernel(request)
    row = await service.get_projection(
        tenant_id=context.tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "PROJECTION_NOT_FOUND"})
    return {"ok": True, "item": row}


@router.post("/dependencies")
async def add_dependency(
    body: DependencyRequest,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    try:
        edge = await service.add_dependency(
            tenant_id=context.tenant_id,
            work_item_id=body.work_item_id,
            depends_on_work_item_id=body.depends_on_work_item_id,
            command_id=body.command_id,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "DEPENDENCY_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "edge": edge}


@router.get("/problems/{problem_id}/next-actionable")
async def next_actionable(
    problem_id: str,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    service = _kernel(request)
    items = await service.next_actionable(tenant_id=context.tenant_id, problem_id=problem_id)
    return {"ok": True, "problem_id": problem_id, "work_item_ids": items}


@router.post("/checkpoints")
async def capture_checkpoint(
    body: CheckpointRequest,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    try:
        cp = await service.capture_checkpoint(
            tenant_id=context.tenant_id, problem_id=body.problem_id, repository_state=body.repository_state
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "CHECKPOINT_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "checkpoint": cp}


@router.post("/problems/{problem_id}/resume")
async def resume_problem(
    problem_id: str,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    result = await service.resume(tenant_id=context.tenant_id, problem_id=problem_id)
    return {
        "ok": True,
        "resume": {
            "problem_id": result.problem_id,
            "checkpoint_id": result.checkpoint_id,
            "work_items": result.work_items,
            "next_actionable": result.next_actionable,
            "projection_watermark": result.projection_watermark,
            "source": result.source,
        },
    }


@router.post("/capsules")
async def build_capsule(
    body: CapsuleRequest,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    try:
        capsule = await service.build_capsule(
            tenant_id=context.tenant_id, work_item_id=body.work_item_id, max_tokens=body.max_tokens
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "CAPSULE_FAILED", "message": str(exc)},
        ) from exc
    return {
        "ok": True,
        "capsule": capsule_to_contract_dict(capsule),
    }


@router.post("/knowledge")
async def upsert_knowledge(
    body: KnowledgeRequest,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    try:
        entry = await service.upsert_knowledge(
            tenant_id=context.tenant_id,
            knowledge_entry_id=body.knowledge_entry_id,
            knowledge_kind=body.knowledge_kind,
            content=body.content,
            command_id=body.command_id,
            capability_id=body.capability_id,
            problem_id=body.problem_id,
            project_id=body.project_id,
            applicable_capability_ids=body.applicable_capability_ids,
            archive=body.archive,
            valid_from=body.valid_from,
            valid_until=body.valid_until,
            superseded_by_entry_id=body.superseded_by_entry_id,
            confidence=body.confidence,
            review_due_at=body.review_due_at,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "KNOWLEDGE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "entry": entry}


@router.post("/dispatch")
async def prepare_dispatch(
    body: DispatchRequestBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    if not body.live:
        try:
            capsule, dispatch = await service.prepare_dispatch(
                tenant_id=context.tenant_id,
                work_item_id=body.work_item_id,
                max_tokens=body.max_tokens,
                timeout_budget_seconds=body.timeout_budget_seconds,
                model_routing=body.model_routing,
                include_verifier_route=body.include_verifier_route,
                require_independent_verifier=body.require_independent_verifier,
            )
        except ExecutionKernelRepositoryError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "DISPATCH_FAILED", "message": str(exc)},
            ) from exc
        return {
            "ok": True,
            "live": False,
            "dispatch": {
                "work_item_id": dispatch.work_item_id,
                "tenant_id": dispatch.tenant_id,
                "capsule_content_sha256": dispatch.capsule_content_sha256,
                "max_tokens": dispatch.max_tokens,
                "timeout_budget_seconds": dispatch.timeout_budget_seconds,
                "constraints": dispatch.constraints,
                "acceptance": dispatch.acceptance,
                "model_routes": dispatch.model_routes,
            },
            "capsule_content_sha256": capsule.capsule_content_sha256,
        }

    key = str(idempotency_key or body.idempotency_key or "").strip()
    if not key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    if not str(body.workhub_task_id or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "WORKHUB_TASK_ID_REQUIRED"},
        )
    try:
        result = await service.live_dispatch(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            work_item_id=body.work_item_id,
            backend=_cycle_backend(request),
            workhub_task_id=str(body.workhub_task_id),
            idempotency_key=key,
            max_tokens=body.max_tokens,
            timeout_budget_seconds=body.timeout_budget_seconds,
            task_class=body.task_class,
            candidate_sha=body.candidate_sha,
            repository_id=body.repository_id,
            definition_of_done=body.definition_of_done or None,
            allowed_files=body.allowed_files or None,
            project_id=body.project_id,
            budgets=body.budgets or None,
            expected_sequence_number=body.expected_sequence_number,
            mark_in_progress=body.mark_in_progress,
            repository=body.repository or None,
            model_routing=body.model_routing,
            include_verifier_route=body.include_verifier_route,
            require_independent_verifier=body.require_independent_verifier,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "LIVE_DISPATCH_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "live": True, **result}


@router.post("/problems/{problem_id}/step")
async def problem_step(
    problem_id: str,
    body: ProblemStepRequest,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    key = str(idempotency_key or body.idempotency_key or "").strip()
    if not key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})

    outcome = None
    decision = None
    work_item_id = None
    waiver_id = None
    waiver_rationale = None
    waiver_approver_actor_id = None
    waiver_approver_role = None
    waiver_expires_at = None
    required_evidence = True
    if body.completion is not None:
        work_item_id = body.completion.work_item_id
        decision = body.completion.decision
        waiver_id = body.completion.waiver_id
        waiver_rationale = body.completion.waiver_rationale
        waiver_approver_actor_id = body.completion.waiver_approver_actor_id
        waiver_approver_role = body.completion.waiver_approver_role
        waiver_expires_at = body.completion.waiver_expires_at
        required_evidence = body.completion.required_evidence
        outcome = AdapterOutcome(
            kind=body.completion.outcome_kind,
            evidence_refs=tuple(body.completion.evidence_refs),
            artifact_refs=tuple(body.completion.artifact_refs),
            reason_code=body.completion.reason_code,
            cycle_id=body.completion.cycle_id,
        )

    cycle_backend = None
    if body.mode == "tick_live":
        cycle_backend = _cycle_backend(request)

    try:
        result = await service.run_problem_step(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            problem_id=problem_id,
            mode=body.mode,
            repository_state=body.repository_state,
            idempotency_key=key,
            max_tokens=body.max_tokens,
            timeout_budget_seconds=body.timeout_budget_seconds,
            distill=body.distill,
            lesson_summary=body.lesson_summary,
            promote_pattern=body.promote_pattern,
            cycle_backend=cycle_backend,
            workhub_task_id=body.workhub_task_id,
            task_class=body.task_class,
            candidate_sha=body.candidate_sha,
            repository_id=body.repository_id,
            definition_of_done=body.definition_of_done or None,
            allowed_files=body.allowed_files or None,
            project_id=body.project_id,
            budgets=body.budgets or None,
            repository=body.repository or None,
            work_item_id=work_item_id,
            outcome=outcome,
            decision=decision,
            waiver_id=waiver_id,
            waiver_rationale=waiver_rationale,
            waiver_approver_actor_id=waiver_approver_actor_id,
            waiver_approver_role=waiver_approver_role,
            waiver_expires_at=waiver_expires_at,
            required_evidence=required_evidence,
            dry_auto_verify=body.dry_auto_verify,
            proposal_raise=body.proposal.model_dump() if body.proposal else None,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PROBLEM_STEP_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "step": result.as_dict()}


@router.post("/bootstrap")
async def bootstrap_graph(
    body: BootstrapRequest,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    key = str(idempotency_key or body.idempotency_key or "").strip()
    if not key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    spec = BootstrapSpec(
        vision_summary=body.vision_summary,
        requirements_summary=body.requirements_summary,
        capability_name=body.capability_name,
        charter_summary=body.charter_summary,
        work_items=[
            BootstrapWorkItem(title=w.title, work_item_id=w.work_item_id, depends_on=list(w.depends_on))
            for w in body.work_items
        ],
        project_id=body.project_id,
        capability_id=body.capability_id,
        problem_id=body.problem_id,
        roadmap_position=body.roadmap_position,
    )
    try:
        graph = await service.bootstrap_graph(tenant_id=context.tenant_id, spec=spec, idempotency_key=key)
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "BOOTSTRAP_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "graph": graph}


@router.post("/problems/{problem_id}/proposals")
async def raise_problem_proposal(
    problem_id: str,
    body: ProposalRaiseBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    key = str(idempotency_key or body.command_id or "").strip()
    if not key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    try:
        proposal = await service.raise_proposal(
            tenant_id=context.tenant_id,
            problem_id=problem_id,
            reason=body.reason,
            command_id=key,
            level=body.level,  # type: ignore[arg-type]
            work_item_id=body.work_item_id,
            affected_capabilities=body.affected_capabilities,
            estimated_work_item_delta=body.estimated_work_item_delta,
            risk=body.risk,
            proposal_id=body.proposal_id,
            requests_new_capability=body.requests_new_capability,
            charter_change=body.charter_change,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PROPOSAL_RAISE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "proposal": proposal}


@router.get("/problems/{problem_id}/proposals")
async def list_problem_proposals(
    problem_id: str,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """EX3-S002: Proposal inbox — list open (non-terminal) proposals for a
    Problem. Backend query only; the KernelService method this calls
    (`list_open_proposals`) already existed (EX2-S012) but was never
    reachable via the API surface before this endpoint."""
    service = _kernel(request)
    proposals = await service.list_open_proposals(tenant_id=context.tenant_id, problem_id=problem_id)
    return {"ok": True, "problem_id": problem_id, "proposals": proposals}


@router.post("/proposals/{proposal_id}/resolve")
async def resolve_problem_proposal(
    proposal_id: str,
    body: ProposalResolveBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    key = str(idempotency_key or body.command_id or "").strip()
    if not key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    try:
        proposal = await service.resolve_proposal(
            tenant_id=context.tenant_id,
            proposal_id=proposal_id,
            command_id=key,
            decision=body.decision,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PROPOSAL_RESOLVE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "proposal": proposal}


@router.post("/dispatch/poll")
async def poll_dispatch(
    body: PollDispatchRequest,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    handle = AdapterHandle(
        cycle_id=body.cycle_id,
        objective_id=body.objective_id,
        phase=body.phase,
        work_item_id=body.work_item_id,
        tenant_id=context.tenant_id,
        capsule_content_sha256=body.capsule_content_sha256,
        candidate_sha=body.candidate_sha,
    )
    try:
        outcome = await service.poll_live_dispatch(
            tenant_id=context.tenant_id, backend=_cycle_backend(request), handle=handle
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "DISPATCH_POLL_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "outcome": outcome, "in_flight": outcome is None}


@router.post("/work-items/{work_item_id}/verify")
async def verify_work_item(
    work_item_id: str,
    body: VerifyRequest,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    outcome = AdapterOutcome(
        kind=body.outcome_kind,
        evidence_refs=tuple(body.evidence_refs),
        artifact_refs=tuple(body.artifact_refs),
        reason_code=body.reason_code,
        cycle_id=body.cycle_id,
    )
    try:
        result = await service.apply_verification(
            tenant_id=context.tenant_id,
            work_item_id=work_item_id,
            outcome=outcome,
            decision=body.decision,
            expected_sequence_number=body.expected_sequence_number,
            command_id=body.command_id,
            waiver_id=body.waiver_id,
            waiver_rationale=body.waiver_rationale,
            waiver_approver_actor_id=body.waiver_approver_actor_id,
            waiver_approver_role=body.waiver_approver_role,
            waiver_expires_at=body.waiver_expires_at,
            required_evidence=body.required_evidence,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VERIFY_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, **result}


@router.post("/work-items/{work_item_id}/distill")
async def distill_work_item(
    work_item_id: str,
    body: DistillRequest,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    try:
        entries = await service.distill_learning(
            tenant_id=context.tenant_id,
            work_item_id=work_item_id,
            decision=body.decision,
            evidence_refs=body.evidence_refs,
            lesson_summary=body.lesson_summary,
            promote_pattern=body.promote_pattern,
            command_id_prefix=body.command_id_prefix,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "DISTILL_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "entries": entries}


class CapabilityRegisterBody(BaseModel):
    capability_key: str
    version: str
    display_name: str
    description: str = ""
    input_contract: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    authority_requirements: dict[str, Any] = Field(default_factory=dict)
    executor_binding: str = ""
    verification_method: str = ""
    dependencies: list[str] = Field(default_factory=list)
    cost_class: Literal["low", "medium", "high"] = "medium"
    risk_class: Literal["low", "medium", "high"] = "medium"
    status: Literal["ACTIVE", "DEPRECATED", "RETIRED"] = "ACTIVE"


@router.post("/capability-registry")
async def register_capability(
    body: CapabilityRegisterBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """EX4-R2-P6-S001: register an internal AIX execution capability (not marketplace)."""
    await _require_command(request, context)
    service = _kernel(request)
    try:
        entry = await service.register_capability(
            tenant_id=context.tenant_id,
            capability_key=body.capability_key,
            version=body.version,
            display_name=body.display_name,
            description=body.description,
            input_contract=body.input_contract,
            output_contract=body.output_contract,
            authority_requirements=body.authority_requirements,
            executor_binding=body.executor_binding,
            verification_method=body.verification_method,
            dependencies=body.dependencies,
            cost_class=body.cost_class,
            risk_class=body.risk_class,
            status=body.status,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "CAPABILITY_REGISTER_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "capability": entry}


@router.get("/capability-registry")
async def list_capabilities(
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    capability_key: str | None = None,
    active_only: bool = True,
) -> dict[str, Any]:
    service = _kernel(request)
    try:
        entries = await service.list_registered_capabilities(
            tenant_id=context.tenant_id,
            active_only=active_only,
            capability_key=capability_key,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "CAPABILITY_LIST_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "capabilities": entries}


@router.get("/capability-registry/{capability_key}")
async def get_capability(
    capability_key: str,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    version: str | None = None,
) -> dict[str, Any]:
    service = _kernel(request)
    try:
        entry = await service.get_registered_capability(
            tenant_id=context.tenant_id, capability_key=capability_key, version=version
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "CAPABILITY_GET_FAILED", "message": str(exc)},
        ) from exc
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CAPABILITY_NOT_FOUND"},
        )
    return {"ok": True, "capability": entry}


@router.get("/capability-registry/{capability_key}/resolve")
async def resolve_capability(
    capability_key: str,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    version: str | None = None,
) -> dict[str, Any]:
    service = _kernel(request)
    try:
        entries = await service.resolve_capability_dependencies(
            tenant_id=context.tenant_id, capability_key=capability_key, version=version
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "CAPABILITY_RESOLVE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "resolved": entries}


class PatternRecommendQuery(BaseModel):
    capability_id: str
    problem_id: str | None = None
    limit: int = Field(default=5, ge=1, le=50)


class PatternUseBody(BaseModel):
    work_item_id: str
    pattern_entry_id: str
    command_id: str


@router.get("/patterns/discover")
async def patterns_discover(
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    min_occurrences: int = 2,
) -> dict[str, Any]:
    """EX4-R2-P7-S001: corpus pattern candidates — no auto-promotion."""
    service = _kernel(request)
    try:
        candidates = await service.discover_patterns(
            tenant_id=context.tenant_id, min_occurrences=min_occurrences
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PATTERN_DISCOVER_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "candidates": candidates, "auto_promoted": False}


@router.get("/patterns/recommend")
async def patterns_recommend(
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    capability_id: str,
    problem_id: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    service = _kernel(request)
    try:
        items = await service.recommend_patterns(
            tenant_id=context.tenant_id,
            capability_id=capability_id,
            problem_id=problem_id,
            limit=limit,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PATTERN_RECOMMEND_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "recommendations": items}


@router.post("/patterns/use")
async def patterns_record_use(
    body: PatternUseBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    try:
        edge = await service.record_pattern_use(
            tenant_id=context.tenant_id,
            work_item_id=body.work_item_id,
            pattern_entry_id=body.pattern_entry_id,
            command_id=body.command_id,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PATTERN_USE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "edge": edge}


class LearningObserveBody(BaseModel):
    summary: str
    source_work_item_id: str | None = None
    source_knowledge_ids: list[str] | None = None
    capability_id: str | None = None
    problem_id: str | None = None


class LearningAdvanceBody(BaseModel):
    to_stage: str


class LearningScopeBody(BaseModel):
    scope: str


class LearningConfidenceBody(BaseModel):
    confidence: str


class LearningApproveBody(BaseModel):
    actor_id: str
    proposal_level: int = Field(ge=1, le=5)
    proposal_id: str | None = None
    promote_to_knowledge: bool = False
    command_id: str | None = None


@router.post("/learning")
async def learning_observe(
    body: LearningObserveBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """EX4-R3-P8-S001: open a learning record at OBSERVATION."""
    await _require_command(request, context)
    service = _kernel(request)
    try:
        record = await service.observe_learning(
            tenant_id=context.tenant_id,
            summary=body.summary,
            source_work_item_id=body.source_work_item_id,
            source_knowledge_ids=body.source_knowledge_ids,
            capability_id=body.capability_id,
            problem_id=body.problem_id,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "LEARNING_OBSERVE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "learning": record}


@router.get("/learning")
async def learning_list(
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    stage: str | None = None,
    active_only: bool = False,
) -> dict[str, Any]:
    service = _kernel(request)
    try:
        items = await service.list_learning(
            tenant_id=context.tenant_id, stage=stage, active_only=active_only
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "LEARNING_LIST_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "learning": items}


@router.post("/learning/{learning_id}/advance")
async def learning_advance(
    learning_id: str,
    body: LearningAdvanceBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    try:
        record = await service.advance_learning(
            tenant_id=context.tenant_id, learning_id=learning_id, to_stage=body.to_stage
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "LEARNING_ADVANCE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "learning": record}


@router.post("/learning/{learning_id}/scope")
async def learning_scope(
    learning_id: str,
    body: LearningScopeBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    try:
        record = await service.classify_learning_scope(
            tenant_id=context.tenant_id, learning_id=learning_id, scope=body.scope
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "LEARNING_SCOPE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "learning": record}


@router.post("/learning/{learning_id}/confidence")
async def learning_confidence(
    learning_id: str,
    body: LearningConfidenceBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    try:
        record = await service.set_learning_confidence(
            tenant_id=context.tenant_id,
            learning_id=learning_id,
            confidence=body.confidence,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "LEARNING_CONFIDENCE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "learning": record}


@router.post("/learning/{learning_id}/approve")
async def learning_approve(
    learning_id: str,
    body: LearningApproveBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """Governed activation — Proposal level must meet scope floor."""
    await _require_command(request, context)
    service = _kernel(request)
    try:
        record = await service.approve_learning(
            tenant_id=context.tenant_id,
            learning_id=learning_id,
            actor_id=body.actor_id,
            proposal_level=body.proposal_level,
            proposal_id=body.proposal_id,
            promote_to_knowledge=body.promote_to_knowledge,
            command_id=body.command_id,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "LEARNING_APPROVE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "learning": record, "auto_promoted": False}


@router.post("/learning/{learning_id}/reject")
async def learning_reject(
    learning_id: str,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    await _require_command(request, context)
    service = _kernel(request)
    try:
        record = await service.reject_learning(
            tenant_id=context.tenant_id, learning_id=learning_id
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "LEARNING_REJECT_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "learning": record}


# ---------------------------------------------------------------------------
# EX4-R2-P3-S001: Organizational Brain — functional knowledge-platform routes
# ---------------------------------------------------------------------------


class KnowledgeConflictBody(BaseModel):
    left_knowledge_id: str
    right_knowledge_id: str
    command_id: str
    authority_context: str | None = None


class KnowledgeConflictResolveBody(BaseModel):
    authoritative_knowledge_id: str
    superseded_knowledge_id: str
    command_id: str


@router.get("/organizational-knowledge")
async def query_organizational_knowledge(
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    capability_id: str | None = None,
    project_id: str | None = None,
    minimum_confidence: str = "unknown",
    include_stale: bool = False,
) -> dict[str, Any]:
    """EX4-R2-P3-S001: provenance-aware governed Knowledge query across the org.

    Returns current Knowledge entries scoped to the caller's tenant, enriched
    with their active Knowledge Graph edges so consumers see full provenance
    without a second trip. Optionally filters by capability, project, and
    minimum confidence. Stale (expired) entries are excluded by default but
    can be included for audit purposes.
    """
    service = _kernel(request)
    try:
        entries = await service.query_organizational_knowledge(
            tenant_id=context.tenant_id,
            capability_id=capability_id,
            project_id=project_id,
            minimum_confidence=minimum_confidence,
            include_stale=include_stale,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "ORG_KNOWLEDGE_QUERY_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "entries": entries, "count": len(entries)}


@router.get("/organizational-knowledge/missing")
async def detect_missing_knowledge(
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    capability_id: str = "",
    required_kinds: str | None = None,
) -> dict[str, Any]:
    """EX4-R2-P3-S001: detect absent Knowledge for a Capability.

    Returns whether current (non-stale, non-deprecated) Knowledge exists for
    ``capability_id``, and — if ``required_kinds`` is supplied (comma-separated
    list of knowledge_kind values) — which of those kinds are missing. Callers
    can use this to surface knowledge-gap signals to the Planner without
    inventing a parallel brain data plane.
    """
    service = _kernel(request)
    parsed_kinds: list[str] | None = None
    if required_kinds:
        parsed_kinds = [k.strip() for k in required_kinds.split(",") if k.strip()]
    try:
        result = await service.detect_missing_knowledge(
            tenant_id=context.tenant_id,
            capability_id=capability_id,
            required_kinds=parsed_kinds,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "MISSING_KNOWLEDGE_CHECK_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, **result}


@router.get("/organizational-knowledge/cross-project")
async def reconcile_cross_project_knowledge(
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """EX4-R2-P3-S001: identify identical current Knowledge reused across Projects.

    Surfaces Knowledge entries that share the same content fingerprint and
    capability context but are stored independently under two or more Projects
    within the tenant. This enables human-directed consolidation decisions
    without automated rewriting of any entry.
    """
    service = _kernel(request)
    try:
        groups = await service.reconcile_cross_project_knowledge(tenant_id=context.tenant_id)
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "CROSS_PROJECT_RECONCILE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "groups": groups, "count": len(groups)}


class RevalidateKnowledgeBody(BaseModel):
    command_id: str
    valid_until: str | None = None
    review_due_at: str | None = None
    confidence: str | None = None


@router.get("/organizational-knowledge/stale")
async def list_stale_knowledge(
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    capability_id: str | None = None,
) -> dict[str, Any]:
    """EX5-S7: the review worklist — currently-ACTIVE Knowledge entries
    past their valid_until/review_due_at date. Never includes SUPERSEDED/
    DEPRECATED entries — those don't need anyone's attention."""
    service = _kernel(request)
    items = await service.list_stale_knowledge(tenant_id=context.tenant_id, capability_id=capability_id)
    return {"ok": True, "items": items, "count": len(items)}


@router.post("/organizational-knowledge/{knowledge_entry_id}/revalidate")
async def revalidate_knowledge(
    knowledge_entry_id: str,
    body: RevalidateKnowledgeBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """EX5-S7: re-confirm a stale-but-still-good entry without resupplying
    its full content/payload — the actionable half of 'never permanently
    stale'."""
    await _require_command(request, context)
    service = _kernel(request)
    try:
        entry = await service.revalidate_knowledge(
            tenant_id=context.tenant_id,
            knowledge_entry_id=knowledge_entry_id,
            command_id=body.command_id,
            valid_until=body.valid_until,
            review_due_at=body.review_due_at,
            confidence=body.confidence,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "KNOWLEDGE_REVALIDATE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "knowledge": entry}


@router.post("/knowledge/conflicts")
async def declare_knowledge_conflict(
    body: KnowledgeConflictBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """EX4-R2-P3-S001: declare a conflict between two Knowledge entries.

    Creates a CONFLICTS_WITH edge in the Knowledge Graph. Both entries must
    exist and be distinct. Conflict declaration does not modify either entry —
    it only annotates the graph so humans (and future tooling) can see the
    tension and drive resolution via ``/knowledge/conflicts/{edge_id}/resolve``.
    Requires COMMAND permission.
    """
    await _require_command(request, context)
    service = _kernel(request)
    try:
        edge = await service.declare_knowledge_conflict(
            tenant_id=context.tenant_id,
            left_knowledge_id=body.left_knowledge_id,
            right_knowledge_id=body.right_knowledge_id,
            command_id=body.command_id,
            authority_context=body.authority_context,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "CONFLICT_DECLARE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "conflict_edge": edge}


@router.post("/knowledge/conflicts/{conflict_edge_id}/resolve")
async def resolve_knowledge_conflict(
    conflict_edge_id: str,
    body: KnowledgeConflictResolveBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """EX4-R2-P3-S001: resolve a declared Knowledge conflict.

    Supersedes the losing entry with a pointer to the authoritative one and
    retires the CONFLICTS_WITH edge (via ``supersede_edge``). Both IDs must
    match the conflict edge endpoints. Requires COMMAND permission.
    """
    await _require_command(request, context)
    service = _kernel(request)
    try:
        result = await service.resolve_knowledge_conflict(
            tenant_id=context.tenant_id,
            conflict_edge_id=conflict_edge_id,
            authoritative_knowledge_id=body.authoritative_knowledge_id,
            superseded_knowledge_id=body.superseded_knowledge_id,
            command_id=body.command_id,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "CONFLICT_RESOLVE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# EX4-R3-P9-S001: Semantic retrieval
# ---------------------------------------------------------------------------


class SemanticSearchBody(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=50)
    min_score: float = Field(default=0.05, ge=0.0, le=1.0)
    capability_id: str | None = None
    knowledge_kinds: list[str] | None = None


@router.get("/semantic/decision")
async def semantic_technology_decision(
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """EX4-R3-P9-S001: recorded technology choice (pgvector preferred; no dedicated vector DB)."""
    _ = context
    service = _kernel(request)
    return {"ok": True, "decision": await service.semantic_technology_decision()}


@router.post("/semantic/search")
async def semantic_search(
    body: SemanticSearchBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """Additive semantic retrieval over Knowledge — does not replace ranked capsules."""
    service = _kernel(request)
    try:
        hits = await service.semantic_search(
            tenant_id=context.tenant_id,
            query=body.query,
            limit=body.limit,
            min_score=body.min_score,
            capability_id=body.capability_id,
            knowledge_kinds=body.knowledge_kinds,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "SEMANTIC_SEARCH_FAILED", "message": str(exc)},
        ) from exc
    return {
        "ok": True,
        "hits": hits,
        "backend": "hashed_bow_v1",
        "replaces_ranked_retrieval": False,
        "preferred_durable": "postgres_pgvector",
    }


class PlanningDecomposeBody(BaseModel):
    objective: str
    max_work_items: int = Field(default=12, ge=1, le=12)
    capability_id: str | None = None
    # AIX-EXECUTION-006-S7: when capability_id is omitted, derive it from
    # this problem's parent capability rather than silently skipping
    # learning application.
    problem_id: str | None = None


class PlanningAutonomyBody(BaseModel):
    mutation_kind: str
    problem_id: str | None = None
    affected_capabilities: list[str] | None = None
    requests_new_capability: bool = False
    charter_change: bool = False


class PlanningProposeBody(BaseModel):
    mutation_kind: str
    reason: str
    command_id: str
    work_item_id: str | None = None
    affected_capabilities: list[str] | None = None
    objective: str | None = None
    requests_new_capability: bool = False
    charter_change: bool = False


@router.post("/planning/decompose")
async def planning_decompose(
    body: PlanningDecomposeBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """EX4-R3-P10-S001: bounded draft plan — no projection writes."""
    service = _kernel(request)
    try:
        plan = await service.decompose_planning_objective(
            objective=body.objective, max_work_items=body.max_work_items,
            tenant_id=context.tenant_id, capability_id=body.capability_id,
            problem_id=body.problem_id,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PLANNING_DECOMPOSE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "plan": plan, "silent_graph_mutation": False}


@router.post("/planning/autonomy")
async def planning_autonomy(
    body: PlanningAutonomyBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    service = _kernel(request)
    try:
        decision = await service.evaluate_planning_autonomy(
            mutation_kind=body.mutation_kind,
            problem_id=body.problem_id,
            tenant_id=context.tenant_id,
            affected_capabilities=body.affected_capabilities,
            requests_new_capability=body.requests_new_capability,
            charter_change=body.charter_change,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PLANNING_AUTONOMY_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "autonomy": decision}


@router.get("/planning/problems/{problem_id}/replan-triggers")
async def planning_replan_triggers(
    problem_id: str,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    capability_id: str | None = None,
    required_kinds: str | None = None,
    objective: str | None = None,
) -> dict[str, Any]:
    service = _kernel(request)
    kinds = [k.strip() for k in required_kinds.split(",") if k.strip()] if required_kinds else None
    try:
        result = await service.detect_planning_replan_triggers(
            tenant_id=context.tenant_id,
            problem_id=problem_id,
            capability_id=capability_id,
            required_knowledge_kinds=kinds,
            objective=objective,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "REPLAN_TRIGGERS_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, **result}


@router.post("/planning/problems/{problem_id}/propose-mutation")
async def planning_propose_mutation(
    problem_id: str,
    body: PlanningProposeBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """All plan mutations via Proposal rails — never silent graph rewrite."""
    await _require_command(request, context)
    service = _kernel(request)
    try:
        result = await service.propose_plan_mutation(
            tenant_id=context.tenant_id,
            problem_id=problem_id,
            mutation_kind=body.mutation_kind,
            reason=body.reason,
            command_id=body.command_id,
            work_item_id=body.work_item_id,
            affected_capabilities=body.affected_capabilities,
            objective=body.objective,
            requests_new_capability=body.requests_new_capability,
            charter_change=body.charter_change,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PLAN_MUTATION_PROPOSE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, **result}


class ReasoningQueryBody(BaseModel):
    mode: str
    question: str = ""
    capability_id: str | None = None
    project_id: str | None = None
    objective: str | None = None
    required_knowledge_kinds: list[str] | None = None
    semantic_limit: int = Field(default=5, ge=1, le=20)


@router.get("/reasoning/scenarios")
async def reasoning_golden_scenarios(
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """EX4-R4-P11-S001: named golden reasoning scenarios."""
    _ = context
    service = _kernel(request)
    return {"ok": True, "scenarios": await service.reasoning_golden_scenarios()}


@router.post("/reasoning/query")
async def reasoning_query(
    body: ReasoningQueryBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """Read/reason under tenant scope — never mutates plans or policy."""
    service = _kernel(request)
    try:
        result = await service.reason_organizationally(
            tenant_id=context.tenant_id,
            mode=body.mode,
            question=body.question,
            capability_id=body.capability_id,
            project_id=body.project_id,
            objective=body.objective,
            required_knowledge_kinds=body.required_knowledge_kinds,
            semantic_limit=body.semantic_limit,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "REASONING_QUERY_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "result": result}


class EnterpriseImprovementBody(BaseModel):
    summary: str
    scope: str = "organisation"
    capability_id: str | None = None
    actor_id: str = "enterprise_operator"


@router.get("/enterprise/scenarios")
async def enterprise_scenarios_endpoint(
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """EX4-R4-P12-S001: named enterprise scenarios."""
    _ = context
    service = _kernel(request)
    return {"ok": True, "scenarios": await service.enterprise_scenarios()}


@router.get("/enterprise/portfolio")
async def enterprise_portfolio(
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """Portfolio intelligence view — multi-project coordination snapshot."""
    service = _kernel(request)
    try:
        view = await service.portfolio_intelligence_view(tenant_id=context.tenant_id)
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PORTFOLIO_VIEW_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "portfolio": view}


@router.post("/enterprise/improvement-cycles")
async def enterprise_improvement_cycle(
    body: EnterpriseImprovementBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """Governed improvement cycle — org scope never silently promotes."""
    await _require_command(request, context)
    service = _kernel(request)
    try:
        result = await service.start_enterprise_improvement_cycle(
            tenant_id=context.tenant_id,
            summary=body.summary,
            scope=body.scope,
            capability_id=body.capability_id,
            actor_id=body.actor_id,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "IMPROVEMENT_CYCLE_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, **result}


@router.post("/enterprise/problems/{problem_id}/strategic-resume")
async def enterprise_strategic_resume(
    problem_id: str,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    capability_id: str | None = None,
) -> dict[str, Any]:
    """Long-horizon strategic programme resume under tenant scope."""
    await _require_command(request, context)
    service = _kernel(request)
    try:
        result = await service.strategic_programme_resume(
            tenant_id=context.tenant_id,
            problem_id=problem_id,
            capability_id=capability_id,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "STRATEGIC_RESUME_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, **result}


@router.get("/enterprise/audit")
async def enterprise_audit_list(
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    service = _kernel(request)
    events = await service.list_enterprise_audit(tenant_id=context.tenant_id)
    return {"ok": True, "events": events, "count": len(events)}


class ExecutionRouteSwitchBody(BaseModel):
    model_routes: dict[str, Any]
    reason: str
    cycle_id: str | None = None
    as_fallback: bool = False


@router.get("/execution-routes/{work_item_id}")
async def execution_route_get(
    work_item_id: str,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """EX5-S2: the currently bound executor/verifier model route for a Work
    Item — None if no dispatch with model_routing has bound one yet."""
    service = _kernel(request)
    route = await service.get_execution_route(tenant_id=context.tenant_id, work_item_id=work_item_id)
    return {"ok": True, "route": route}


@router.post("/execution-routes/{work_item_id}/switch")
async def execution_route_switch(
    work_item_id: str,
    body: ExecutionRouteSwitchBody,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    """EX5-S2: switch the bound model route — a reason is always required and
    always recorded; the previous route becomes SUPERSEDED, never dropped."""
    await _require_command(request, context)
    service = _kernel(request)
    try:
        route = await service.switch_execution_route(
            tenant_id=context.tenant_id,
            work_item_id=work_item_id,
            model_routes=body.model_routes,
            reason=body.reason,
            cycle_id=body.cycle_id,
            as_fallback=body.as_fallback,
        )
    except ExecutionKernelRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "EXECUTION_ROUTE_SWITCH_FAILED", "message": str(exc)},
        ) from exc
    return {"ok": True, "route": route}


@router.get("/execution-routes/{work_item_id}/history")
async def execution_route_history(
    work_item_id: str,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    service = _kernel(request)
    rows = await service.list_execution_route_history(tenant_id=context.tenant_id, work_item_id=work_item_id)
    return {"ok": True, "history": rows, "count": len(rows)}


@router.get("/execution-routes/{work_item_id}/audit")
async def execution_route_audit(
    work_item_id: str,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
) -> dict[str, Any]:
    service = _kernel(request)
    events = await service.list_execution_route_audit(tenant_id=context.tenant_id, work_item_id=work_item_id)
    return {"ok": True, "events": events, "count": len(events)}


@router.get("/audit/actor-mutations")
async def actor_mutation_audit(
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_kernel_security_context)],
    limit: int = 200,
) -> dict[str, Any]:
    """AIX-EXECUTION-006-S5: who did what, when — across every mutating
    execution_kernel endpoint (Postgres-backed deployments only)."""
    service = _kernel(request)
    if not isinstance(service, PostgresKernelService):
        return {"ok": True, "events": [], "count": 0, "durable": False}
    events = await service.list_actor_audit(tenant_id=context.tenant_id, limit=limit)
    return {"ok": True, "events": events, "count": len(events), "durable": True}
