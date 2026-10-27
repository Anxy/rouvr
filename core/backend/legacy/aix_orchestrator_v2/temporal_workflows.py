from __future__ import annotations

import inspect
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from backend.aix_orchestrator_v2.models import (
    AixOrchestratorV2RunState,
    ExecutionResult,
    PlannerResult,
    ReconResult,
    VerificationResult,
    WritebackResult,
    utc_now,
)

try:  # pragma: no cover
    from temporalio import activity, workflow
except ImportError:  # pragma: no cover
    activity = None  # type: ignore[assignment]
    workflow = None  # type: ignore[assignment]


def _activity_logger() -> Any:
    import structlog

    return structlog.get_logger("hyeve_aix_service.aix_orchestrator_v2.temporal")


def _classify_failure_semantics(*, phase: str, reason_code: str, message: str, execution_attempted: bool) -> str:
    from backend.legacy.aix_orchestrator_v2.discipline import classify_failure_semantics

    return classify_failure_semantics(
        phase=phase,
        reason_code=reason_code,
        message=message,
        execution_attempted=execution_attempted,
    )


def _semantic_state_for_classification(classification: str) -> str:
    from backend.legacy.aix_orchestrator_v2.discipline import semantic_state_for_classification

    return semantic_state_for_classification(classification)


