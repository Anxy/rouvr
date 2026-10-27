"""Restricted, gateway-only objective-cycle command and query surface."""

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.runtime.objective.api_backend import ObjectiveCycleApiBackend
from backend.aix_core.runtime.objective.api_security import (
    CycleApiSecurityContext,
    CycleApiSecurityError,
    CyclePermission,
    GatewayAssertion,
)
from backend.aix_core.runtime.executor.attachments import build_attachment_context_block
from backend.aix_core.runtime.objective.discuss_db import (
    DiscussDbError,
    get_discuss_thread_db,
    list_discuss_threads_db,
    persist_discuss_turn_db,
)
from backend.aix_core.runtime.objective.discuss_store import persist_discuss_turn
from backend.infrastructure.model_gateway.hyeve_integration_client import (
    ChatMessage,
    HyeveIntegrationChatError,
    chat_completion,
)
from backend.infrastructure.model_gateway.intelligence_layer import (
    intelligence_metrics,
    load_model_registry,
    run_discuss_turn,
)
from backend.infrastructure.model_gateway.intelligence_layer.intake_objective_scope import (
    resolve_normalized_objective,
)
from backend.infrastructure.model_gateway.intelligence_layer.execution_routing import (
    assert_independent_verification,
    resolve_intelligence_role,
)
from backend.aix_core.runtime.governance.policy import SUPPORTED_TASK_CLASSES
from backend.infrastructure.model_gateway.provider_routing import GovernedProviderRouter
from backend.hyeve_aix_service.config import settings

router = APIRouter(prefix="/aix", tags=["objective cycle"])

# Optional admission metadata vocabulary only — never used to restrict execution capability.
_DISCUSS_TASK_CLASSES = frozenset(SUPPORTED_TASK_CLASSES | {"bounded_repair"})

_DISCUSS_SYSTEM_PROMPT = (
    "You are AIX's Intake agent. A human is describing an objective for a governed "
    "code-change cycle. Describe WHAT they want — not HOW execution must perform it.\n"
    "1. Produce a normalized_objective that preserves every explicit user deliverable; "
    "never reduce scope. Treat only/just/provide/return/do not as constraints unless "
    "they explicitly cancel a deliverable.\n"
    "2. Set repository_hint only if the user named a repository/service; else null.\n"
    "3. Copy only explicitly mentioned paths/symbols into file_hints/symbol_hints — "
    "never invent files or implementation strategy.\n"
    "4. Copy only user-stated constraints into constraints.\n"
    "5. Optional task_class metadata may be one of "
    f"{sorted(_DISCUSS_TASK_CLASSES)} when useful for admission records; omit when unsure. "
    "It must not choose execution strategy or file scope.\n"
    "6. If ambiguous/under-specified, set ambiguous=true and ask exactly one clarifying "
    "question in reply.\n"
    "7. ambiguous and reply must never disagree: if reply tells the user their objective "
    "is confirmed/ready and to proceed (e.g. \"Confirmed\", \"click Create & run\"), you "
    "must set ambiguous=false and choose a real task_class in that same response -- never "
    "hand off to the user while still leaving ambiguous=true, and never leave ambiguous=true "
    "just because task_class feels uncertain once the objective itself is no longer in "
    "question. Conversely, while ambiguous=true, reply must ask its one clarifying question, "
    "never tell the user to proceed.\n"
    "You must never propose deploy, merge, or production schema changes, and you have no "
    "authority to accept, execute, or approve anything — you only describe the objective. "
    "Respond with ONLY a single JSON object matching this schema, no prose outside it and "
    "no markdown code fences:\n"
    '{"reply": string, "normalized_objective": string, "repository_hint": string|null, '
    '"file_hints": [string], "symbol_hints": [string], "constraints": [string], '
    '"task_class": string|null, "ambiguous": boolean}'
)


class AdmitObjectiveMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
    parent_message_id: str | None = None


class AdmitObjectiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workhub_task_id: str | None = Field(
        default=None,
        description=(
            "Existing WorkHub parent task id. Placeholders auto|create|new are create-intent "
            "only and require OBJECTIVE_CYCLE_CREATE_WORKHUB_TASK=true; they never persist."
        ),
    )
    objective: str = Field(min_length=1)
    task_class: str = Field(min_length=1)
    definition_of_done: tuple[str, ...] = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    allowed_files: tuple[str, ...] = ()
    project_id: str | None = Field(default=None, description="WorkHub project id (required to create or load a task).")
    task_number: int | None = Field(default=None, description="Existing WorkHub task number for read-only load.")
    create_workhub_task: bool | None = Field(
        default=None,
        description=(
            "Create-intent flag. With OBJECTIVE_CYCLE_CREATE_WORKHUB_TASK enabled and a "
            "placeholder/empty workhub_task_id, admit POSTs a WorkHub parent task and "
            "persists only the returned real id (exception 001P-W12). Ignored for real ids."
        ),
    )
    attachments: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Uploaded file asset refs (id/storage_key/filename/mime/local_path) for execute.",
    )
    conversation_id: str | None = Field(
        default=None, description="Discuss/Studio conversation id (AIX-REAL-EXECUTION-002 message binding)."
    )
    thread_id: str | None = Field(
        default=None,
        description="Alias for conversation_id (CLI discuss thread id). Prefer conversation_id.",
    )
    messages: tuple[AdmitObjectiveMessage, ...] = Field(
        default=(),
        description=(
            "Chat-origin admission only: the conversation's messages, oldest first. When "
            "supplied, `objective` text is always re-derived server-side from the correctly "
            "bound message -- never trusted from the client -- per AIX-REAL-EXECUTION-002 "
            "conversation-to-objective binding. Omit for legacy non-chat admission."
        ),
    )
    selected_message_id: str | None = Field(
        default=None,
        description="Optional explicit message selection; overrides latest-objective-bearing-message binding.",
    )
    objective_source_message_id: str | None = Field(
        default=None,
        description="Legacy non-chat admission only: the originating message id, supplied directly.",
    )


class DiscussObjectiveMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class DiscussObjectiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: tuple[DiscussObjectiveMessage, ...] = Field(min_length=1)
    thread_id: str | None = Field(
        default=None,
        description="Optional discuss thread id so transcript/token usage append to the same store.",
    )
    escalate_to_planner: bool = Field(
        default=False,
        description="When confirm-mode is on, set true after user confirms Create Objective.",
    )
    active_objective_id: str | None = Field(
        default=None,
        description="Optional existing objective/WorkHub id for continue_objective intent.",
    )
    attachments: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Uploaded file asset refs so discuss can read/check attached content.",
    )


async def get_cycle_api_backend(request: Request) -> ObjectiveCycleApiBackend:
    backend = getattr(request.app.state, "objective_cycle_api_backend", None)
    if backend is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "CYCLE_DURABLE_BACKEND_NOT_CONFIGURED"},
        )
    return backend


async def get_cycle_security_context(
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
    policy = getattr(request.app.state, "objective_cycle_security_policy", None)
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "CYCLE_SECURITY_POLICY_NOT_CONFIGURED"},
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
        code = getattr(exc, "code", "CYCLE_SECURITY_ASSERTION_INVALID")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": code}) from exc


