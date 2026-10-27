from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, Field

from backend.aix_orchestrator_v2.models import AixOrchestratorV2RunState
from backend.aix_orchestrator_v2.run_summary import build_run_summary
from backend.aix_orchestrator_v2.service import AixOrchestratorV2Service
from backend.aix_orchestrator_v2.state_store import state_store
from backend.aix_core.runtime.objective.dual_surface import decide_post_admit_continuation
from backend.errors.aix_errors import aix_http_error
from backend.services.aix_operator_run_api_service import (
    OperatorRunApiError,
    get_operator_run_evidence_bundle,
    get_operator_run_replay_by_evidence_id,
)
from backend.services.aix_review_packet_handoff_service import (
    build_review_packet_from_evidence_bundle_v2,
    validate_review_packet_handoff,
)

router = APIRouter(prefix="/aix", tags=["aix mvp product api"])

SUPPORTED_MVP_TASK_KINDS = {
    "docs_only_change",
    "test_fixture_change",
    "single_file_helper",
    "test_generation",
    "verification_only",
}

# C004 / I-T003: all MVP mutation paths are retired.  Auth header is still
# checked first (fail-closed on missing Bearer) so retirement is visible only
# to callers that pass authentication.  This mirrors execute_aix_mvp_task.
MVP_ADMIT_RETIRED: bool = True
MVP_ADMIT_RETIREMENT_CODE: str = "MVP_ADMIT_RETIRED"
MVP_REPLAY_RETIRED: bool = True
MVP_REPLAY_RETIREMENT_CODE: str = "MVP_REPLAY_RETIRED"
MVP_REVIEW_DECISION_RETIRED: bool = True
MVP_REVIEW_DECISION_RETIREMENT_CODE: str = "MVP_REVIEW_DECISION_RETIRED"

MVP_REVIEW_DECISION_TO_STATE = {
    "approve": "review_accepted",
    "reject": "review_rejected",
    "request_changes": "changes_requested",
}

FORBIDDEN_REVIEW_ACTOR_TYPES = {"executor", "service", "automated", "persona", "model", "tool"}
FORBIDDEN_REVIEW_ACTOR_ROLES = {"executor", "service", "automated", "persona", "model", "tool"}


class AixMvpTaskAdmissionRequest(BaseModel):
    id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    repo_ref: str = Field(min_length=1)
    task_kind: str = Field(min_length=1)
    description: str = Field(min_length=1)
    risk_hint: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    bounded_scope: list[str] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AixMvpTaskExecutionRequest(AixMvpTaskAdmissionRequest):
    project_id: str = Field(min_length=1)
    task_number: int = Field(ge=1)
    workspace_root: str = Field(min_length=1)
    repo_key: str = Field(default="hyeve-aix-service", min_length=1)
    explicit_profile_id: str | None = Field(default=None, min_length=1)
    mode: Literal["inline"] = "inline"
    writeback: bool = False


class AixMvpReviewDecisionRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    review_packet_version: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    decision: Literal["approve", "reject", "request_changes"]
    human_actor_id: str = Field(min_length=1)
    actor_type: str = Field(default="human", min_length=1)
    actor_role: str = Field(default="human", min_length=1)
    rationale: str = Field(min_length=1)


class AixMvpEvidenceReplayRequest(BaseModel):
    replay_mode: Literal["read_only_comparison"] = "read_only_comparison"


def _require_auth_header(authorization: str | None) -> None:
    if not authorization or not authorization.strip():
        raise aix_http_error(
            http_status=status.HTTP_401_UNAUTHORIZED,
            code="AUTH_FAILED",
            sub_code="AUTH_MISSING",
            detail="Authorization header required",
        )