def _emit_result_event(
    logger: Any,
    *,
    event_name: str,
    phase: str,
    state: AixOrchestratorV2RunState,
    classification: str,
    reason_code: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    logger.info(
        event_name,
        event_name=event_name,
        phase=phase,
        task_id=state.task_id,
        run_id=state.run_id,
        classification=classification,
        reason_code=str(reason_code or "").strip() or None,
        **dict(payload or {}),
    )


def _emit_missing_signal(
    logger: Any,
    *,
    phase: str,
    run_id: str,
    task_id: str,
    reason_code: str,
    message: str,
) -> None:
    logger.info(
        "terminal_failure",
        event_name="terminal_failure",
        phase=phase,
        task_id=task_id,
        run_id=run_id,
        classification="INFRA_FAIL",
        reason_code=reason_code,
        message=message,
    )


def _add_execution_artifacts(state: AixOrchestratorV2RunState) -> None:
    execution = _model_dict(state.execution)
    state.add_artifact(
        name="execution_summary",
        payload={
            "status": execution.get("status"),
            "changed_files": list(execution.get("changed_files") or []),
            "commands": list(execution.get("commands") or []),
            "diff_refs": list(execution.get("diff_refs") or []),
        },
    )
    if list(execution.get("changed_files") or []):
        state.add_artifact(name="changed_files", payload={"changed_files": list(execution.get("changed_files") or [])})
    if list(execution.get("diff_refs") or []):
        state.add_artifact(name="diff_refs", payload={"diff_refs": list(execution.get("diff_refs") or [])})


def _add_run_summary_artifact(state: AixOrchestratorV2RunState) -> None:
    from backend.aix_orchestrator_v2.run_summary import build_packet_audit, build_run_summary

    state.add_artifact(name="packet_audit", payload=build_packet_audit(state.model_dump(mode="python")))
    state.add_artifact(name="run_summary", payload=build_run_summary(state.model_dump(mode="python")))


def _service() -> Any:
    from backend.aix_orchestrator_v2.service import AixOrchestratorV2Service

    return AixOrchestratorV2Service()


async def _apply_remediation_control_if_available(service: Any, state: AixOrchestratorV2RunState) -> None:
    callback = getattr(service, "_apply_remediation_control_async", None)
    if callable(callback):
        result = callback(state)
        if inspect.isawaitable(result):
            await result
        return
    callback = getattr(service, "_apply_remediation_control", None)
    if callable(callback):
        result = callback(state)
        if inspect.isawaitable(result):
            await result


def _model_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    return value if isinstance(value, dict) else {}


def _failure_code(value: Any) -> str:
    failure = _model_dict(value).get("failure")
    if not isinstance(failure, dict):
        return ""
    return str(failure.get("code") or "")


def _activity_timeout(seconds: int) -> timedelta:
    return timedelta(seconds=seconds)


def _set_phase_timestamp(state: AixOrchestratorV2RunState, *, phase: str) -> None:
    now = utc_now()
    if phase == "recon":
        state.timestamps.recon_at = now
        state.status = "recon_complete"
    elif phase == "planner":
        state.timestamps.planned_at = now
        if state.status not in {"blocked", "failed", "needs_human_input"}:
            state.status = "planned"
    elif phase == "execution":
        state.timestamps.executed_at = now
        if state.status not in {"blocked", "failed", "needs_human_input"}:
            state.status = "executed"
    elif phase == "verification":
        state.timestamps.verified_at = now
        verification_classification = str((state.verification.classification if state.verification else "") or "")
        state.status = {
            "success": "success",
            "blocked": "blocked",
            "failed": "failed",
            "needs_human_input": "needs_human_input",
        }.get(verification_classification, state.status)
    elif phase == "writeback":
        state.timestamps.writeback_at = now
        writeback_error = None
        if state.writeback:
            for candidate in (
                state.writeback.verification_error,
                state.writeback.transition_error,
                state.writeback.evidence_error,
                state.writeback.closeout_error,
            ):
                message = str(candidate or "").strip()
                if message:
                    writeback_error = message
                    break
        if writeback_error:
            classification = _classify_failure_semantics(
                phase="writeback",
                reason_code="WRITEBACK_FAILED",
                message=writeback_error,
                execution_attempted=True,
            )
            state.fail(
                classification=classification,
                phase="writeback",
                code="WRITEBACK_FAILED",
                message=writeback_error,
                status="failed",
                details={
                    "writeback": _model_dict(state.writeback),
                    "semantic_state": _semantic_state_for_classification(classification),
                },
            )
            return
        if state.status == "success":
            state.status = "writeback_complete"
            state.timestamps.completed_at = now
    state.touch()


def _blocked_execution_payload(state: AixOrchestratorV2RunState) -> ExecutionResult:
    blocked_reason = str(
        (state.plan.model_dump(mode="python") if state.plan else {}).get("blocked_reason") or "planner blocked"
    )
    classification = _classify_failure_semantics(
        phase="planner",
        reason_code="PLANNER_BLOCKED",
        message=blocked_reason,
        execution_attempted=False,
    )
    task_ref = _model_dict(state.packet.get("task_ref"))
    return ExecutionResult(
        status="skipped",
        run_result={
            "ingress": "aix_orchestrator_v2.execution",
            "schema_id": None,
            "executable": False,
            "status": "failed",
            "run_id": None,
            "task_id": state.task_id,
            "task_number": task_ref.get("task_number"),
            "project_id": task_ref.get("project_id"),
            "correlation_id": state.run_id,
            "packet_id": None,
            "replayed": False,
            "evidence_refs": [],
            "evidence": {},
            "telemetry": {},
            "error": {
                "code": "PLANNER_BLOCKED",
                "message": blocked_reason,
                "retryable": False,
                "details": {
                    "classification": classification,
                    "semantic_state": _semantic_state_for_classification(classification),
                },
            },
        },
    )


class AixOrchestratorV2Activities:
    def __init__(self) -> None:
        self._service = _service()

    async def load_task_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        _activity_logger().info(
            "activity_start",
            event_name="activity_start",
            phase="load_task",
            activity="aix_orchestrator_v2.load_task_activity",
            task_id=str(payload.get("task_id") or ""),
            run_id=str(payload.get("run_id") or ""),
            task_number=payload.get("task_number"),
        )
        state = await self._service.load_task(
            project_id=str(payload["project_id"]),
            task_number=int(payload["task_number"]),
            workspace_root=str(payload["workspace_root"]),
            run_id=str(payload.get("run_id") or "").strip() or None,
            tenant_id=str(payload.get("tenant_id") or "").strip() or None,
        )
        state.packet["runtime"] = {
            "repo_key": str(payload.get("repo_key") or "hyeve-aix-service"),
            "explicit_profile_id": str(payload.get("explicit_profile_id") or "").strip() or None,
            "tenant_id": str(payload.get("tenant_id") or "default"),
            "actor_id": str(payload.get("actor_id") or "aix-orchestrator-v2"),
            "writeback": bool(payload.get("writeback", True)),
        }
        self._service._persist(state)  # noqa: SLF001
        _activity_logger().info(
            "activity_end",
            event_name="activity_end",
            phase="load_task",
            activity="aix_orchestrator_v2.load_task_activity",
            task_id=state.task_id,
            run_id=state.run_id,
            classification="success",
        )
        return state.model_dump(mode="python")

    async def recon_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = AixOrchestratorV2RunState.model_validate(payload)
        _activity_logger().info(
            "activity_start",
            event_name="activity_start",
            phase="recon",
            activity="aix_orchestrator_v2.recon_activity",
            task_id=state.task_id,
            run_id=state.run_id,
        )
        contracts = await self._service._workhub.list_task_execution_contracts(  # noqa: SLF001
            project_id=str(state.packet["task_ref"]["project_id"]),
            task_number=int(state.packet["task_ref"]["task_number"]),
        )
        from backend.legacy.aix_orchestrator_v2.recon_service import build_recon

        state.recon = ReconResult.model_validate(
            await build_recon(
                tenant_id="default",
                project_id=str(state.packet["task_ref"]["project_id"]),
                task_payload=state.task_payload,
                execution_contracts=contracts,
                workspace_root=str(state.packet["workspace_ref"]["workspace_root"]),
            )
        )
        state.packet["slim_packet"] = dict(state.recon.slim_packet_inputs)
        state.current_phase = "recon"
        _set_phase_timestamp(state, phase="recon")
        state.add_artifact(name="recon", payload=_model_dict(state.recon))
        if str(state.recon.status or "").strip() == "insufficient":
            classification = _classify_failure_semantics(
                phase="recon",
                reason_code="RECON_TARGET_FILE_REQUIRED",
                message=str(state.recon.reason or "target_file_missing"),
                execution_attempted=False,
            )
            state.fail(
                classification=classification,
                phase="recon",
                code="RECON_TARGET_FILE_REQUIRED",
                message=str(state.recon.reason or "target_file_missing"),
                status="blocked",
                details={
                    "recon": _model_dict(state.recon),
                    "semantic_state": _semantic_state_for_classification(classification),
                },
            )
            await _apply_remediation_control_if_available(self._service, state)
        self._service._persist(state)  # noqa: SLF001
        _activity_logger().info(
            "activity_end",
            event_name="activity_end",
            phase="recon",
            activity="aix_orchestrator_v2.recon_activity",
            task_id=state.task_id,
            run_id=state.run_id,
            classification=str((state.failure.classification if state.failure else "success") or "success"),
            reason_code=str((state.failure.code if state.failure else "") or ""),
        )
        return state.model_dump(mode="python")

    async def planner_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = AixOrchestratorV2RunState.model_validate(payload)
        _activity_logger().info(
            "activity_start",
            event_name="activity_start",
            phase="planner",
            activity="aix_orchestrator_v2.planner_activity",
            task_id=state.task_id,
            run_id=state.run_id,
        )
        from backend.legacy.aix_orchestrator_v2.planner_service import plan_task

        state.plan = PlannerResult.model_validate(
            plan_task(
                tenant_id="default",
                project_id=str(state.packet["task_ref"]["project_id"]),
                correlation_id=state.run_id,
                workspace_root=str(state.packet["workspace_ref"]["workspace_root"]),
                task_payload=state.task_payload,
                recon=_model_dict(state.recon),
            )
        )
        state.current_phase = "planner"
        _set_phase_timestamp(state, phase="planner")
        state.add_artifact(name="planner_input_summary", payload=_model_dict(state.plan.input_summary))
        state.add_artifact(
            name="planner_raw",
            payload={"raw_output_text": str(state.plan.raw_output_text or "").strip() or None},
        )
        state.add_artifact(name="normalized_plan", payload={"steps": list(state.plan.normalized_plan or [])})
        state.add_artifact(name="planner_trace", payload=_model_dict(state.plan.planner_trace))
        if str(state.plan.status or "").strip() == "blocked":
            blocked_reason = str(state.plan.blocked_reason or "planner blocked")
            failure_code = str(
                state.plan.validation_failures[0] if state.plan.validation_failures else "PLANNER_BLOCKED"
            )
            classification = _classify_failure_semantics(
                phase="planner",
                reason_code=failure_code,
                message=blocked_reason,
                execution_attempted=False,
            )
            state.fail(
                classification=classification,
                phase="planner",
                code=failure_code,
                message=blocked_reason,
                status="blocked",
                details={
                    "planner_trace": _model_dict(state.plan.planner_trace),
                    "semantic_state": _semantic_state_for_classification(classification),
                },
            )
            await _apply_remediation_control_if_available(self._service, state)
        self._service._persist(state)  # noqa: SLF001
        _activity_logger().info(
            "activity_end",
            event_name="activity_end",
            phase="planner",
            activity="aix_orchestrator_v2.planner_activity",
            task_id=state.task_id,
            run_id=state.run_id,
            classification=str((state.failure.classification if state.failure else "success") or "success"),
            reason_code=str((state.failure.code if state.failure else "") or ""),
        )
        return state.model_dump(mode="python")

    async def execution_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = AixOrchestratorV2RunState.model_validate(payload)
        logger = _activity_logger()
        logger.info(
            "activity_start",
            event_name="activity_start",
            phase="execution",
            activity="aix_orchestrator_v2.execution_activity",
            task_id=state.task_id,
            run_id=state.run_id,
        )
        from backend.legacy.aix_orchestrator_v2.execution_service import execute_plan

        state.execution = ExecutionResult.model_validate(
            execute_plan(
                project_id=str(state.packet["task_ref"]["project_id"]),
                run_id=state.run_id,
                task_payload=state.task_payload,
                recon=_model_dict(state.recon),
                plan=_model_dict(state.plan),
                workspace_root=str(state.packet["workspace_ref"]["workspace_root"]),
                repo_key=str((state.packet.get("runtime") or {}).get("repo_key") or "hyeve-aix-service"),
                explicit_profile_id=str((state.packet.get("runtime") or {}).get("explicit_profile_id") or "").strip()
                or None,
                state_packet=_model_dict(state.packet),
            )
        )
        state.current_phase = "execution"
        _set_phase_timestamp(state, phase="execution")
        _add_execution_artifacts(state)
        if _model_dict(state.execution.failure):
            await _apply_remediation_control_if_available(self._service, state)
        self._service._persist(state)  # noqa: SLF001
        execution_failure = _model_dict(state.execution.failure)
        if execution_failure:
            _emit_result_event(
                logger,
                event_name="terminal_failure",
                phase="execution",
                state=state,
                classification="CODE_FAIL",
                reason_code=str(execution_failure.get("code") or ""),
                payload={
                    "message": str(execution_failure.get("message") or "").strip() or None,
                },
            )
        logger.info(
            "activity_end",
            event_name="activity_end",
            phase="execution",
            activity="aix_orchestrator_v2.execution_activity",
            task_id=state.task_id,
            run_id=state.run_id,
            classification="success" if str(state.execution.status or "") == "completed" else "CODE_FAIL",
            reason_code=_failure_code(state.execution),
        )
        return state.model_dump(mode="python")

    async def verifier_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = AixOrchestratorV2RunState.model_validate(payload)
        logger = _activity_logger()
        logger.info(
            "activity_start",
            event_name="activity_start",
            phase="verification",
            activity="aix_orchestrator_v2.verifier_activity",
            task_id=state.task_id,
            run_id=state.run_id,
        )
        from backend.legacy.aix_orchestrator_v2.verification_service import verify_run

        execution = _model_dict(state.execution)
        state.verification = VerificationResult.model_validate(
            verify_run(
                plan=_model_dict(state.plan),
                execution=execution,
                failure=execution.get("failure") or _model_dict(state.failure),
            )
        )
        state.current_phase = "verification"
        _set_phase_timestamp(state, phase="verification")
        state.add_artifact(name="verifier_result", payload=_model_dict(state.verification))
        if str(state.verification.classification or "") != "success":
            await _apply_remediation_control_if_available(self._service, state)
        self._service._persist(state)  # noqa: SLF001
        _emit_result_event(
            logger,
            event_name="validation_result",
            phase="verification",
            state=state,
            classification=str(state.verification.classification or ""),
            reason_code=str(state.verification.reason_code or ""),
            payload={
                "summary": str(state.verification.summary or "").strip() or None,
                "check_count": len(list(state.verification.checks or [])),
            },
        )
        logger.info(
            "activity_end",
            event_name="activity_end",
            phase="verification",
            activity="aix_orchestrator_v2.verifier_activity",
            task_id=state.task_id,
            run_id=state.run_id,
            classification=str(state.verification.classification or ""),
            reason_code=str(state.verification.reason_code or ""),
        )
        return state.model_dump(mode="python")

    async def writeback_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = AixOrchestratorV2RunState.model_validate(payload)
        logger = _activity_logger()
        logger.info(
            "activity_start",
            event_name="activity_start",
            phase="writeback",
            activity="aix_orchestrator_v2.writeback_activity",
            task_id=state.task_id,
            run_id=state.run_id,
        )
        from backend.legacy.aix_orchestrator_v2.writeback_service import writeback_v2_run

        if (
            state.execution is None
            and str((state.plan.model_dump(mode="python") if state.plan else {}).get("status") or "") == "blocked"
        ):
            state.execution = _blocked_execution_payload(state)
        writeback = await writeback_v2_run(
            client=self._service._workhub,  # noqa: SLF001
            tenant_id=str((state.packet.get("runtime") or {}).get("tenant_id") or "default"),
            actor_id=str((state.packet.get("runtime") or {}).get("actor_id") or "aix-orchestrator-v2"),
            project_id=str(state.packet["task_ref"]["project_id"]),
            task_payload=state.task_payload,
            run_id=state.run_id,
            execution=_model_dict(state.execution),
            verification=_model_dict(state.verification),
        )
        state.writeback = WritebackResult.model_validate(writeback)
        state.add_artifact(name="writeback", payload=writeback)
        _add_run_summary_artifact(state)
        state.current_phase = "writeback"
        _set_phase_timestamp(state, phase="writeback")
        if state.failure is not None:
            await _apply_remediation_control_if_available(self._service, state)
        self._service._persist(state)  # noqa: SLF001
        _emit_result_event(
            logger,
            event_name="writeback_result",
            phase="writeback",
            state=state,
            classification=str((state.failure.classification if state.failure else state.status) or ""),
            reason_code=str((state.failure.code if state.failure else "") or ""),
            payload={
                "writeback_status": str((_model_dict(state.writeback).get("writeback") or {}).get("status") or "")
                or None,
                "transition_ok": bool(state.writeback.transition_ok) if state.writeback else False,
                "evidence_ok": bool(state.writeback.evidence_ok) if state.writeback else False,
            },
        )
        if state.failure is not None:
            _emit_result_event(
                logger,
                event_name="terminal_failure",
                phase="writeback",
                state=state,
                classification=str(state.failure.classification or ""),
                reason_code=str(state.failure.code or ""),
                payload={"message": str(state.failure.message or "").strip() or None},
            )
        logger.info(
            "activity_end",
            event_name="activity_end",
            phase="writeback",
            activity="aix_orchestrator_v2.writeback_activity",
            task_id=state.task_id,
            run_id=state.run_id,
            classification=str((state.failure.classification if state.failure else state.status) or ""),
            reason_code=str((state.failure.code if state.failure else "") or ""),
        )
        return state.model_dump(mode="python")


async def load_task_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await AixOrchestratorV2Activities().load_task_activity(payload)


async def recon_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await AixOrchestratorV2Activities().recon_activity(payload)


async def planner_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await AixOrchestratorV2Activities().planner_activity(payload)


async def execution_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await AixOrchestratorV2Activities().execution_activity(payload)


async def verifier_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await AixOrchestratorV2Activities().verifier_activity(payload)


async def writeback_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await AixOrchestratorV2Activities().writeback_activity(payload)


if TYPE_CHECKING or workflow is None:

    class AixOrchestratorV2Workflow:  # pragma: no cover
        async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
            return payload

else:  # pragma: no cover
    load_task_activity = activity.defn(name="aix_orchestrator_v2.load_task_activity")(load_task_activity)
    recon_activity = activity.defn(name="aix_orchestrator_v2.recon_activity")(recon_activity)
    planner_activity = activity.defn(name="aix_orchestrator_v2.planner_activity")(planner_activity)
    execution_activity = activity.defn(name="aix_orchestrator_v2.execution_activity")(execution_activity)
    verifier_activity = activity.defn(name="aix_orchestrator_v2.verifier_activity")(verifier_activity)
    writeback_activity = activity.defn(name="aix_orchestrator_v2.writeback_activity")(writeback_activity)

    @workflow.defn(name="AixOrchestratorV2Workflow")
    class AixOrchestratorV2Workflow:
        @workflow.run
        async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
            # Keep the workflow body free of application logging. Our deployed
            # logging stack is not guaranteed to be Temporal-sandbox-safe, and
            # activation must stay deterministic and side-effect free.
            state = await workflow.execute_activity(
                load_task_activity,
                payload,
                start_to_close_timeout=_activity_timeout(300),
            )
            state = await workflow.execute_activity(
                recon_activity,
                state,
                start_to_close_timeout=_activity_timeout(300),
            )
            if str((state or {}).get("recon", {}).get("status") or "") != "insufficient":
                state = await workflow.execute_activity(
                    planner_activity,
                    state,
                    start_to_close_timeout=_activity_timeout(300),
                )
            if (
                str((state or {}).get("recon", {}).get("status") or "") == "insufficient"
                and not str((state or {}).get("failure", {}).get("code") or "").strip()
            ):
                state.setdefault("failure", {})
                state["failure"]["code"] = "PLANNER_SKIPPED_WITHOUT_REASON"
                state["failure"]["message"] = "planner phase skipped without explicit failure reason"
                workflow.logger.info(
                    "planner_skipped",
                    event_name="planner_skipped",
                    reason_code="RECON_INSUFFICIENT",
                )
                workflow.logger.info(
                    "terminal_failure",
                    event_name="terminal_failure",
                    reason_code="PLANNER_SKIPPED_WITHOUT_REASON",
                )
            if (
                str((state or {}).get("recon", {}).get("status") or "") != "insufficient"
                and str((state or {}).get("plan", {}).get("status") or "") != "blocked"
            ):
                state = await workflow.execute_activity(
                    execution_activity,
                    state,
                    start_to_close_timeout=_activity_timeout(1800),
                )
            else:
                execution_skip_reason = str((state or {}).get("failure", {}).get("code") or "").strip()
                if not execution_skip_reason and str((state or {}).get("plan", {}).get("status") or "") == "blocked":
                    execution_skip_reason = "PLANNER_BLOCKED"
                if (
                    not execution_skip_reason
                    and str((state or {}).get("recon", {}).get("status") or "") == "insufficient"
                ):
                    execution_skip_reason = "RECON_INSUFFICIENT"
                if not execution_skip_reason:
                    state.setdefault("failure", {})
                    state["failure"]["code"] = "EXECUTION_SKIPPED_WITHOUT_REASON"
                    state["failure"]["message"] = "execution phase skipped without explicit reason"
            if (state or {}).get("execution") is None:
                state.setdefault("failure", {})
                state["failure"]["code"] = "VERIFIER_CALLED_WITHOUT_EXECUTION_STATE"
                state["failure"]["message"] = "verifier phase started without execution state"
                workflow.logger.info(
                    "terminal_failure",
                    event_name="terminal_failure",
                    reason_code="VERIFIER_CALLED_WITHOUT_EXECUTION_STATE",
                )
            state = await workflow.execute_activity(
                verifier_activity,
                state,
                start_to_close_timeout=_activity_timeout(300),
            )
            if bool(((state or {}).get("packet") or {}).get("runtime", {}).get("writeback", True)):
                state = await workflow.execute_activity(
                    writeback_activity,
                    state,
                    start_to_close_timeout=_activity_timeout(300),
                )
            return state