@router.post("/objectives/discuss")
async def discuss_objective(
    body: DiscussObjectiveRequest,
    request: Request,
    context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)],
) -> dict[str, Any]:
    """Governed pre-admission chat with optional intelligence layer.

    Intelligence layer (default on): conversation never invokes planner; objective
    intent may invoke planner intake. Legacy flag-off path keeps planner-every-turn.
    Never admits or starts a cycle — human must call /objectives/admit.
    """
    if CyclePermission.COMMAND.value not in context.roles and CyclePermission.ADMIN.value not in context.roles:
        raise HTTPException(status_code=403, detail={"code": "CYCLE_RBAC_FORBIDDEN"})

    authorization = request.headers.get("authorization")
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "AUTHORIZATION_REQUIRED"})

    request_id = request.headers.get("x-request-id")
    message_dicts = [{"role": m.role, "content": m.content} for m in body.messages]
    latest_user = ""
    for item in reversed(message_dicts):
        if item.get("role") == "user":
            latest_user = str(item.get("content") or "")
            break
    attachment_block, resolved_attachments = await build_attachment_context_block(
        attachments=list(body.attachments or []),
        tenant_id=context.tenant_id,
        objective_hint=latest_user,
        settings=settings,
    )
    # Inject only for the model call — do not persist the bulk file dump into transcript.
    model_messages = list(message_dicts)
    if attachment_block:
        model_messages = [
            {
                "role": "user",
                "content": f"[Attached files context]\n{attachment_block}",
            },
            *model_messages,
        ]

    if settings.INTELLIGENCE_LAYER_ENABLED:
        try:
            turn = await run_discuss_turn(
                messages=model_messages,
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                authorization_header=authorization,
                task_classes=_DISCUSS_TASK_CLASSES,
                escalate_to_planner=bool(body.escalate_to_planner),
                active_objective_id=body.active_objective_id,
            )
        except HyeveIntegrationChatError as exc:
            if exc.code == "GOVERNED_ROUTE_DENIED":
                status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            elif exc.code == "OPENROUTER_INSUFFICIENT_CREDITS":
                status_code = status.HTTP_402_PAYMENT_REQUIRED
            elif exc.code == "OPENROUTER_UNAUTHORIZED":
                status_code = status.HTTP_401_UNAUTHORIZED
            elif exc.code == "OPENROUTER_RATE_LIMITED":
                status_code = status.HTTP_429_TOO_MANY_REQUESTS
            else:
                status_code = status.HTTP_502_BAD_GATEWAY
            raise HTTPException(
                status_code=status_code,
                detail={"code": exc.code, "detail": exc.detail},
            ) from exc

        reply = turn.reply
        task_class = turn.task_class
        proposed_scope = list(turn.file_hints or turn.proposed_scope or [])
        ambiguous = turn.ambiguous
        normalized_objective = turn.normalized_objective
        repository_hint = turn.repository_hint
        symbol_hints = list(turn.symbol_hints or [])
        constraints = list(turn.constraints or [])
        provider = turn.resolved_provider or turn.provider
        model = turn.model
        usage = turn.usage
        raw_model_output = turn.raw_model_output
        intake_scope_code = turn.intake_scope_code
        intake_scope_fallback = bool(turn.intake_scope_fallback)
        # If vision already inventoried an image, never let the chat model pretend it's blind.
        vision_ready = [
            item
            for item in resolved_attachments
            if item.get("is_image") and item.get("readable") and item.get("text")
        ]
        if vision_ready:
            lowered_reply = (reply or "").lower()
            if any(
                phrase in lowered_reply
                for phrase in (
                    "cannot view",
                    "can't view",
                    "cannot see",
                    "can't see",
                    "unable to view",
                    "unable to see",
                    "describe the layout",
                    "please describe",
                )
            ):
                names = ", ".join(str(item.get("filename") or "image") for item in vision_ready)
                reply = (
                    f"I analyzed the attached screenshot ({names}) with vision and will use "
                    "that inventory as objective context. Click **Create & run with AIX** so "
                    "execution discovers the required files and operations."
                )
                ambiguous = False
        il_fields = {
            "intent": turn.intent,
            "confidence": turn.confidence,
            "objective_summary": turn.objective_summary,
            "routing_phase": turn.routing_phase,
            "model_role": turn.model_role,
            "planner_invoked": turn.planner_invoked,
            "requires_objective_confirm": turn.requires_objective_confirm,
            "intelligence_layer": True,
            "route_evidence": turn.route_evidence,
            "normalized_objective": normalized_objective,
            "repository_hint": repository_hint,
            "file_hints": proposed_scope,
            "symbol_hints": symbol_hints,
            "constraints": constraints,
            "intake_scope_code": intake_scope_code,
            "intake_scope_fallback": intake_scope_fallback,
        }
    else:
        route_decision = GovernedProviderRouter().resolve(role="planner", model_ref="auto")
        if route_decision.disposition != "ALLOW":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "GOVERNED_ROUTE_DENIED", "reasons": route_decision.reason_codes},
            )
        chat_messages = [ChatMessage(role="system", content=_DISCUSS_SYSTEM_PROMPT)] + [
            ChatMessage(role=m["role"], content=m["content"])  # type: ignore[arg-type]
            for m in model_messages
            if m.get("role") in {"user", "assistant"} and m.get("content")
        ]
        try:
            result = await chat_completion(
                messages=chat_messages,
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                authorization_header=authorization,
                response_format={"type": "json_object"},
            )
        except HyeveIntegrationChatError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": exc.code, "detail": exc.detail},
            ) from exc
        try:
            parsed = json.loads(result.output_text)
            if not isinstance(parsed, dict):
                raise ValueError("model reply JSON is not an object")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": "MODEL_REPLY_NOT_JSON", "raw": result.output_text[:500]},
            ) from exc
        reply = str(parsed.get("reply") or "").strip()
        if not reply:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail={"code": "MODEL_REPLY_EMPTY"})
        task_class = str(parsed.get("task_class") or "").strip().lower()
        ambiguous = bool(parsed.get("ambiguous", False))
        if task_class and task_class not in _DISCUSS_TASK_CLASSES:
            task_class = ""
        file_hint_raw = parsed.get("file_hints")
        if not isinstance(file_hint_raw, list):
            file_hint_raw = parsed.get("proposed_scope") or []
        proposed_scope = [str(item).strip() for item in file_hint_raw if str(item).strip()]
        symbol_hints = [
            str(item).strip() for item in (parsed.get("symbol_hints") or []) if str(item).strip()
        ]
        constraints = [
            str(item).strip() for item in (parsed.get("constraints") or []) if str(item).strip()
        ]
        raw_model_output = str(result.output_text or "")
        model_normalized = str(parsed.get("normalized_objective") or "").strip() or None
        latest_user = ""
        for item in reversed(message_dicts):
            if str(item.get("role") or "").strip().lower() == "user":
                latest_user = str(item.get("content") or "").strip()
                if latest_user:
                    break
        normalized_objective, scope = resolve_normalized_objective(
            original_objective=latest_user,
            normalized_objective=model_normalized,
        )
        intake_scope_code = scope.code
        intake_scope_fallback = bool(scope.used_fallback)
        repository_hint = str(parsed.get("repository_hint") or "").strip() or None
        provider = result.resolved_provider or result.provider
        model = result.model
        usage = dict(result.usage or {})
        il_fields = {
            "intent": "objective",
            "confidence": 1.0,
            "objective_summary": normalized_objective,
            "routing_phase": "planning",
            "model_role": "planner",
            "planner_invoked": True,
            "requires_objective_confirm": False,
            "intelligence_layer": False,
            "route_evidence": {},
            "normalized_objective": normalized_objective,
            "repository_hint": repository_hint,
            "file_hints": proposed_scope,
            "symbol_hints": symbol_hints,
            "constraints": constraints,
            "intake_scope_code": intake_scope_code,
            "intake_scope_fallback": intake_scope_fallback,
        }

    persisted: dict[str, Any] = {}
    db_persisted: dict[str, Any] = {}
    try:
        persisted = persist_discuss_turn(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            thread_id=body.thread_id,
            messages=message_dicts,
            reply=reply,
            task_class=task_class or None,
            proposed_scope=proposed_scope,
            ambiguous=ambiguous,
            provider=provider,
            model=model,
            usage=usage,
            raw_model_output=raw_model_output,
            normalized_objective=normalized_objective if isinstance(normalized_objective, str) else None,
            intake_scope_code=str(il_fields.get("intake_scope_code") or "") or None,
            intake_scope_fallback=bool(il_fields.get("intake_scope_fallback")),
        )
    except Exception:
        persisted = {}

    try:
        db_persisted = await persist_discuss_turn_db(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            thread_id=str(persisted.get("thread_id") or body.thread_id or "") or None,
            messages=message_dicts,
            reply=reply,
            task_class=task_class or None,
            proposed_scope=proposed_scope,
            ambiguous=ambiguous,
            provider=provider,
            model=model,
            usage=usage,
            request_id=request_id,
            roles=sorted(context.roles or ()),
            raw_model_output=raw_model_output,
            normalized_objective=normalized_objective if isinstance(normalized_objective, str) else None,
            intake_scope_code=str(il_fields.get("intake_scope_code") or "") or None,
            intake_scope_fallback=bool(il_fields.get("intake_scope_fallback")),
        )
    except Exception as exc:
        db_persisted = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}

    thread_id = db_persisted.get("thread_id") or persisted.get("thread_id") or body.thread_id

    return {
        "ok": True,
        "reply": reply,
        "task_class": task_class or None,
        "proposed_scope": proposed_scope,
        "file_hints": proposed_scope,
        "symbol_hints": il_fields.get("symbol_hints") or [],
        "constraints": il_fields.get("constraints") or [],
        "normalized_objective": il_fields.get("normalized_objective"),
        "repository_hint": il_fields.get("repository_hint"),
        "intake_scope_code": il_fields.get("intake_scope_code"),
        "intake_scope_fallback": bool(il_fields.get("intake_scope_fallback")),
        "ambiguous": ambiguous,
        "provider": provider,
        "model": model,
        "usage": db_persisted.get("usage") or persisted.get("usage") or usage or {},
        "cumulative_usage": db_persisted.get("cumulative_usage") or persisted.get("cumulative_usage") or {},
        "thread_id": thread_id,
        "turn_id": db_persisted.get("turn_id"),
        "turn_index": db_persisted.get("turn_index"),
        "audit_id": db_persisted.get("audit_id"),
        "session_path": persisted.get("session_path"),
        "transcript_path": persisted.get("transcript_path"),
        "token_usage_path": persisted.get("token_usage_path"),
        "intake_raw_path": persisted.get("intake_raw_path"),
        "discuss_path": persisted.get("discuss_path"),
        "plan_path": persisted.get("plan_path"),
        "objective_path": persisted.get("objective_path"),
        "db_persisted": bool(db_persisted.get("turn_id")),
        "db_error": db_persisted.get("error"),
        "attachments_loaded": len(resolved_attachments),
        "attachments": [
            {
                "id": item.get("id"),
                "filename": item.get("filename"),
                "mime": item.get("mime"),
                "bytes": item.get("bytes"),
                "readable": item.get("readable"),
                "is_image": item.get("is_image"),
                "vision_model": item.get("vision_model"),
                "note": item.get("note"),
            }
            for item in resolved_attachments
        ],
        **il_fields,
    }


