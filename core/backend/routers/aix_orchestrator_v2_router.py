from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request, status

from backend.aix_orchestrator_v2.models import (
    AixOrchestratorV2RunState,
    AixOrchestratorV2TriggerRequest,
    AixOrchestratorV2TriggerResponse,
)
from backend.aix_orchestrator_v2.run_summary import build_run_summary
from backend.aix_orchestrator_v2.service import AixOrchestratorV2Service
from backend.aix_orchestrator_v2.state_store import state_store
from backend.errors.aix_errors import aix_http_error
from backend.shared.request_header_utils import resolve_tenant_header

router = APIRouter(prefix="/api/aix/orchestrator-v2", tags=["aix orchestrator v2"])


def _require_auth_header(authorization: str | None) -> None:
    if not authorization or not authorization.strip():
        raise aix_http_error(
            http_status=status.HTTP_401_UNAUTHORIZED,
            code="AUTH_FAILED",
            sub_code="AUTH_MISSING",
            detail="Authorization header required",
        )


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _artifact_ref(state: AixOrchestratorV2RunState, name: str) -> str | None:
    for artifact in reversed(state.artifacts):
        if artifact.name == name:
            return str(artifact.ref or "").strip() or None
    return None


def _command_view(command: Any) -> dict[str, Any]:
    if not isinstance(command, dict):
        return {}
    return {
        "command_text": command.get("command_text") or command.get("command"),
        "exit_code": command.get("exit_code"),
        "status": command.get("status"),
        "allowlist_compliance": command.get("allowlist_compliance"),
        "classification": _as_dict(command.get("classification_mapping")).get("result")
        or command.get("classification"),
    }


def _phase_results_payload(state: AixOrchestratorV2RunState) -> dict[str, Any]:
    execution = state.execution.model_dump(mode="python") if state.execution else None
    verification = state.verification.model_dump(mode="python") if state.verification else None
    writeback = state.writeback.model_dump(mode="python") if state.writeback else None
    normalized_plan = list(
        _as_dict(state.plan.model_dump(mode="python") if state.plan else {}).get("normalized_plan") or []
    )
    planner_input = dict(
        _as_dict(state.plan.model_dump(mode="python") if state.plan else {}).get("input_summary") or {}
    )
    planner_raw = _as_dict(state.plan.model_dump(mode="python") if state.plan else {}).get("raw_output_text")
    return {
        "recon": (
            {
                "status": state.recon.status,
                "reason": state.recon.reason,
                "target_file": state.recon.target_file,
                "target_symbol": state.recon.target_symbol,
                "validation_target": state.recon.validation_target,
            }
            if state.recon
            else None
        ),
        "planner": (
            {
                "status": state.plan.status,
                "blocked_reason": state.plan.blocked_reason,
                "declared_validation": state.plan.declared_validation,
                "validation_failures": list(state.plan.validation_failures or []),
                "input_summary": {
                    "target_file": planner_input.get("target_file"),
                    "target_symbol": planner_input.get("target_symbol"),
                    "validation_command": planner_input.get("validation_command"),
                    "active_packet_validator_sha": planner_input.get("active_packet_validator_sha"),
                    "planner_precheck_sha": planner_input.get("planner_precheck_sha"),
                    "packet_warning_codes": list(planner_input.get("packet_warning_codes") or []),
                },
                "raw_output": {
                    "present": bool(planner_raw),
                    "size_bytes": len(str(planner_raw).encode("utf-8")) if planner_raw else 0,
                },
                "normalized_plan": {
                    "step_count": len(normalized_plan),
                    "step_tools": [
                        step.get("tool") or step.get("type")
                        for step in normalized_plan
                        if isinstance(step, dict) and (step.get("tool") or step.get("type"))
                    ],
                },
                "trace_artifact_ref": state.plan.trace_artifact_ref or _artifact_ref(state, "planner_trace_ref"),
            }
            if state.plan
            else None
        ),
        "execution": (
            {
                "status": execution.get("status"),
                "failure": execution.get("failure"),
                "changed_files": list(execution.get("changed_files") or []),
                "diff_refs": list(execution.get("diff_refs") or []),
                "commands": [_command_view(command) for command in list(execution.get("commands") or [])],
                "command_count": len(list(execution.get("commands") or [])),
            }
            if execution
            else None
        ),
        "verification": (
            {
                "classification": verification.get("classification"),
                "reason_code": verification.get("reason_code"),
                "summary": verification.get("summary"),
                "check_count": len(list(verification.get("checks") or [])),
            }
            if verification
            else None
        ),
        "writeback": (
            {
                "writeback": _as_dict(writeback.get("writeback")),
                "transition_ok": bool(writeback.get("transition_ok")),
                "transition_error": writeback.get("transition_error"),
                "evidence_ok": bool(writeback.get("evidence_ok")),
                "evidence_error": writeback.get("evidence_error"),
                "closeout_error": writeback.get("closeout_error"),
            }
            if writeback
            else None
        ),
    }


