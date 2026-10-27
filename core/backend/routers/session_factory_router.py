"""Session Factory routes skeleton (Wave 15 Phase 2, PR bundle).

Mounted in ``backend.main`` as EXPERIMENTAL (Lead-approved Wave14.5).
Redis injection, ``aix_runs`` insert, and pointer resolution are still
TODO and fail closed (501). Endpoints:

- ``POST /v1/aix/sessions`` — create ``aix_runs`` entry (existing
  table, no migration) + write Redis capsule, return ``envelope_id``.
- ``POST /v1/aix/intent`` — web/voice intent text → session envelope →
  caller creates the WorkHub mandate with
  ``Idempotency-Key = envelope_id`` (mandate creation stays in WorkHub;
  this router never mints mandates).
- ``POST /v1/aix/sessions/{id}/hydrate`` — resolve capsule pointers
  (lane-state, CodeIntel, WorkHub, recent sessions) into a boot bundle.
  Muse is spawned with the positional ``envelope_id`` only and reads
  everything else here (tenant-scoped JWT, never full JSON on CLI).

Auth follows the CI10 lane pattern (401 on missing header).
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from backend.session_factory.workhub_binding import BindingRefused as BindingRefusedError
from backend.session_factory.capsule import (
    build_capsule,
    build_run_record,
    read_capsule,
    recent_sessions,
    write_capsule,
)
from backend.session_factory.hydration import (
    resolve_codeintel,
    resolve_workhub,
)
from backend.session_factory.stores import (
    ActivationAuthenticator,
    ActivationEventBridge,
    BindingVerifier,
    RunStore,
    get_activation_authenticator,
    get_activation_bridge,
    get_activation_store,
    get_binding_verifier,
    get_redis_client,
    get_run_store,
)

_CODEINTEL_FETCHER = None
_WORKHUB_FETCHER = None


def configure_hydration_fetchers(*, codeintel=None, workhub=None) -> None:
    """Attach pointer resolvers (tests / deploy wiring)."""
    global _CODEINTEL_FETCHER, _WORKHUB_FETCHER
    _CODEINTEL_FETCHER = codeintel
    _WORKHUB_FETCHER = workhub


def reset_hydration_fetchers() -> None:
    configure_hydration_fetchers(codeintel=None, workhub=None)


router = APIRouter(prefix="/v1/aix", tags=["session factory"])


class SessionCreate(BaseModel):
    tenant_id: str
    intent: str
    lane_checkpoint: str = ""
    lane_tag: str = ""
    persona_bindings: list[str] = Field(default_factory=list)


class IntentCreate(BaseModel):
    tenant_id: str
    intent_text: str
    voice_transcript_ref: str | None = None


class SelectionAccepted(BaseModel):
    tenant_id: str
    objective_id: str
    cycle_id: str
    selection_id: str
    mandate_id: str
    session_id: str
    worker_id: str
    accepted: Literal[True]
    workflow_version: int = Field(default=1, ge=1)
    candidate_sha: str = Field(min_length=7)
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    correlation_id: str = Field(min_length=1)
    model: str = "Muse"
    executor: str = "muse-lane"
    max_repairs: int = Field(default=2, ge=0, le=10)
    workhub_binding: dict[str, Any] = Field(default_factory=dict)


class IndependentVerdict(BaseModel):
    tenant_id: str
    activation_id: str
    verdict_id: str
    outcome: Literal["pass", "fail", "blocked"]
    worker_id: str
    reviewer_id: str
    evidence_ref: str
    result_id: str


class ActivationResult(BaseModel):
    tenant_id: str
    activation_id: str
    result_id: str
    worker_id: str
    status: Literal["SUCCEEDED", "FAILED"]
    evidence_ref: str
    # Lane-ops #23 repair binding (optional, round 0 when absent):
    # the exact fail-verdict allocation plus its repair number whose
    # completed release authorizes this result's round.
    repair_number: int | None = Field(default=None, ge=1)
    repair_verdict_id: str = ""


def _require_auth(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SESSION_AUTH_REQUIRED",
        )
    return authorization


def _event_id(kind: str, tenant_id: str, source_id: str) -> str:
    digest = hashlib.sha256(
        f"{kind}\x1f{tenant_id}\x1f{source_id}".encode()
    ).hexdigest()
    return f"activation-{kind}-{digest[:32]}"


async def _principal(
    authorization: str | None, authenticator: ActivationAuthenticator
) -> Any:
    try:
        return await authenticator(_require_auth(authorization))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc


def _require_identity(principal: Any, *, tenant_id: str, actor_id: str) -> None:
    if principal.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SESSION_ACTIVATION_TENANT_MISMATCH",
        )
    if principal.actor_id != actor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SESSION_ACTIVATION_ACTOR_MISMATCH",
        )


RedisDep = Annotated[Any, Depends(get_redis_client)]
StoreDep = Annotated[RunStore, Depends(get_run_store)]
ActivationBridgeDep = Annotated[ActivationEventBridge, Depends(get_activation_bridge)]
ActivationAuthDep = Annotated[
    ActivationAuthenticator, Depends(get_activation_authenticator)
]


@router.post("/activation/selection", status_code=status.HTTP_202_ACCEPTED)
async def activate_accepted_selection(
    body: SelectionAccepted,
    bridge: ActivationBridgeDep,
    authenticator: ActivationAuthDep,
    verifier: Annotated[BindingVerifier, Depends(get_binding_verifier)],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = await _principal(authorization, authenticator)
    _require_identity(principal, tenant_id=body.tenant_id, actor_id=body.worker_id)
    if not body.workhub_binding:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="WORKHUB_BINDING_REQUIRED",
        )
    if str(body.workhub_binding.get("executor_sub") or "") != body.worker_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SESSION_ACTIVATION_EXECUTOR_MISMATCH",
        )
    try:
        verified = await verifier(dict(body.workhub_binding))
    except BindingRefusedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    # Bind every effect-driving outer field to the canonical verified
    # inner selection before enqueue: an approved nested binding must not
    # carry a substituted outer candidate/cycle into the inbox. Verifiers
    # that establish no selection leave nothing to bind to; the
    # coordinator re-binds against its own proof before any effect.
    canonical = verified.get("selection") if isinstance(verified, dict) else None
    if isinstance(canonical, dict) and canonical:
        from backend.session_factory.activation import SELECTION_BINDING_FIELDS

        if canonical != (body.workhub_binding or {}).get("selection"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="SELECTION_BINDING_MISMATCH",
            )
        for field in SELECTION_BINDING_FIELDS:
            if getattr(body, field) != canonical.get(field):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="SELECTION_BINDING_MISMATCH",
                )
    event_id = _event_id("selection", body.tenant_id, body.selection_id)
    payload = {"event_id": event_id, **body.model_dump()}
    try:
        await bridge.enqueue(event_id, "accepted_selection", payload)
        return {"event_id": event_id, "status": "QUEUED"}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.post("/activation/verdict", status_code=status.HTTP_202_ACCEPTED)
async def activate_after_verdict(
    body: IndependentVerdict,
    bridge: ActivationBridgeDep,
    authenticator: ActivationAuthDep,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = await _principal(authorization, authenticator)
    _require_identity(principal, tenant_id=body.tenant_id, actor_id=body.reviewer_id)
    event_id = _event_id("verdict", body.tenant_id, body.verdict_id)
    payload = {"event_id": event_id, **body.model_dump()}
    try:
        await bridge.enqueue(event_id, "independent_verdict", payload)
        return {"event_id": event_id, "status": "QUEUED"}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.get("/activation/by-attempt", status_code=status.HTTP_200_OK)
async def read_activation_by_attempt(
    tenant_id: str,
    project_id: str,
    workhub_attempt_id: str,
    store: Annotated[Any, Depends(get_activation_store)],
    authenticator: ActivationAuthDep,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Owner readback of one activation by WorkHub attempt (lane-ops #23).

    The maintained controller supervision observes authoritative
    activation/workflow state through this read-only route: no
    activation is ever inferred from queue or Temporal listings.
    """
    principal = await _principal(authorization, authenticator)
    if principal.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SESSION_ACTIVATION_TENANT_MISMATCH",
        )
    activation_ids = await store.find_attempt_activation(
        tenant_id, project_id, workhub_attempt_id)
    if not activation_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SESSION_ACTIVATION_UNKNOWN",
        )
    if len(activation_ids) != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="SESSION_ACTIVATION_AMBIGUOUS",
        )
    record = await store.get_activation(activation_ids[0])
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SESSION_ACTIVATION_UNKNOWN",
        )
    _require_identity(
        principal, tenant_id=record["tenant_id"],
        actor_id=record["worker_id"],
    )
    # Lane-ops #23: surface the claimed terminal result row (if any) as
    # the authoritative workflow proof supervision carries end to end.
    # No result yet means no proof (observers hold); distinct result
    # rows mean no single proof (409, observers hold).
    try:
        terminal_result = await store.get_result_by_activation(
            activation_ids[0])
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    if terminal_result is not None:
        record = {**record, "activation_result": terminal_result}
    return record