@router.get("/objectives/intelligence/metrics")
async def intelligence_layer_metrics(
    context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)],
) -> dict[str, Any]:
    """W10: snapshot of in-process intelligence-layer counters + role routes."""
    if (
        CyclePermission.READ.value not in context.roles
        and CyclePermission.COMMAND.value not in context.roles
        and CyclePermission.ADMIN.value not in context.roles
    ):
        raise HTTPException(status_code=403, detail={"code": "CYCLE_RBAC_FORBIDDEN"})
    registry = load_model_registry()
    exec_route = resolve_intelligence_role("executor")
    ver_route = resolve_intelligence_role("verifier")
    try:
        assert_independent_verification(
            executor_model_ref=exec_route.model_ref,
            verifier_model_ref=ver_route.model_ref,
        )
        independent = True
    except ValueError:
        independent = False
    return {
        "ok": True,
        "enabled": registry.enabled,
        "registry": registry.as_dict(),
        "metrics": intelligence_metrics.snapshot(),
        "execution_routing": {
            "executor_model_ref": exec_route.model_ref,
            "verifier_model_ref": ver_route.model_ref,
            "independent_verification": independent,
            "executor_route": exec_route.decision.disposition,
            "verifier_route": ver_route.decision.disposition,
        },
    }


@router.get("/objectives/discuss")
async def list_discuss_threads(
    context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)],
    limit: Annotated[int, Query(ge=1, le=100)] = 24,
    mine: Annotated[bool, Query(description="When true, only return threads for the current actor.")] = True,
) -> dict[str, Any]:
    """List pre-admission discuss chats so Mission Control can show live threads (not stubs)."""
    if (
        CyclePermission.READ.value not in context.roles
        and CyclePermission.COMMAND.value not in context.roles
        and CyclePermission.ADMIN.value not in context.roles
    ):
        raise HTTPException(status_code=403, detail={"code": "CYCLE_RBAC_FORBIDDEN"})

    try:
        items = await list_discuss_threads_db(
            tenant_id=context.tenant_id,
            limit=limit,
            actor_id=context.actor_id if mine else None,
        )
    except DiscussDbError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "DISCUSS_DB_UNAVAILABLE", "message": str(exc)},
        ) from exc
    return {"ok": True, "items": items, "count": len(items)}