def _retired_mvp_admit_guard(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    """Reject retired MVP admit before FastAPI validates the legacy payload."""
    _require_auth_header(authorization)
    raise aix_http_error(
        http_status=status.HTTP_410_GONE,
        code=MVP_ADMIT_RETIREMENT_CODE,
        sub_code="USE_OBJECTIVE_CYCLE",
        detail="MVP admit path retired. Use canonical objective_cycle APIs.",
    )


def _retired_mvp_mutation_guard(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    """Reject retired writes before FastAPI validates their legacy payloads."""
    _require_auth_header(authorization)
    raise aix_http_error(
        http_status=status.HTTP_410_GONE,
        code="MVP_MUTATION_RETIRED",
        sub_code="USE_OBJECTIVE_CYCLE",
        detail="MVP mutation path retired. Use canonical objective_cycle APIs.",
    )


def _require_header(value: str | None, *, name: str, sub_code: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise aix_http_error(
            http_status=status.HTTP_400_BAD_REQUEST,
            code="AIX_MVP_CONTRACT_FAILED",
            sub_code=sub_code,
            detail=f"{name} header required",
        )
    return normalized


def _require_tenant_header(*, tenant_id: str | None, hyeve_tenant_id: str | None) -> str:
    return _require_header(
        tenant_id or hyeve_tenant_id,
        name="X-Tenant-ID or X-Hyeve-Tenant-Id",
        sub_code="TENANT_ID_REQUIRED",
    )


def _require_tenant_match(*, header_tenant_id: str, body_tenant_id: str) -> None:
    if header_tenant_id != body_tenant_id:
        raise aix_http_error(
            http_status=status.HTTP_403_FORBIDDEN,
            code="AIX_MVP_CONTRACT_FAILED",
            sub_code="TENANT_MISMATCH",
            detail="Task tenant must match trusted tenant header.",
        )


def _require_supported_task_kind(task_kind: str) -> None:
    if task_kind not in SUPPORTED_MVP_TASK_KINDS:
        raise aix_http_error(
            http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="AIX_MVP_ADMISSION_REJECTED",
            sub_code="UNSUPPORTED_TASK_KIND",
            detail="Task kind is outside the restricted AIX MVP scope.",
            context={"task_kind": task_kind, "supported_task_kinds": sorted(SUPPORTED_MVP_TASK_KINDS)},
        )


def _metadata_bool(metadata: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "y"}:
            return True
    return False


def _metadata_int(metadata: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = metadata.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _reject_admission(*, sub_code: str, detail: str, context: dict[str, Any] | None = None) -> None:
    raise aix_http_error(
        http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="AIX_MVP_ADMISSION_REJECTED",
        sub_code=sub_code,
        detail=detail,
        context=dict(context or {}),
    )


def _looks_like_test_path(path: str) -> bool:
    normalized = str(path or "").strip().replace("\\", "/").lower()
    if not normalized:
        return False
    name = normalized.rsplit("/", 1)[-1]
    return (
        "/tests/" in f"/{normalized}"
        or normalized.startswith("tests/")
        or normalized.startswith("test/")
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".spec.ts")
        or name.endswith(".test.ts")
        or name.endswith(".spec.tsx")
        or name.endswith(".test.tsx")
    )


def _require_task_kind_policy(body: AixMvpTaskAdmissionRequest) -> None:
    metadata = dict(body.metadata or {})
    environment = str(
        metadata.get("target_environment")
        or metadata.get("environment")
        or metadata.get("mutation_workspace")
        or ""
    ).strip().lower()
    if (
        environment in {"prod", "production", "live"}
        or _metadata_bool(metadata, "production_mutation_requested", "production_deployment", "mutate_production")
    ):
        _reject_admission(
            sub_code="PRODUCTION_MUTATION_FORBIDDEN",
            detail="AIX MVP admission forbids production mutation requests.",
            context={"task_kind": body.task_kind, "target_environment": environment or None},
        )

    if body.task_kind == "single_file_helper":
        if len(body.bounded_scope) != 1:
            _reject_admission(
                sub_code="SINGLE_FILE_SCOPE_REQUIRED",
                detail="single_file_helper requires exactly one bounded scope file.",
                context={"bounded_scope_count": len(body.bounded_scope)},
            )
        max_delta = _metadata_int(metadata, "max_diff_lines", "max_loc_delta", "declared_max_loc_delta")
        if max_delta is None:
            _reject_admission(
                sub_code="SINGLE_FILE_LOC_BOUND_REQUIRED",
                detail="single_file_helper requires a declared maximum diff/LOC delta.",
            )
        if max_delta > 50:
            _reject_admission(
                sub_code="SINGLE_FILE_LOC_BOUND_EXCEEDED",
                detail="single_file_helper may not exceed 50 diff/LOC lines.",
                context={"declared_max_delta": max_delta},
            )

    if body.task_kind == "test_generation":
        if not body.bounded_scope:
            _reject_admission(
                sub_code="TEST_GENERATION_SCOPE_REQUIRED",
                detail="test_generation requires at least one bounded test file path.",
            )
        non_test_paths = [path for path in body.bounded_scope if not _looks_like_test_path(path)]
        if non_test_paths:
            _reject_admission(
                sub_code="TEST_GENERATION_TEST_PATH_REQUIRED",
                detail="test_generation bounded scope must target tests only.",
                context={"non_test_paths": non_test_paths},
            )
        if _metadata_bool(metadata, "source_mutation_allowed", "production_source_mutation_allowed"):
            _reject_admission(
                sub_code="TEST_GENERATION_SOURCE_MUTATION_FORBIDDEN",
                detail="test_generation may not authorize source mutation.",
            )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _state_tenant_id(state: AixOrchestratorV2RunState) -> str:
    packet = state.packet if isinstance(state.packet, dict) else {}
    runtime = packet.get("runtime") if isinstance(packet.get("runtime"), dict) else {}
    task_payload = state.task_payload if isinstance(state.task_payload, dict) else {}
    for candidate in (
        runtime.get("tenant_id"),
        task_payload.get("tenant_id"),
        task_payload.get("tenantId"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _state_project_id(state: AixOrchestratorV2RunState) -> str:
    task_payload = state.task_payload if isinstance(state.task_payload, dict) else {}
    packet = state.packet if isinstance(state.packet, dict) else {}
    runtime = packet.get("runtime") if isinstance(packet.get("runtime"), dict) else {}
    for candidate in (
        task_payload.get("project_id"),
        task_payload.get("projectId"),
        runtime.get("project_id"),
        runtime.get("projectId"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _state_review_packet_version(state: AixOrchestratorV2RunState) -> str:
    validator_ledger = state.validator_ledger if isinstance(state.validator_ledger, dict) else {}
    packet = state.packet if isinstance(state.packet, dict) else {}
    review_packet = packet.get("review_packet") if isinstance(packet.get("review_packet"), dict) else {}
    task_payload = state.task_payload if isinstance(state.task_payload, dict) else {}
    for candidate in (
        validator_ledger.get("review_packet_version"),
        review_packet.get("version"),
        packet.get("review_packet_version"),
        task_payload.get("review_packet_version"),
        state.run_id,
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _state_evidence_bundle_v2(state: AixOrchestratorV2RunState) -> dict[str, Any]:
    packet = state.packet if isinstance(state.packet, dict) else {}
    validator_ledger = state.validator_ledger if isinstance(state.validator_ledger, dict) else {}
    for candidate in (
        packet.get("evidence_bundle_v2"),
        validator_ledger.get("evidence_bundle_v2"),
        _as_nested_evidence_bundle_v2(packet.get("writeback")),
        _as_nested_evidence_bundle_v2(validator_ledger.get("writeback")),
    ):
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _as_nested_evidence_bundle_v2(value: Any) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    evidence_bundle = payload.get("evidence_bundle") if isinstance(payload.get("evidence_bundle"), dict) else {}
    nested = evidence_bundle.get("evidence_bundle_v2") if isinstance(evidence_bundle.get("evidence_bundle_v2"), dict) else {}
    return nested


def _require_state_for_tenant(*, state: AixOrchestratorV2RunState | None, tenant_id: str) -> AixOrchestratorV2RunState:
    if state is None or _state_tenant_id(state) != tenant_id:
        raise aix_http_error(
            http_status=status.HTTP_404_NOT_FOUND,
            code="AIX_MVP_TASK_NOT_FOUND",
            sub_code="TASK_ID_NOT_FOUND",
            detail="Task not found.",
        )
    return state


def _require_human_review_actor(*, trusted_actor_id: str, body: AixMvpReviewDecisionRequest) -> None:
    if trusted_actor_id != body.human_actor_id:
        raise aix_http_error(
            http_status=status.HTTP_403_FORBIDDEN,
            code="AIX_MVP_REVIEW_REJECTED",
            sub_code="HUMAN_ACTOR_MISMATCH",
            detail="Review decision human actor must match trusted actor header.",
        )
    actor_type = body.actor_type.strip().lower()
    actor_role = body.actor_role.strip().lower()
    if actor_type != "human" or actor_type in FORBIDDEN_REVIEW_ACTOR_TYPES or actor_role in FORBIDDEN_REVIEW_ACTOR_ROLES:
        raise aix_http_error(
            http_status=status.HTTP_403_FORBIDDEN,
            code="AIX_MVP_REVIEW_REJECTED",
            sub_code="HUMAN_REVIEW_REQUIRED",
            detail="Only a trusted human actor may record AIX review decisions.",
        )


def _review_decision_fingerprint(*, body: AixMvpReviewDecisionRequest, review_state: str, actor_id: str) -> dict[str, Any]:
    return {
        "tenant_id": body.tenant_id,
        "project_id": body.project_id,
        "task_id": body.task_id,
        "run_id": body.run_id,
        "review_packet_version": body.review_packet_version,
        "decision_id": body.decision_id,
        "decision": body.decision,
        "review_state": review_state,
        "human_actor_id": body.human_actor_id,
        "trusted_actor_id": actor_id,
        "actor_type": body.actor_type.strip().lower(),
        "actor_role": body.actor_role.strip().lower(),
        "rationale": body.rationale,
    }


def _status_payload_for_state(state: AixOrchestratorV2RunState, *, tenant_id: str) -> dict[str, Any]:
    state_payload = state.model_dump(mode="python")
    return {
        "success": True,
        "data": {
            "schema_version": "aix.mvp.task_status.v1",
            "task_id": state.task_id,
            "tenant_id": tenant_id,
            "run_id": state.run_id,
            "status": state.status,
            "current_phase": state.current_phase,
            "failure": state.failure.model_dump(mode="python") if state.failure else None,
            "summary": build_run_summary(state_payload),
            "review_state": "review_pending",
            "authority": {
                "source": "aix_orchestrator_v2_state_store",
                "workhub_lifecycle_authority": False,
                "evidence_is_acceptance": False,
            },
        },
    }


def _state_status_payload(*, task_id: str, tenant_id: str) -> dict[str, Any]:
    state = _require_state_for_tenant(state=state_store.get_latest_by_task_id(task_id), tenant_id=tenant_id)
    return _status_payload_for_state(state, tenant_id=tenant_id)


@router.post("/tasks/admit", dependencies=[Depends(_retired_mvp_admit_guard)])
async def admit_aix_mvp_task(
    body: Any = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    hyeve_tenant_id: str | None = Header(default=None, alias="X-Hyeve-Tenant-Id"),
    actor_id: str | None = Header(default=None, alias="X-Actor-ID"),
) -> dict[str, Any]:
    del body, tenant_id, hyeve_tenant_id, actor_id  # retained for OpenAPI shape
    _require_auth_header(authorization)
    # I-T003 / C004: MVP admit mutation path retired.  All lifecycle admission
    # must use canonical objective_cycle: POST /aix/objectives/admit.
    raise aix_http_error(
        http_status=status.HTTP_410_GONE,
        code=MVP_ADMIT_RETIREMENT_CODE,
        sub_code="USE_OBJECTIVE_CYCLE",
        detail=(
            "MVP /aix/tasks/admit mutation path retired by I-T003. "
            "Use canonical objective_cycle: POST /aix/objectives/admit."
        ),
    )


@router.post("/tasks/execute", dependencies=[Depends(_retired_mvp_mutation_guard)])
async def execute_aix_mvp_task(
    body: Any = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    hyeve_tenant_id: str | None = Header(default=None, alias="X-Hyeve-Tenant-Id"),
    actor_id: str | None = Header(default=None, alias="X-Actor-ID"),
) -> dict[str, Any]:
    del body, tenant_id, hyeve_tenant_id, actor_id  # retained for OpenAPI shape
    _require_auth_header(authorization)
    # C004-T03: MVP execute wrapped independent orchestrator_v2 authority — retired.
    raise aix_http_error(
        http_status=status.HTTP_410_GONE,
        code="MVP_EXECUTE_RETIRED",
        sub_code="USE_OBJECTIVE_CYCLE",
        detail=(
            "MVP /aix/tasks/execute mutation path retired by C004. "
            "Use canonical objective_cycle: POST /aix/objectives/admit then cycle commands."
        ),
    )


@router.get("/tasks/{id}/status")
async def get_aix_mvp_task_status(
    id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    hyeve_tenant_id: str | None = Header(default=None, alias="X-Hyeve-Tenant-Id"),
) -> dict[str, Any]:
    _require_auth_header(authorization)
    trusted_tenant_id = _require_tenant_header(tenant_id=tenant_id, hyeve_tenant_id=hyeve_tenant_id)
    return _state_status_payload(task_id=id, tenant_id=trusted_tenant_id)


@router.get("/tasks/{id}/evidence")
async def get_aix_mvp_task_evidence(
    id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    hyeve_tenant_id: str | None = Header(default=None, alias="X-Hyeve-Tenant-Id"),
) -> dict[str, Any]:
    _require_auth_header(authorization)
    trusted_tenant_id = _require_tenant_header(tenant_id=tenant_id, hyeve_tenant_id=hyeve_tenant_id)
    state = _require_state_for_tenant(state=state_store.get_latest_by_task_id(id), tenant_id=trusted_tenant_id)
    try:
        result = get_operator_run_evidence_bundle(tenant_id=trusted_tenant_id, run_id=state.run_id)
    except OperatorRunApiError as exc:
        raise aix_http_error(
            http_status=status.HTTP_502_BAD_GATEWAY,
            code="AIX_MVP_EVIDENCE_FAILED",
            sub_code=exc.reason,
            detail=exc.detail,
            context={"failure_class": exc.failure_class},
        ) from exc
    if result.status == "not_found":
        raise aix_http_error(
            http_status=status.HTTP_404_NOT_FOUND,
            code="AIX_MVP_EVIDENCE_NOT_FOUND",
            sub_code="RUN_EVIDENCE_NOT_FOUND",
            detail="Task evidence not found.",
        )
    payload = dict(result.payload)
    payload["mvp_task_id"] = id
    payload["authority"] = {
        "source": "aix_operator_run_evidence",
        "workhub_lifecycle_authority": False,
        "evidence_is_acceptance": False,
    }
    return payload


@router.post("/replay/{evidence_id}", dependencies=[Depends(_retired_mvp_mutation_guard)])
async def replay_aix_mvp_evidence(
    evidence_id: str,
    body: Any = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    hyeve_tenant_id: str | None = Header(default=None, alias="X-Hyeve-Tenant-Id"),
    actor_id: str | None = Header(default=None, alias="X-Actor-ID"),
) -> dict[str, Any]:
    del evidence_id, body, tenant_id, hyeve_tenant_id, actor_id  # retained for OpenAPI shape
    _require_auth_header(authorization)
    # I-T003 / C004: MVP replay mutation path retired.  Evidence replay via the
    # objective-cycle evidence API only.
    raise aix_http_error(
        http_status=status.HTTP_410_GONE,
        code=MVP_REPLAY_RETIREMENT_CODE,
        sub_code="USE_OBJECTIVE_CYCLE",
        detail=(
            "MVP /aix/replay/{evidence_id} mutation path retired by I-T003. "
            "Use canonical objective_cycle evidence API."
        ),
    )


@router.get("/tasks/{id}/review-packet")
async def get_aix_mvp_review_packet(
    id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    hyeve_tenant_id: str | None = Header(default=None, alias="X-Hyeve-Tenant-Id"),
) -> dict[str, Any]:
    _require_auth_header(authorization)
    trusted_tenant_id = _require_tenant_header(tenant_id=tenant_id, hyeve_tenant_id=hyeve_tenant_id)
    state = _require_state_for_tenant(state=state_store.get_latest_by_task_id(id), tenant_id=trusted_tenant_id)
    evidence_bundle_v2 = _state_evidence_bundle_v2(state)
    if not evidence_bundle_v2:
        raise aix_http_error(
            http_status=status.HTTP_404_NOT_FOUND,
            code="AIX_MVP_REVIEW_PACKET_NOT_FOUND",
            sub_code="SIGNED_EVIDENCE_BUNDLE_V2_NOT_FOUND",
            detail="Signed EvidenceBundleV2 is required before rendering a review packet.",
        )
    review_packet = build_review_packet_from_evidence_bundle_v2(
        evidence_bundle_v2=evidence_bundle_v2,
        project_id=_state_project_id(state),
        run_id=state.run_id,
        review_packet_version=_state_review_packet_version(state),
    )
    validation = validate_review_packet_handoff(review_packet)
    if not bool(validation.get("passed")):
        raise aix_http_error(
            http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="AIX_MVP_REVIEW_PACKET_REJECTED",
            sub_code=str(validation["failures"][0]["code"]),
            detail="Review packet handoff validation failed.",
            context={"failures": validation["failures"]},
        )
    return {
        "success": True,
        "data": {
            **review_packet,
            "handoff_validation": validation,
            "authority": {
                "source": "aix_mvp_product_api",
                "workhub_lifecycle_authority": False,
                "evidence_is_acceptance": False,
                "executor_review_authority": False,
            },
        },
    }


@router.post("/tasks/{id}/review-decision", dependencies=[Depends(_retired_mvp_mutation_guard)])
async def record_aix_mvp_review_decision(
    id: str,
    body: Any = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    hyeve_tenant_id: str | None = Header(default=None, alias="X-Hyeve-Tenant-Id"),
    actor_id: str | None = Header(default=None, alias="X-Actor-ID"),
) -> dict[str, Any]:
    del id, body, tenant_id, hyeve_tenant_id, actor_id  # retained for OpenAPI shape
    _require_auth_header(authorization)
    # I-T003 / C004: MVP review-decision mutation path retired.  Review decisions
    # must be recorded via the objective-cycle approval API.
    raise aix_http_error(
        http_status=status.HTTP_410_GONE,
        code=MVP_REVIEW_DECISION_RETIREMENT_CODE,
        sub_code="USE_OBJECTIVE_CYCLE",
        detail=(
            "MVP /aix/tasks/{id}/review-decision mutation path retired by I-T003. "
            "Use canonical objective_cycle approval API."
        ),
    )
