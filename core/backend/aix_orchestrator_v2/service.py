from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from backend.legacy.aix_orchestrator_v2.discipline import (
    classify_failure_semantics,
    compact_recon_for_active_packet,
    compact_verification_for_task_metadata,
    compact_writeback_for_task_metadata,
    semantic_state_for_classification,
    validator_sha,
)
from backend.legacy.aix_orchestrator_v2.execution_service import execute_plan
from backend.aix_orchestrator_v2.models import (
    AixOrchestratorV2RunState,
    ExecutionResult,
    PlannerResult,
    ReconResult,
    RunStatus,
    VerificationResult,
    WritebackResult,
    utc_now,
)
from backend.legacy.aix_orchestrator_v2.planner_service import plan_task
from backend.legacy.aix_orchestrator_v2.recon_service import build_recon
from backend.aix_orchestrator_v2.run_summary import build_run_summary
from backend.aix_orchestrator_v2.state_store import AixOrchestratorV2StateStore, state_store
from backend.legacy.aix_orchestrator_v2.verification_service import verify_run
from backend.aix_orchestrator_v2.workhub_client import WorkHubTaskClient, workhub_task_client
from backend.hyeve_aix_service.config import settings
from backend.services.remediation_control_layer import (
    LEDGER_SCAN_STATUSES,
    build_defect_fingerprint,
    build_family_blocker_ledger,
    build_family_blocker_ledger_from_rows,
    build_fingerprint_key,
    child_task_contract_validator,
    default_remediation_policy,
    should_create_remedial_task,
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _allowed_workspace_roots() -> list[Path]:
    raw = str(settings.DEVEXECUTOR_ALLOWED_WORKSPACE_ROOTS or "").strip()
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(values, list):
        return []
    roots: list[Path] = []
    for item in values:
        text = str(item or "").strip()
        if not text:
            continue
        try:
            roots.append(Path(text).resolve())
        except Exception:
            continue
    return roots


def _workspace_outside_allowed_roots(workspace_root: str) -> dict[str, Any] | None:
    allowed_roots = _allowed_workspace_roots()
    if not allowed_roots:
        return None
    try:
        resolved = Path(workspace_root).resolve()
    except Exception as exc:
        return {
            "code": "WORKSPACE_ROOT_INVALID",
            "message": f"workspace_root could not be resolved: {exc}",
            "workspace_root": str(workspace_root),
        }
    if any(resolved == root or root in resolved.parents for root in allowed_roots):
        return None
    return {
        "code": "WORKSPACE_OUTSIDE_ALLOWED_ROOTS",
        "message": "workspace_root is outside configured execution workspace roots",
        "workspace_root": str(resolved),
        "allowed_roots": [str(root) for root in allowed_roots],
    }


def _model_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    return _as_dict(value)


class AixOrchestratorV2Service:
    def __init__(
        self,
        *,
        workhub_client: WorkHubTaskClient | None = None,
        run_store: AixOrchestratorV2StateStore | None = None,
    ) -> None:
        self._workhub = workhub_client or workhub_task_client
        self._run_store = run_store or state_store
        self._logger = structlog.get_logger("hyeve_aix_service.aix_orchestrator_v2.service")

    def _persist(self, state: AixOrchestratorV2RunState, *, artifact_name: str | None = None) -> str:
        ref = self._run_store.save(state)
        if artifact_name:
            state.add_artifact(name=artifact_name, payload={"state_ref": ref}, ref=ref)
            ref = self._run_store.save(state)
        return ref

    def _failure_classification(self, *, phase: str, status: str, code: str | None = None) -> str:
        return classify_failure_semantics(
            phase=phase,
            reason_code=str(code or status or "").strip(),
            message=str(code or status or "").strip(),
            execution_attempted=phase in {"execution", "verification", "writeback"},
        )

    def _record_guard_event(
        self,
        state: AixOrchestratorV2RunState,
        *,
        guard_name: str,
        reason_code: str,
        phase: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        event_payload = {
            "guard_name": guard_name,
            "reason_code": reason_code,
            "task_id": state.task_id,
            "run_id": state.run_id,
            "phase": phase,
            "payload": dict(payload or {}),
        }
        event_sha = validator_sha(event_payload)
        ledger = dict(state.validator_ledger or {})
        last_validated = dict(ledger.get("last_validated_sha") or {})
        if last_validated.get(guard_name) == event_sha:
            return
        last_validated[guard_name] = event_sha
        events = list(ledger.get("events") or [])
        events.append(
            {
                "event": "guard_triggered",
                "guard_name": guard_name,
                "reason_code": reason_code,
                "task_id": state.task_id,
                "run_id": state.run_id,
                "validator_sha": event_sha,
                "phase": phase,
            }
        )
        self._logger.info(
            "guard_triggered",
            run_id=state.run_id,
            task_id=state.task_id,
            phase=phase,
            event_name="guard_triggered",
            reason_code=reason_code,
            classification="POLICY_FAIL",
            validator_sha=event_sha,
        )
        state.validator_ledger = {"last_validated_sha": last_validated, "events": events}
        state.add_artifact(
            name="guard_event",
            payload={
                "guard_name": guard_name,
                "reason_code": reason_code,
                "task_id": state.task_id,
                "run_id": state.run_id,
                "validator_sha": event_sha,
                "phase": phase,
            },
        )

    def _writeback_error_message(self, writeback: WritebackResult) -> str | None:
        for candidate in (
            writeback.verification_error,
            writeback.transition_error,
            writeback.evidence_error,
        ):
            message = str(candidate or "").strip()
            if message:
                return message
        return None

    def _task_number(self, state: AixOrchestratorV2RunState) -> int:
        task_ref = _as_dict(_as_dict(state.packet).get("task_ref"))
        return max(0, int(task_ref.get("task_number") or state.task_payload.get("task_number") or 0))

    def _project_id(self, state: AixOrchestratorV2RunState) -> str:
        task_ref = _as_dict(_as_dict(state.packet).get("task_ref"))
        return str(task_ref.get("project_id") or "").strip()

    def _family_root_task_number(self, state: AixOrchestratorV2RunState) -> int:
        metadata = _as_dict(_as_dict(state.task_payload).get("metadata_json"))
        return max(
            0,
            int(
                metadata.get("recovery_root_task_number")
                or metadata.get("decomposition_parent_task_number")
                or metadata.get("planning_parent_task_number")
                or metadata.get("source_task_number")
                or metadata.get("parent_task_number")
                or self._task_number(state)
                or 0
            ),
        )

    def _candidate_target_files(self, state: AixOrchestratorV2RunState) -> list[str]:
        candidates = [
            *list(_model_dict(state.recon).get("target_files") or []),
            str(_model_dict(state.recon).get("target_file") or "").strip(),
            *list(_model_dict(state.plan).get("file_targets") or []),
            *list(_model_dict(state.execution).get("changed_files") or []),
        ]
        seen: set[str] = set()
        target_files: list[str] = []
        for item in candidates:
            text = str(item or "").strip().replace("\\", "/")
            if not text or text in seen:
                continue
            seen.add(text)
            target_files.append(text)
        return target_files

    def _candidate_validation_commands(self, state: AixOrchestratorV2RunState) -> list[str]:
        task_contract = _as_dict(_as_dict(state.task_payload).get("active_execution_contract"))
        execution_slice = _as_dict(task_contract.get("execution_slice"))
        commands = [
            str(_model_dict(state.plan).get("declared_validation") or "").strip(),
            str(_model_dict(state.recon).get("validation_target") or "").strip(),
            *[str(item).strip() for item in execution_slice.get("validation_commands") or [] if str(item).strip()],
        ]
        seen: set[str] = set()
        result: list[str] = []
        for item in commands:
            if not item or item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    def _remediation_failure_view(self, state: AixOrchestratorV2RunState) -> dict[str, str] | None:
        if state.failure is not None:
            return {
                "classification": str(state.failure.classification or "").strip(),
                "phase": str(state.failure.phase or "").strip(),
                "reason_code": str(state.failure.code or "").strip(),
                "message": str(state.failure.message or "").strip(),
            }
        execution_failure = _as_dict(_model_dict(state.execution).get("failure"))
        if execution_failure:
            reason_code = str(execution_failure.get("code") or "EXECUTION_FAILED").strip()
            return {
                "classification": self._failure_classification(phase="execution", status="failed", code=reason_code),
                "phase": "execution",
                "reason_code": reason_code,
                "message": str(execution_failure.get("message") or reason_code).strip(),
            }
        verification = _model_dict(state.verification)
        verification_classification = str(verification.get("classification") or "").strip()
        if verification_classification and verification_classification != "success":
            reason_code = str(verification.get("reason_code") or verification_classification or "FAILED").strip()
            return {
                "classification": self._failure_classification(
                    phase="verification",
                    status="failed" if verification_classification == "failed" else "blocked",
                    code=reason_code,
                ),
                "phase": "verification",
                "reason_code": reason_code,
                "message": str(verification.get("summary") or reason_code).strip(),
            }
        return None

    def _apply_remediation_control(self, state: AixOrchestratorV2RunState) -> dict[str, Any] | None:
        context = self._remediation_context(state)
        if context is None:
            return None
        ledger = build_family_blocker_ledger(
            task_client=self._workhub,
            project_id=context["project_id"],
            family_root_task_number=context["family_root_task_number"],
            family_key=context["family_key"],
        )
        return self._finalize_remediation_control(state=state, ledger=ledger, **context)

    async def _apply_remediation_control_async(self, state: AixOrchestratorV2RunState) -> dict[str, Any] | None:
        context = self._remediation_context(state)
        if context is None:
            return None
        list_tasks = getattr(self._workhub, "list_tasks", None)
        if not callable(list_tasks):
            return self._apply_remediation_control(state)
        rows_by_status: dict[str, list[dict[str, Any]]] = {}
        for status in LEDGER_SCAN_STATUSES:
            try:
                rows = await list_tasks(
                    project_id=context["project_id"],
                    limit=200,
                    status=status,
                )
            except Exception:
                continue
            rows_by_status[status] = [row for row in rows if isinstance(row, dict)]
        ledger = build_family_blocker_ledger_from_rows(
            rows_by_status=rows_by_status,
            family_root_task_number=context["family_root_task_number"],
            family_key=context["family_key"],
        )
        return self._finalize_remediation_control(state=state, ledger=ledger, **context)

    def _remediation_context(self, state: AixOrchestratorV2RunState) -> dict[str, Any] | None:
        failure_view = self._remediation_failure_view(state)
        project_id = self._project_id(state)
        if failure_view is None or not project_id:
            return None
        task_number = self._task_number(state)
        family_root_task_number = self._family_root_task_number(state)
        family_key = f"{project_id}:{family_root_task_number}"
        defect_class = str(failure_view["classification"] or "").strip().lower() or "unknown"
        reason_code = str(failure_view["reason_code"] or "UNKNOWN_REASON").strip()
        phase = str(failure_view["phase"] or "unknown").strip().lower()
        target_files = self._candidate_target_files(state)
        fingerprint = build_defect_fingerprint(
            family_root_task_number=family_root_task_number,
            family_key=family_key,
            defect_class=defect_class,
            reason_code=reason_code,
            phase=phase,
            title=f"AIX v2 remediation: {reason_code} family #{family_root_task_number}",
            target_files=target_files,
        )
        return {
            "project_id": project_id,
            "task_number": task_number,
            "family_root_task_number": family_root_task_number,
            "family_key": family_key,
            "defect_class": defect_class,
            "reason_code": reason_code,
            "phase": phase,
            "failure_view": failure_view,
            "fingerprint": fingerprint,
            "contract_validation": child_task_contract_validator(
                title=f"AIX v2 remediation: {reason_code} family #{family_root_task_number}",
                description=str(failure_view["message"] or reason_code).strip() or reason_code,
                metadata_json={
                    "task_intent": "system_issue_auto_submit",
                    "target_files": target_files,
                    "validation_commands": self._candidate_validation_commands(state),
                    "failure_code": reason_code,
                    "execution_phase": phase,
                    "parent_task_number": family_root_task_number,
                },
            ),
        }

    def _finalize_remediation_control(
        self,
        *,
        state: AixOrchestratorV2RunState,
        ledger: Any,
        task_number: int,
        family_root_task_number: int,
        family_key: str,
        defect_class: str,
        reason_code: str,
        phase: str,
        fingerprint: Any,
        contract_validation: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        policy = default_remediation_policy()
        raw_decision = should_create_remedial_task(
            fingerprint=fingerprint,
            contract_validation=contract_validation,
            ledger=ledger,
            policy=policy,
        )
        existing_blocker = raw_decision.get("existing_blocker")
        if raw_decision.get("action") == "create":
            effective_action = "observe_only"
            effective_reason: str | None = "REMEDIATION_AUTO_CREATE_DISABLED_IN_AIX_V2"
        else:
            effective_action = str(raw_decision.get("action") or "suppress")
            effective_reason = str(raw_decision.get("reason_code") or "").strip() or None
        existing_blocker_payload = (
            {
                "task_number": int(existing_blocker.task_number),
                "task_id": existing_blocker.task_id,
                "status": existing_blocker.status,
                "title": existing_blocker.title,
            }
            if existing_blocker is not None
            else None
        )
        payload = {
            "family_key": family_key,
            "family_root_task_number": family_root_task_number,
            "task_number": task_number,
            "defect_class": defect_class,
            "phase": phase,
            "reason_code": reason_code,
            "fingerprint_key": build_fingerprint_key(fingerprint),
            "contract_validation": contract_validation,
            "raw_decision": {
                "action": str(raw_decision.get("action") or "").strip() or None,
                "reason_code": str(raw_decision.get("reason_code") or "").strip() or None,
                "audit_event": str(raw_decision.get("audit_event") or "").strip() or None,
            },
            "effective_action": effective_action,
            "effective_reason_code": effective_reason,
            "existing_blocker": existing_blocker_payload,
            "ledger_summary": {
                "open_count": len(ledger.open_entries),
                "history_count": len(ledger.all_entries),
            },
        }
        payload_sha = validator_sha(payload)
        ledger_state = dict(state.validator_ledger or {})
        last_validated = dict(ledger_state.get("last_validated_sha") or {})
        if last_validated.get("remediation_control") == payload_sha:
            return payload
        last_validated["remediation_control"] = payload_sha
        events = list(ledger_state.get("events") or [])
        events.append(
            {
                "event": "remediation_control_decision",
                "phase": phase,
                "reason_code": reason_code,
                "defect_class": defect_class,
                "fingerprint_key": payload["fingerprint_key"],
                "effective_action": effective_action,
                "effective_reason_code": effective_reason,
                "attached_task_number": int(existing_blocker_payload["task_number"])
                if existing_blocker_payload
                else None,
            }
        )
        state.validator_ledger = {"last_validated_sha": last_validated, "events": events}
        state.add_artifact(name="remediation_control", payload=payload)
        self._logger.info(
            "remediation_control",
            run_id=state.run_id,
            task_id=state.task_id,
            phase=phase,
            event_name="remediation_control",
            reason_code=reason_code,
            classification=defect_class,
            effective_action=effective_action,
            effective_reason_code=effective_reason,
            fingerprint_key=payload["fingerprint_key"],
        )
        return payload

    def _add_execution_artifacts(self, state: AixOrchestratorV2RunState) -> None:
        execution = _model_dict(state.execution)
        state.add_artifact(
            name="execution_summary",
            payload={
                "status": execution.get("status"),
                "changed_files": list(execution.get("changed_files") or []),
                "command_count": len(list(execution.get("commands") or [])),
                "commands": [
                    {
                        "command_text": str(_as_dict(item).get("command_text") or "").strip() or None,
                        "exit_code": _as_dict(item).get("exit_code"),
                        "classification": str(_as_dict(item).get("classification") or "").strip() or None,
                    }
                    for item in list(execution.get("commands") or [])
                    if isinstance(item, dict)
                ],
                "diff_refs": list(execution.get("diff_refs") or []),
            },
        )
        if list(execution.get("changed_files") or []):
            state.add_artifact(
                name="changed_files", payload={"changed_files": list(execution.get("changed_files") or [])}
            )
        if list(execution.get("diff_refs") or []):
            state.add_artifact(name="diff_refs", payload={"diff_refs": list(execution.get("diff_refs") or [])})

    def _add_recon_artifact(self, state: AixOrchestratorV2RunState) -> None:
        recon = _model_dict(state.recon)
        state.add_artifact(name="recon", payload=compact_recon_for_active_packet(recon))

    def _add_verifier_artifact(self, state: AixOrchestratorV2RunState) -> None:
        verification = _model_dict(state.verification)
        state.add_artifact(
            name="verifier_result",
            payload=compact_verification_for_task_metadata(
                verification,
                evidence_ref=f"aix://orchestrator-v2/runs/{state.run_id}",
                machine_proof_refs=[],
            ),
        )

    def _add_writeback_artifact(self, state: AixOrchestratorV2RunState) -> None:
        writeback = _as_dict(_model_dict(state.writeback).get("writeback"))
        state.add_artifact(name="writeback", payload=compact_writeback_for_task_metadata(writeback))

    def _add_run_summary_artifact(self, state: AixOrchestratorV2RunState) -> None:
        from backend.aix_orchestrator_v2.run_summary import build_packet_audit

        state.add_artifact(name="packet_audit", payload=build_packet_audit(state.model_dump(mode="python")))
        state.add_artifact(name="run_summary", payload=build_run_summary(state.model_dump(mode="python")))

    async def load_task(
        self,
        *,
        project_id: str,
        task_number: int,
        workspace_root: str,
        run_id: str | None = None,
        tenant_id: str | None = None,
    ) -> AixOrchestratorV2RunState:
        state = AixOrchestratorV2RunState(run_id=run_id) if run_id else AixOrchestratorV2RunState()
        task_payload = await self._workhub.get_task_by_number(
            project_id=project_id, task_number=task_number, tenant_id=tenant_id
        )
        if not task_payload:
            state.fail(
                classification="blocked_needs_human",
                phase="load_task",
                code="TASK_NOT_FOUND",
                message=f"Task #{task_number} not found in project {project_id}",
                status="blocked",
            )
            return state
        state.task_id = str(task_payload.get("id") or task_payload.get("task_id") or "").strip() or None
        state.task_payload = dict(task_payload)

        # Best-effort: fetch WorkHub's pre-built ai_context briefing instead of leaving
        # every run to cold-explore the repo for context WorkHub already computed once
        # (relevant files, conventions, prior attempts on this task, CodeIntel symbols).
        # Never blocks or fails task load -- absence just means recon has less of a
        # head start, not that loading itself fails.
        ai_context_briefing = await self._workhub.get_task_ai_context_markdown(
            project_id=project_id, task_number=task_number, tenant_id=tenant_id
        )

        state.packet = {
            "schema_name": "aix.orchestrator.control_packet.v2",
            "schema_version": "v2",
            "task_ref": {
                "project_id": project_id,
                "task_id": state.task_id,
                "task_number": task_number,
            },
            "workspace_ref": {"workspace_root": workspace_root},
            "control": {
                "run_id": state.run_id,
                "cloud_event_boundary": "required_at_execution",
                "planner_requires_recon": True,
            },
            "ai_context_briefing": ai_context_briefing,
        }
        state.validator_ledger = {"last_validated_sha": {}, "events": []}
        state.status = "loaded"
        state.current_phase = "load_task"
        state.timestamps.loaded_at = utc_now()
        if ai_context_briefing:
            state.add_artifact(
                name="workhub_ai_context_briefing",
                payload={"source": "workhub_ai_context", "format": "markdown", "markdown": ai_context_briefing},
            )
        state.touch()
        self._persist(state)
        return state

    async def run_inline(
        self,
        *,
        tenant_id: str,
        project_id: str,
        task_number: int,
        workspace_root: str,
        repo_key: str,
        explicit_profile_id: str | None,
        actor_id: str,
        writeback: bool,
    ) -> AixOrchestratorV2RunState:
        state = await self.load_task(
            project_id=project_id, task_number=task_number, workspace_root=workspace_root, tenant_id=tenant_id
        )
        if state.failure:
            return state
        if not Path(workspace_root).exists():
            state.fail(
                classification="blocked_needs_human",
                phase="load_task",
                code="WORKSPACE_NOT_FOUND",
                message=f"workspace_root does not exist: {workspace_root}",
                status="blocked",
            )
            return state
        workspace_policy_failure = _workspace_outside_allowed_roots(workspace_root)
        if workspace_policy_failure is not None:
            state.fail(
                classification="blocked_needs_human",
                phase="load_task",
                code=str(workspace_policy_failure["code"]),
                message=str(workspace_policy_failure["message"]),
                status="blocked",
                details=workspace_policy_failure,
            )
            self._persist(state)
            return state
        contracts = await self._workhub.list_task_execution_contracts(
            project_id=project_id, task_number=task_number, tenant_id=tenant_id
        )
        recon_result = ReconResult.model_validate(
            await build_recon(
                tenant_id=tenant_id,
                project_id=project_id,
                task_payload=state.task_payload,
                execution_contracts=contracts,
                workspace_root=workspace_root,
            )
        )
        state.recon = recon_result
        state.packet["slim_packet"] = dict(recon_result.slim_packet_inputs)
        state.status = "recon_complete"
        state.current_phase = "recon"
        state.timestamps.recon_at = utc_now()
        self._add_recon_artifact(state)
        self._persist(state)
        if str(recon_result.status or "").strip() == "insufficient":
            recon_reason = str(recon_result.reason or "target_file_missing")
            self._record_guard_event(
                state,
                guard_name="recon_admissibility",
                reason_code=recon_reason,
                phase="recon",
                payload={"recon": _model_dict(recon_result)},
            )
            state.fail(
                classification=self._failure_classification(
                    phase="recon",
                    status="blocked",
                    code=recon_reason,
                ),
                phase="recon",
                code=recon_reason.upper(),
                message=recon_reason,
                status="blocked",
                details={"recon": _model_dict(recon_result)},
            )
            self._apply_remediation_control(state)
            verification_result = VerificationResult.model_validate(
                verify_run(plan={}, execution={}, failure=_model_dict(state.failure))
            )
            state.verification = verification_result
            state.current_phase = "verification"
            state.timestamps.verified_at = utc_now()
            self._add_verifier_artifact(state)
            self._persist(state)
            if writeback:
                from backend.legacy.aix_orchestrator_v2.writeback_service import writeback_v2_run

                failure_classification = state.failure.classification if state.failure else "POLICY_FAIL"
                state.execution = ExecutionResult(
                    status="skipped",
                    run_result={
                        "ingress": "aix_orchestrator_v2.execution",
                        "schema_id": None,
                        "executable": False,
                        "status": "failed",
                        "run_id": None,
                        "task_id": state.task_id,
                        "task_number": task_number,
                        "project_id": project_id,
                        "correlation_id": state.run_id,
                        "packet_id": None,
                        "replayed": False,
                        "evidence_refs": [],
                        "evidence": {},
                        "telemetry": {},
                        "error": {
                            "code": recon_reason.upper(),
                            "message": recon_reason,
                            "retryable": False,
                            "details": {
                                "classification": failure_classification,
                                "semantic_state": semantic_state_for_classification(failure_classification),
                            },
                        },
                    },
                )
                writeback_result = await writeback_v2_run(
                    client=self._workhub,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    project_id=project_id,
                    task_payload=state.task_payload,
                    run_id=state.run_id,
                    execution=_model_dict(state.execution),
                    verification=_model_dict(verification_result),
                )
                state.writeback = WritebackResult.model_validate(writeback_result)
                self._add_writeback_artifact(state)
                self._add_run_summary_artifact(state)
                state.timestamps.writeback_at = utc_now()
                state.current_phase = "writeback"
                self._persist(state)
            return state

        plan_result = PlannerResult.model_validate(
            plan_task(
                tenant_id=tenant_id,
                project_id=project_id,
                correlation_id=state.run_id,
                workspace_root=workspace_root,
                task_payload=state.task_payload,
                recon=_model_dict(state.recon),
            )
        )
        state.plan = plan_result
        state.timestamps.planned_at = utc_now()
        state.current_phase = "planner"
        state.add_artifact(name="planner_trace", payload=_as_dict(plan_result.planner_trace))
        state.add_artifact(name="planner_input_summary", payload=_as_dict(plan_result.input_summary))
        state.add_artifact(
            name="planner_raw",
            payload={"raw_output_text": str(plan_result.raw_output_text or "").strip() or None},
        )
        state.add_artifact(name="normalized_plan", payload={"steps": list(plan_result.normalized_plan or [])})
        if str(plan_result.trace_artifact_ref or "").strip():
            state.add_artifact(
                name="planner_trace_ref",
                payload={"trace_artifact_ref": str(plan_result.trace_artifact_ref)},
                ref=str(plan_result.trace_artifact_ref),
            )
        self._persist(state)
        if str(plan_result.status or "") == "blocked":
            blocked_reason = str(plan_result.blocked_reason or "planner blocked")
            self._record_guard_event(
                state,
                guard_name="planner_precheck_or_validation",
                reason_code=blocked_reason,
                phase="planner",
                payload={"planner_trace": _as_dict(plan_result.planner_trace)},
            )
            state.status = "blocked"
            verification_result = VerificationResult.model_validate(
                verify_run(plan=_model_dict(plan_result), execution={}, failure=None)
            )
            state.verification = verification_result
            state.current_phase = "verification"
            state.timestamps.verified_at = utc_now()
            self._add_verifier_artifact(state)
            state.fail(
                classification=self._failure_classification(
                    phase="planner",
                    status="blocked",
                    code=blocked_reason,
                ),
                phase="planner",
                code=blocked_reason.upper().replace(" ", "_"),
                message=blocked_reason,
                status="blocked",
                details={"planner_trace": _as_dict(plan_result.planner_trace)},
            )
            self._apply_remediation_control(state)
            self._persist(state)
            if writeback:
                from backend.legacy.aix_orchestrator_v2.writeback_service import writeback_v2_run

                failure_classification = state.failure.classification if state.failure else "POLICY_FAIL"
                state.execution = ExecutionResult(
                    status="skipped",
                    run_result={
                        "ingress": "aix_orchestrator_v2.execution",
                        "schema_id": None,
                        "executable": False,
                        "status": "failed",
                        "run_id": None,
                        "task_id": state.task_id,
                        "task_number": task_number,
                        "project_id": project_id,
                        "correlation_id": state.run_id,
                        "packet_id": None,
                        "replayed": False,
                        "evidence_refs": [],
                        "evidence": {},
                        "telemetry": {},
                        "error": {
                            "code": blocked_reason.upper().replace(" ", "_"),
                            "message": blocked_reason,
                            "retryable": False,
                            "details": {
                                "classification": failure_classification,
                                "semantic_state": semantic_state_for_classification(failure_classification),
                            },
                        },
                    },
                )
                writeback_result = await writeback_v2_run(
                    client=self._workhub,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    project_id=project_id,
                    task_payload=state.task_payload,
                    run_id=state.run_id,
                    execution=_model_dict(state.execution),
                    verification=_model_dict(verification_result),
                )
                state.writeback = WritebackResult.model_validate(writeback_result)
                self._add_writeback_artifact(state)
                self._add_run_summary_artifact(state)
                state.timestamps.writeback_at = utc_now()
                state.current_phase = "writeback"
                self._persist(state)
            return state

        state.status = "planned"
        state.current_phase = "execution"
        execution_result = ExecutionResult.model_validate(
            execute_plan(
                project_id=project_id,
                run_id=state.run_id,
                task_payload=state.task_payload,
                recon=_model_dict(recon_result),
                plan=_model_dict(plan_result),
                workspace_root=workspace_root,
                repo_key=repo_key,
                explicit_profile_id=explicit_profile_id,
                state_packet=_model_dict(state.packet),
            )
        )
        state.execution = execution_result
        state.timestamps.executed_at = utc_now()
        state.add_artifact(name="execution_trace", payload=_as_dict(execution_result.trace))
        self._add_execution_artifacts(state)
        self._persist(state)
        run_failure = _as_dict(execution_result.failure) or None
        verification_result = VerificationResult.model_validate(
            verify_run(plan=_model_dict(plan_result), execution=_model_dict(execution_result), failure=run_failure)
        )
        state.verification = verification_result
        state.current_phase = "verification"
        state.timestamps.verified_at = utc_now()
        self._add_verifier_artifact(state)
        status_lookup: dict[str, RunStatus] = {
            "success": "success",
            "CODE_FAIL": "failed",
            "INFRA_FAIL": "blocked",
            "POLICY_FAIL": "blocked",
            "SHAPING_FAIL": "blocked",
            "VERIFICATION_FAIL": "failed",
        }
        verification_status = status_lookup.get(str(verification_result.classification or ""), "failed")
        state.status = verification_status
        if state.status in {"blocked", "failed"}:
            failure_payload = run_failure or {
                "message": str(verification_result.summary or "execution failed"),
                "code": str(verification_result.reason_code or verification_result.classification or "FAILED").upper(),
            }
            state.failure = None
            state.fail(
                classification=self._failure_classification(
                    phase="verification",
                    status=state.status,
                    code=str(failure_payload.get("code") or "FAILED"),
                ),
                phase="verification",
                code=str(failure_payload.get("code") or "FAILED"),
                message=str(failure_payload.get("message") or verification_result.summary or "failed"),
                status=state.status,
                details={
                    "verification": _model_dict(verification_result),
                    "semantic_state": semantic_state_for_classification(str(verification_result.classification or "")),
                },
            )
            self._apply_remediation_control(state)
        self._persist(state)
        if writeback:
            from backend.legacy.aix_orchestrator_v2.writeback_service import writeback_v2_run

            writeback_result = await writeback_v2_run(
                client=self._workhub,
                tenant_id=tenant_id,
                actor_id=actor_id,
                project_id=project_id,
                task_payload=state.task_payload,
                run_id=state.run_id,
                execution=_model_dict(execution_result),
                verification=_model_dict(verification_result),
            )
            writeback_result_model = WritebackResult.model_validate(writeback_result)
            state.writeback = writeback_result_model
            self._add_writeback_artifact(state)
            self._add_run_summary_artifact(state)
            state.timestamps.writeback_at = utc_now()
            state.current_phase = "writeback"
            writeback_error_message = self._writeback_error_message(writeback_result_model)
            if writeback_error_message:
                state.failure = None
                state.fail(
                    classification=self._failure_classification(
                        phase="writeback",
                        status="failed",
                        code="WRITEBACK_FAILED",
                    ),
                    phase="writeback",
                    code="WRITEBACK_FAILED",
                    message=writeback_error_message,
                    status="failed",
                    details={"writeback": _model_dict(writeback_result_model)},
                )
                self._apply_remediation_control(state)
            state.status = "writeback_complete" if state.status == "success" else state.status
            self._persist(state)
        if state.status == "writeback_complete":
            state.timestamps.completed_at = utc_now()
            self._persist(state)
            # C003: orchestrator_v2 must not mint canonical terminal success.
            try:
                from backend.aix_core.runtime.governance.completion_authority import adapter_non_canonical_report

                adapter_non_canonical_report(
                    source="aix_orchestrator_v2",
                    tenant_id=str(getattr(state, "tenant_id", "") or ""),
                    objective_id=str(state.task_id or ""),
                    run_id=str(state.run_id or ""),
                    claimed_status="writeback_complete",
                    detail="Awaiting C004 adapter retirement; not Completion Authority.",
                )
            except Exception:
                pass
        return state