@router.get("/objectives/discuss/{thread_id}")
async def get_discuss_thread(
    thread_id: str,
    context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)],
) -> dict[str, Any]:
    """Load one discuss thread (messages + proposal fields) for resume in the UI."""
    if (
        CyclePermission.READ.value not in context.roles
        and CyclePermission.COMMAND.value not in context.roles
        and CyclePermission.ADMIN.value not in context.roles
    ):
        raise HTTPException(status_code=403, detail={"code": "CYCLE_RBAC_FORBIDDEN"})

    try:
        thread = await get_discuss_thread_db(tenant_id=context.tenant_id, thread_id=thread_id)
    except DiscussDbError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "DISCUSS_DB_UNAVAILABLE", "message": str(exc)},
        ) from exc
    if thread is None:
        raise HTTPException(status_code=404, detail={"code": "DISCUSS_THREAD_NOT_FOUND"})
    return {"ok": True, **thread}


class StartCycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_sha: str = Field(min_length=7)
    budgets: dict[str, int] = Field(default_factory=dict)


class CycleControlRequest(BaseModel):
    # Ignore unknown fields (e.g. legacy clients sending `cycle_id` in the body —
    # the id is already in the URL). `extra="forbid"` previously 422'd Studio approve/resume.
    model_config = ConfigDict(extra="ignore")

    reason: str | None = None
    clarification: dict[str, Any] | None = None
    step_id: str | None = None
    approval_reference: str | None = None
    approval_request_id: str | None = None
    replan_input: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Accepted but unused — some Studio builds still echo the path id into the body.
    cycle_id: str | None = None
    # Approval identity (optional, opt-in): when a caller proves which plan/gate it is
    # approving, a stale value is rejected outright instead of silently accepted
    # because the cycle happens to be WAITING_APPROVAL again. Omitted by legacy
    # callers, who keep today's implicit-current-gate behavior.
    approval_gate_id: str | None = None
    plan_revision: int | None = None
    plan_id: str | None = None