@router.get("/activation/{activation_id}", status_code=status.HTTP_200_OK)
async def read_activation(
    activation_id: str,
    store: Annotated[Any, Depends(get_activation_store)],
    authenticator: ActivationAuthDep,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Owner readback of one activation record (lane-ops #23 supervision)."""
    principal = await _principal(authorization, authenticator)
    record = await store.get_activation(activation_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SESSION_ACTIVATION_UNKNOWN",
        )
    _require_identity(
        principal, tenant_id=record["tenant_id"], actor_id=record["worker_id"]
    )
    return record


@router.post("/activation/result", status_code=status.HTTP_202_ACCEPTED)
async def record_activation_result(
    body: ActivationResult,
    bridge: ActivationBridgeDep,
    authenticator: ActivationAuthDep,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = await _principal(authorization, authenticator)
    _require_identity(principal, tenant_id=body.tenant_id, actor_id=body.worker_id)
    event_id = _event_id("result", body.tenant_id, body.result_id)
    payload = {"event_id": event_id, **body.model_dump()}
    try:
        await bridge.enqueue(event_id, "attempt_result", payload)
        return {"event_id": event_id, "status": "QUEUED"}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(
    body: SessionCreate,
    redis_client: RedisDep,
    run_store: StoreDep,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_auth(authorization)
    envelope_id = f"env-{uuid.uuid4().hex[:12]}"
    capsule = build_capsule(
        session_id=envelope_id,
        tenant_id=body.tenant_id,
        intent=body.intent,
        lane_checkpoint=body.lane_checkpoint,
        lane_tag=body.lane_tag,
        persona_bindings=body.persona_bindings,
    )
    try:
        run_store.insert_run(
            build_run_record(
                envelope_id=envelope_id,
                tenant_id=body.tenant_id,
                intent=body.intent,
                trace_id=f"tr-{uuid.uuid4().hex[:12]}",
            )
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="SESSION_PERSIST_FAILED",
        ) from exc
    write_capsule(redis_client, capsule)
    return {"envelope_id": envelope_id, "capsule_version": 1}


@router.post("/intent", status_code=status.HTTP_201_CREATED)
def create_intent(
    body: IntentCreate,
    redis_client: RedisDep,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_auth(authorization)
    envelope_id = f"env-{uuid.uuid4().hex[:12]}"
    capsule = build_capsule(
        session_id=envelope_id,
        tenant_id=body.tenant_id,
        intent=body.intent_text,
        lane_checkpoint="",
        lane_tag="",
        voice_transcript_ref=body.voice_transcript_ref,
    )
    write_capsule(redis_client, capsule)
    return {
        "envelope_id": envelope_id,
        "idempotency_key": envelope_id,
        "next": "POST WorkHub mandate with Idempotency-Key=envelope_id",
    }


@router.get("/sessions/recent")
def recent_session_list(
    redis_client: RedisDep,
    authorization: str | None = Header(default=None),
    tenant_id: str = "",
) -> dict[str, Any]:
    """Last-3 sessions with capsules (null entry = expired since list)."""
    _require_auth(authorization)
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="SESSION_TENANT_REQUIRED",
        )
    ids = recent_sessions(redis_client, tenant_id)
    return {
        "tenant_id": tenant_id,
        "sessions": [
            {"session_id": sid, "capsule": read_capsule(redis_client, sid)}
            for sid in ids
        ],
    }


@router.post("/sessions/{session_id}/hydrate")
async def hydrate_session(
    session_id: str,
    redis_client: RedisDep,
    authorization: str | None = Header(default=None),
    tenant_id: str = "",
    project_id: str = "",
) -> dict[str, Any]:
    _require_auth(authorization)
    capsule = read_capsule(redis_client, session_id)
    if capsule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SESSION_NOT_FOUND",
        )
    if tenant_id and capsule.get("tenant_id") != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SESSION_TENANT_MISMATCH",
        )
    session_tenant = str(capsule.get("tenant_id") or "")
    kinds = ["context_pack_document", "generated_summary"]
    codeintel = await resolve_codeintel(
        _CODEINTEL_FETCHER,
        tenant_id=session_tenant,
        project_id=project_id,
        kinds=kinds,
    )
    workhub = await resolve_workhub(_WORKHUB_FETCHER, tenant_id=session_tenant)
    return {
        "capsule": capsule,
        "recent": recent_sessions(redis_client, capsule["tenant_id"]),
        "resolved": {"codeintel": codeintel, "workhub": workhub},
    }