def _run_response_payload(state: AixOrchestratorV2RunState) -> dict[str, Any]:
    state_payload = state.model_dump(mode="python")
    summary = build_run_summary(state_payload)
    payload = {
        "task_id": state.task_id,
        "run_id": state.run_id,
        "status": state.status,
        "current_phase": state.current_phase,
        "failure": state.failure.model_dump(mode="python") if state.failure else None,
        "timestamps": state.timestamps.model_dump(mode="python"),
        "summary": summary,
        "phase_results": _phase_results_payload(state),
        "retrieval": {
            "task_id": state.task_id,
            "task_number": int(_as_dict(state_payload.get("task_payload")).get("task_number") or 0) or None,
            "run_id": state.run_id,
            "workflow_id": str(
                _as_dict(_as_dict(state_payload.get("packet")).get("temporal")).get("workflow_id") or ""
            ).strip()
            or None,
            "artifact_ref_count": len(list(summary.get("artifact_refs") or [])),
        },
    }
    return payload


# C004-T03: independent mutation authority retired. Reads remain for migration evidence.
ORCHESTRATOR_V2_MUTATION_RETIRED = True
ORCHESTRATOR_V2_RETIREMENT_CODE = "ORCHESTRATOR_V2_MUTATION_RETIRED"


@router.post("/runs", response_model=AixOrchestratorV2TriggerResponse)
async def trigger_aix_orchestrator_v2_run(
    request: Request,
    body: Any = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    _require_auth_header(authorization)
    if ORCHESTRATOR_V2_MUTATION_RETIRED:
        raise aix_http_error(
            http_status=status.HTTP_410_GONE,
            code=ORCHESTRATOR_V2_RETIREMENT_CODE,
            sub_code="USE_OBJECTIVE_CYCLE",
            detail=(
                "aix_orchestrator_v2 mutation authority retired by C004. "
                "Use canonical objective_cycle (/aix/objectives, /aix/cycles). "
                "GET run retrieval remains available as a read adapter."
            ),
        )
    assert isinstance(body, AixOrchestratorV2TriggerRequest)  # unreachable while retired
    tenant_id = resolve_tenant_header(request.headers, default=body.tenant_id) or body.tenant_id
    service = AixOrchestratorV2Service()
    if body.mode == "temporal":
        temporal_runtime = getattr(request.app.state, "temporal_runtime", None)
        if temporal_runtime is None or temporal_runtime._sdk_loader() is None:  # noqa: SLF001
            raise aix_http_error(
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="TEMPORAL_UNAVAILABLE",
                sub_code="TEMPORAL_SDK_NOT_INSTALLED",
                detail="Temporal execution requested but SDK/runtime is unavailable.",
            )
        state = await service.load_task(
            project_id=body.project_id,
            task_number=body.task_number,
            workspace_root=body.workspace_root,
            tenant_id=tenant_id,
        )
        if state.failure:
            return {"success": True, "data": _run_response_payload(state)}
        bootstrap = {
            "workflow_id": f"aix-v2-{body.project_id}-{body.task_number}",
            "project_id": body.project_id,
            "task_number": body.task_number,
            "workspace_root": body.workspace_root,
            "run_id": state.run_id,
            "repo_key": body.repo_key,
            "explicit_profile_id": body.explicit_profile_id,
            "tenant_id": tenant_id,
            "actor_id": body.actor_id,
            "writeback": body.writeback,
        }
        state.packet["runtime"] = {
            "repo_key": body.repo_key,
            "explicit_profile_id": body.explicit_profile_id,
            "tenant_id": tenant_id,
            "actor_id": body.actor_id,
            "writeback": body.writeback,
        }
        try:
            # Final single-path architecture: the legacy v2 Temporal start was
            # removed from TemporalRuntimeManager. This branch is unreachable
            # while ORCHESTRATOR_V2_MUTATION_RETIRED is True; if it is ever
            # reached, fail closed toward the canonical objective-cycle path.
            raise RuntimeError(
                "TEMPORAL_V2_PATH_RETIRED: use canonical_start "
                "(AixObjectiveCycleWorkflow on aix.execution.current)"
            )
        except Exception as exc:
            state.current_phase = "temporal_start"
            state.fail(
                classification="blocked_needs_human",
                phase="temporal_start",
                code="TEMPORAL_START_FAILED",
                message=f"Temporal workflow start failed: {exc}",
                retryable=True,
                status="blocked",
                details={
                    "error_type": type(exc).__name__,
                    "workflow_id": bootstrap["workflow_id"],
                    "task_queue": getattr(temporal_runtime.binding, "execution_queue", None),
                },
            )
            state_store.save(state)
            raise aix_http_error(
                http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="TEMPORAL_START_FAILED",
                sub_code="TEMPORAL_WORKFLOW_START_ERROR",
                detail=state.failure.message if state.failure else "Temporal workflow start failed.",
            ) from exc
        state.current_phase = "temporal_started"
        state.packet["temporal"] = temporal
        state_store.save(state)
        return {
            "success": True,
            "data": {
                **_run_response_payload(state),
                "temporal": temporal,
            },
        }
    state = await service.run_inline(
        tenant_id=tenant_id,
        project_id=body.project_id,
        task_number=body.task_number,
        workspace_root=body.workspace_root,
        repo_key=body.repo_key,
        explicit_profile_id=body.explicit_profile_id,
        actor_id=body.actor_id,
        writeback=body.writeback,
    )
    return {"success": True, "data": _run_response_payload(state)}


@router.get("/runs/{run_id}")
async def get_aix_orchestrator_v2_run(
    run_id: str, authorization: str | None = Header(default=None, alias="Authorization")
) -> dict[str, Any]:
    _require_auth_header(authorization)
    state = state_store.get_by_run_id(run_id)
    if state is None:
        raise aix_http_error(
            http_status=status.HTTP_404_NOT_FOUND,
            code="AIX_ORCHESTRATOR_V2_RUN_NOT_FOUND",
            sub_code="RUN_ID_NOT_FOUND",
            detail=f"run not found: {run_id}",
        )
    return {"success": True, "data": _run_response_payload(state)}


@router.get("/tasks/{task_id}/runs/latest")
async def get_aix_orchestrator_v2_latest_task_run(
    task_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    _require_auth_header(authorization)
    state = state_store.get_latest_by_task_id(task_id)
    if state is None:
        raise aix_http_error(
            http_status=status.HTTP_404_NOT_FOUND,
            code="AIX_ORCHESTRATOR_V2_RUN_NOT_FOUND",
            sub_code="TASK_ID_NOT_FOUND",
            detail=f"latest run not found for task: {task_id}",
        )
    return {"success": True, "data": _run_response_payload(state)}