def _require_idempotency(value: str | None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    return normalized


def _expected_version(if_match: str | None, *, required: bool) -> int | None:
    raw = str(if_match or "").strip()
    if not raw:
        if required:
            raise HTTPException(status_code=428, detail={"code": "VERSION_PRECONDITION_REQUIRED"})
        return None
    normalized = raw.removeprefix("W/").strip().strip('"')
    try:
        return int(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "VERSION_PRECONDITION_INVALID"}) from exc


async def _dispatch_command(
    *,
    backend: ObjectiveCycleApiBackend,
    context: CycleApiSecurityContext,
    operation: str,
    resource_id: str | None,
    body: dict[str, Any],
    idempotency_key: str | None,
    if_match: str | None = None,
    version_required: bool = False,
) -> dict[str, Any]:
    if CyclePermission.COMMAND.value not in context.roles and CyclePermission.ADMIN.value not in context.roles:
        raise HTTPException(status_code=403, detail={"code": "CYCLE_RBAC_FORBIDDEN"})
    return await backend.command(
        operation=operation,
        tenant_id=context.tenant_id,
        actor_id=context.actor_id,
        resource_id=resource_id,
        body=body,
        idempotency_key=_require_idempotency(idempotency_key),
        expected_version=_expected_version(if_match, required=version_required),
    )


async def _dispatch_query(
    *,
    backend: ObjectiveCycleApiBackend,
    context: CycleApiSecurityContext,
    operation: str,
    resource_id: str | None = None,
    child_id: str | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await backend.query(
        operation=operation,
        tenant_id=context.tenant_id,
        resource_id=resource_id,
        child_id=child_id,
        filters=dict(filters or {}),
    )


@router.post("/objectives/admit", status_code=201)
async def admit_objective(
    body: AdmitObjectiveRequest,
    request: Request,
    backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)],
    context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    payload = body.model_dump(mode="json")
    # CLI may send thread_id; durable admit reads conversation_id or thread_id.
    if not payload.get("conversation_id") and payload.get("thread_id"):
        payload["conversation_id"] = payload.get("thread_id")
    # Forward live user/session Bearer for WorkHub create (env service token often expires).
    caller_auth = str(request.headers.get("authorization") or "").strip()
    if caller_auth:
        payload["caller_authorization"] = caller_auth
    result = await _dispatch_command(
        backend=backend,
        context=context,
        operation="admit_objective",
        resource_id=None,
        body=payload,
        idempotency_key=idempotency_key,
    )
    if not result.get("ok", True):
        err = result.get("error") if isinstance(result.get("error"), dict) else {}
        code = str((err or {}).get("code") or "ADMIT_FAILED")
        if code == "WORKHUB_TASK_CREATE_FORBIDDEN":
            status_code = status.HTTP_403_FORBIDDEN
        elif code == "WORKHUB_TASK_CREATE_UNAUTHORIZED":
            status_code = status.HTTP_401_UNAUTHORIZED
        elif "WORKHUB" in code:
            status_code = status.HTTP_502_BAD_GATEWAY
        else:
            status_code = status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=result)
    return result


@router.post("/objectives/{objective_id}/cycles", status_code=201)
async def start_cycle(
    objective_id: str,
    body: StartCycleRequest,
    backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)],
    context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    return await _dispatch_command(
        backend=backend,
        context=context,
        operation="start_cycle",
        resource_id=objective_id,
        body=body.model_dump(mode="json"),
        idempotency_key=idempotency_key,
    )


async def _cycle_control(
    cycle_id: str,
    operation: str,
    body: CycleControlRequest,
    backend: ObjectiveCycleApiBackend,
    context: CycleApiSecurityContext,
    idempotency_key: str | None,
    if_match: str | None,
    request: Request | None = None,
    *,
    version_required: bool = True,
) -> dict[str, Any]:
    payload = body.model_dump(mode="json")
    # Forward live session Bearer for WorkHub mark-done (env service token may be empty/expired).
    if request is not None:
        caller_auth = str(request.headers.get("authorization") or "").strip()
        if caller_auth:
            payload["caller_authorization"] = caller_auth
    # Drop unused echo field so it never reaches the durable backend body.
    payload.pop("cycle_id", None)
    return await _dispatch_command(
        backend=backend,
        context=context,
        operation=operation,
        resource_id=cycle_id,
        body=payload,
        idempotency_key=idempotency_key,
        if_match=if_match,
        version_required=version_required,
    )


@router.post("/cycles/{cycle_id}/clarify")
async def clarify_cycle(cycle_id: str, body: CycleControlRequest, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)], idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None, if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> dict[str, Any]:
    return await _cycle_control(cycle_id, "clarify_cycle", body, backend, context, idempotency_key, if_match)


@router.post("/cycles/{cycle_id}/cancel")
async def cancel_cycle(cycle_id: str, body: CycleControlRequest, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)], idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None, if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> dict[str, Any]:
    return await _cycle_control(cycle_id, "cancel_cycle", body, backend, context, idempotency_key, if_match)


@router.post("/cycles/{cycle_id}/reject")
async def reject_cycle(cycle_id: str, body: CycleControlRequest, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)], idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None, if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> dict[str, Any]:
    return await _cycle_control(cycle_id, "reject_cycle", body, backend, context, idempotency_key, if_match)


@router.post("/cycles/{cycle_id}/block")
async def block_cycle(cycle_id: str, body: CycleControlRequest, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)], idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None, if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> dict[str, Any]:
    return await _cycle_control(cycle_id, "block_cycle", body, backend, context, idempotency_key, if_match)


@router.post("/cycles/{cycle_id}/resume")
async def resume_cycle(cycle_id: str, body: CycleControlRequest, request: Request, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)], idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None, if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> dict[str, Any]:
    # Studio auto-approve often omits If-Match; do not 428 the happy path.
    return await _cycle_control(cycle_id, "resume_cycle", body, backend, context, idempotency_key, if_match, request, version_required=False)


@router.post("/cycles/{cycle_id}/dispatch")
async def dispatch_ready_step(cycle_id: str, body: CycleControlRequest, request: Request, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)], idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> dict[str, Any]:
    """Claim and execute exactly one persisted ready step."""
    return await _cycle_control(
        cycle_id, "dispatch_ready_step", body, backend, context,
        idempotency_key, None, request, version_required=False,
    )


@router.post("/cycles/{cycle_id}/retry-step")
async def retry_step(cycle_id: str, body: CycleControlRequest, request: Request, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)], idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None, if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> dict[str, Any]:
    return await _cycle_control(cycle_id, "retry_step", body, backend, context, idempotency_key, if_match, request)


@router.post("/cycles/{cycle_id}/replan-from-gaps")
async def replan_from_gaps(cycle_id: str, body: CycleControlRequest, request: Request, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)], idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None, if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> dict[str, Any]:
    return await _cycle_control(cycle_id, "replan_from_gaps", body, backend, context, idempotency_key, if_match, request)


@router.post("/cycles/{cycle_id}/repair")
async def repair_cycle(cycle_id: str, body: CycleControlRequest, request: Request, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)], idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None, if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> dict[str, Any]:
    return await _cycle_control(cycle_id, "repair_cycle", body, backend, context, idempotency_key, if_match, request)


@router.post("/cycles/{cycle_id}/reverify")
async def reverify_and_terminalize(cycle_id: str, body: CycleControlRequest, request: Request, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)], idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None, if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> dict[str, Any]:
    return await _cycle_control(cycle_id, "reverify_and_terminalize", body, backend, context, idempotency_key, if_match, request)


@router.post("/cycles/{cycle_id}/replan")
async def replan_cycle(cycle_id: str, body: CycleControlRequest, request: Request, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)], idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None, if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> dict[str, Any]:
    return await _cycle_control(cycle_id, "replan_cycle", body, backend, context, idempotency_key, if_match, request)


@router.post("/cycles/{cycle_id}/approval-reference")
async def attach_approval_reference(cycle_id: str, body: CycleControlRequest, request: Request, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)], idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None, if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> dict[str, Any]:
    if not str(body.approval_reference or "").strip():
        raise HTTPException(status_code=422, detail={"code": "APPROVAL_REFERENCE_REQUIRED"})
    return await _cycle_control(
        cycle_id, "validate_approval_reference", body, backend, context, idempotency_key, if_match, request,
        version_required=False,
    )


@router.get("/objectives")
async def list_objectives(backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)], limit: Annotated[int, Query(ge=1, le=100)] = 50, cursor: str | None = None, state: str | None = None) -> dict[str, Any]:
    return await _dispatch_query(backend=backend, context=context, operation="list_objectives", filters={"limit": limit, "cursor": cursor, "state": state})


@router.get("/objectives/{objective_id}")
async def get_objective(objective_id: str, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)]) -> dict[str, Any]:
    return await _dispatch_query(backend=backend, context=context, operation="get_objective", resource_id=objective_id)


@router.get("/cycles")
async def list_cycles(backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)], limit: Annotated[int, Query(ge=1, le=100)] = 50, cursor: str | None = None, state: str | None = None) -> dict[str, Any]:
    return await _dispatch_query(backend=backend, context=context, operation="list_cycles", filters={"limit": limit, "cursor": cursor, "state": state})


@router.get("/cycles/{cycle_id}")
async def get_cycle(cycle_id: str, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)]) -> dict[str, Any]:
    result = await _dispatch_query(backend=backend, context=context, operation="get_cycle", resource_id=cycle_id)
    if isinstance(result, dict) and result.get("ok") is False:
        code = str((result.get("error") or {}).get("code") or "CYCLE_NOT_FOUND")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if code == "CYCLE_NOT_FOUND" else status.HTTP_400_BAD_REQUEST,
            detail=result,
        )
    return result


@router.get("/cycles/{cycle_id}/preview/{artifact_name}")
async def preview_cycle_artifact(
    cycle_id: str,
    artifact_name: str,
    backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)],
    context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)],
) -> FileResponse:
    """Serve proposed HTML/CSS from the cycle workspace (post real-execute)."""
    resolver = getattr(backend, "resolve_preview_artifact", None)
    if not callable(resolver):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"code": "PREVIEW_NOT_SUPPORTED"},
        )
    path = resolver(tenant_id=context.tenant_id, cycle_id=cycle_id, artifact_name=artifact_name)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PREVIEW_ARTIFACT_NOT_FOUND"},
        )
    media = "text/css; charset=utf-8" if artifact_name.endswith(".css") else "text/html; charset=utf-8"
    return FileResponse(path, media_type=media, filename=artifact_name)


def _query_operation(path_suffix: str) -> str:
    return f"get_{path_suffix.replace('-', '_')}"


@router.get("/cycles/{cycle_id}/diff")
async def get_cycle_diff(cycle_id: str, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)]) -> dict[str, Any]:
    """Real git diff of the target repository for this cycle's source-edit result,
    split into `changed_source_files` vs. internal `run_artifacts` — never merged. See
    `real_execute.py`'s source-edit branch and `completion_gates.split_changed_paths`."""
    return await _dispatch_query(backend=backend, context=context, operation="get_diff", resource_id=cycle_id)


@router.get("/cycles/{cycle_id}/plan")
async def get_plan(cycle_id: str, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)]) -> dict[str, Any]:
    return await _dispatch_query(backend=backend, context=context, operation="get_plan", resource_id=cycle_id)


@router.get("/cycles/{cycle_id}/steps")
async def get_steps(cycle_id: str, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)]) -> dict[str, Any]:
    return await _dispatch_query(backend=backend, context=context, operation="get_steps", resource_id=cycle_id)


@router.get("/cycles/{cycle_id}/steps/{step_id}")
async def get_step(cycle_id: str, step_id: str, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)]) -> dict[str, Any]:
    return await _dispatch_query(backend=backend, context=context, operation="get_step", resource_id=cycle_id, child_id=step_id)


def _register_cycle_query(path_suffix: str) -> None:
    async def handler(cycle_id: str, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)]) -> dict[str, Any]:
        return await _dispatch_query(
            backend=backend,
            context=context,
            operation=_query_operation(path_suffix),
            resource_id=cycle_id,
        )

    handler.__name__ = _query_operation(path_suffix)
    router.add_api_route(f"/cycles/{{cycle_id}}/{path_suffix}", handler, methods=["GET"])


for _suffix in ("attempts", "budgets", "events", "agents", "evidence", "review-packet"):
    _register_cycle_query(_suffix)


@router.get("/cycles/{cycle_id}/replay/{evidence_id}")
async def replay_evidence(cycle_id: str, evidence_id: str, backend: Annotated[ObjectiveCycleApiBackend, Depends(get_cycle_api_backend)], context: Annotated[CycleApiSecurityContext, Depends(get_cycle_security_context)]) -> dict[str, Any]:
    return await _dispatch_query(backend=backend, context=context, operation="replay_evidence", resource_id=cycle_id, child_id=evidence_id)
