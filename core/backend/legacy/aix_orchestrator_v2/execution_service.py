from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.legacy.aix_orchestrator_v2.discipline import inject_file_type_validation_commands
from backend.aix_orchestrator_v2.models import ExecutionResult
from backend.hyeve_aix_service.config import settings
from backend.services.devexecutor_proposal_workspace_service import ProposalWorkspaceError, ProposalWorkspaceManager


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _field_paths(value: Any, *, prefix: str = "") -> set[str]:
    if isinstance(value, dict):
        paths: set[str] = set()
        for key, nested in value.items():
            key_text = str(key).strip()
            if not key_text:
                continue
            path = f"{prefix}.{key_text}" if prefix else key_text
            paths.add(path)
            paths.update(_field_paths(nested, prefix=path))
        return paths
    if isinstance(value, list):
        paths = {prefix} if prefix else set()
        for item in value:
            if isinstance(item, (dict, list)):
                paths.update(_field_paths(item, prefix=prefix))
        return paths
    return {prefix} if prefix else set()


def _contract_drift_details(*, expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    expected_fields = _field_paths(expected)
    actual_fields = _field_paths(actual)
    return {
        "expected": expected,
        "actual": actual,
        "missing_fields": sorted(expected_fields - actual_fields),
        "added_fields": sorted(actual_fields - expected_fields),
    }


def _contract_drift_result(
    *,
    reason_code: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
    envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return ExecutionResult(
        status="failed",
        run_result={},
        changed_files=[],
        commands=[],
        tests={},
        diff_refs=[],
        failure={
            "code": reason_code,
            "message": "execution contract drift detected before runtime dispatch",
            "retryable": False,
            "details": _contract_drift_details(expected=expected, actual=actual),
        },
        trace={"contract_consistency": {"reason_code": reason_code}},
        envelope=dict(envelope or {}),
    ).model_dump(mode="python")


def _local_ref(path_value: str | None) -> str | None:
    text = str(path_value or "").strip()
    if not text:
        return None
    return f"local://{Path(text).resolve().as_posix()}"


def _requires_mutation_workspace(plan: dict[str, Any]) -> bool:
    mutation_tools = {
        "apply_patch",
        "create_file",
        "delete_file",
        "edit_file",
        "extend_python_signature",
        "insert_at_anchor",
        "rename_file",
        "replace_function",
        "replace_range",
        "write_file",
    }
    for step in list(plan.get("steps") or []):
        if not isinstance(step, dict):
            continue
        step_name = str(step.get("tool") or step.get("type") or "").strip()
        if step_name in mutation_tools:
            return True
    return False


def _workspace_is_managed_mutation_workspace(workspace_root: str) -> bool:
    workspace = Path(workspace_root).resolve()
    return (workspace / ".devexecutor_proposal_workspace.json").exists()


def _build_proposal_workspace_manager() -> ProposalWorkspaceManager:
    return ProposalWorkspaceManager(
        str(settings.DEVEXECUTOR_PROPOSAL_WORKSPACE_ROOT or "C:/dev/hyeve-proposals"),
        branch_prefix=str(settings.DEVEXECUTOR_PROPOSAL_BRANCH_PREFIX or "aix/proposals"),
        min_free_bytes=int(settings.DEVEXECUTOR_PROPOSAL_MIN_FREE_BYTES or 0),
    )


def _prepare_execution_workspace(
    *,
    project_id: str,
    run_id: str,
    task_payload: dict[str, Any],
    plan: dict[str, Any],
    workspace_root: str,
) -> tuple[str, dict[str, Any] | None]:
    if not _requires_mutation_workspace(plan):
        return workspace_root, None
    if not bool(settings.DEVEXECUTOR_MUTATION_SANDBOX_REQUIRED):
        return workspace_root, None
    if _workspace_is_managed_mutation_workspace(workspace_root):
        return workspace_root, None
    manager = _build_proposal_workspace_manager()
    context = manager.create_workspace(
        project_id=project_id,
        job_id=run_id,
        task_number=int(task_payload.get("task_number") or 0) or None,
        source_root=workspace_root,
    )
    return str(context.workspace_root), {"manager": manager, "context": context}


def _finalize_execution_workspace(
    *,
    run_result: dict[str, Any],
    proposal_state: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(proposal_state, dict):
        return None
    manager = proposal_state.get("manager")
    context = proposal_state.get("context")
    if not isinstance(manager, ProposalWorkspaceManager) or context is None:
        return None

    exported_artifacts: dict[str, Any] | None = None
    try:
        artifacts = manager.capture_review_artifacts(context=context)
        exported_artifacts = dict(artifacts)
        for key in ("changed_files_ref", "diff_ref", "status_ref"):
            exported_artifacts[key] = _local_ref(exported_artifacts.get(key))
        metadata_path = Path(str(context.artifact_root)) / ".devexecutor_proposal_artifacts.json"
        if metadata_path.exists():
            exported_artifacts["metadata_ref"] = _local_ref(str(metadata_path))
        evidence = _as_dict(run_result.get("evidence"))
        changed_files = [str(item).strip() for item in list(evidence.get("changed_files") or []) if str(item).strip()]
        for candidate in list(exported_artifacts.get("changed_files") or []):
            value = str(candidate).strip()
            if value and value not in changed_files:
                changed_files.append(value)
        refs = [str(item).strip() for item in list(run_result.get("evidence_refs") or []) if str(item).strip()]
        for candidate in (
            exported_artifacts.get("diff_ref"),
            exported_artifacts.get("changed_files_ref"),
            exported_artifacts.get("status_ref"),
            exported_artifacts.get("metadata_ref"),
        ):
            value = str(candidate or "").strip()
            if value and value not in refs:
                refs.append(value)
        evidence["changed_files"] = sorted(set(changed_files))
        evidence["proposal_workspace"] = {
            "proposal_id": str(getattr(context, "proposal_id", "") or "").strip() or None,
            "workspace_root": str(getattr(context, "workspace_root", "") or "").strip() or None,
            "checkout_root": str(getattr(context, "checkout_root", "") or "").strip() or None,
            "branch_name": str(getattr(context, "branch_name", "") or "").strip() or None,
            "base_commit": str(getattr(context, "base_commit", "") or "").strip() or None,
        }
        evidence["proposal_artifacts"] = exported_artifacts
        evidence["refs"] = refs
        run_result["evidence"] = evidence
        run_result["evidence_refs"] = refs
    except ProposalWorkspaceError as exc:
        exported_artifacts = {"error": str(exc), "workspace_root": str(getattr(context, "workspace_root", "") or "")}
    finally:
        retain = bool(settings.DEVEXECUTOR_PROPOSAL_RETAIN_RUNS)
        manager.teardown_workspace(
            context=context,
            retain=retain,
            retention_reason="awaiting_acceptance" if retain else "auto_removed",
            drop_branch=not retain,
        )
    return exported_artifacts


def build_execution_envelope(
    *,
    project_id: str,
    run_id: str,
    task_payload: dict[str, Any],
    recon: dict[str, Any],
    plan: dict[str, Any],
    workspace_root: str,
    repo_key: str,
    explicit_profile_id: str | None,
) -> dict[str, Any]:
    task_number = int(task_payload.get("task_number") or 0) or 1
    task_id = str(task_payload.get("id") or task_payload.get("task_id") or "").strip() or None
    validation_command = str(plan.get("declared_validation") or recon.get("validation_target") or "pytest").strip()
    slim_packet = _as_dict(recon.get("slim_packet_inputs"))
    envelope_id = f"evt-{uuid4().hex[:20]}"
    return {
        "envelope": {
            "specversion": "1.0",
            "type": "com.aix.orchestrator.v2.execution.requested",
            "source": "aix.orchestrator.v2",
            "id": envelope_id,
            "subject": str(task_id or f"task-{task_number}"),
            "datacontenttype": "application/json",
            "data": {
                "packet": {
                    "schema_name": "aix_execution_packet_current",
                    "schema_version": "current",
                    "tenant_id": "default",
                    "project_id": project_id,
                    "correlation_id": run_id,
                    "packet_id": f"pkt-{uuid4().hex[:16]}",
                    "causation_id": envelope_id,
                    "idempotency_key": run_id,
                    "sender": {
                        "agent_id": "aix-orchestrator-v2",
                        "role": "orchestrator",
                        "provider": "internal",
                        "model": "temporal-spine",
                    },
                    "target": {"agent_id": "devexecutor-runtime", "role": "executor"},
                    "step": {
                        "index": task_number,
                        "status": "queued",
                        "summary": str(task_payload.get("title") or "queued for v2 execution"),
                        "evidence_refs": [],
                    },
                },
                "execution": {
                    "task_id": task_id,
                    "task_number": task_number,
                    "workspace_root": workspace_root,
                    "repo_key": repo_key,
                    "explicit_profile_id": explicit_profile_id,
                    "steps": list(plan.get("steps") or []),
                    "token_budget": None,
                    "max_runtime_seconds": 180.0,
                    "max_patch_attempts": 10,
                    "idempotency_key": run_id,
                    "slim_packet": slim_packet,
                    "telemetry": {
                        "phase": "execution",
                        "slim_packet": slim_packet,
                        "validation_command": validation_command,
                    },
                },
            },
        }
    }


def _validate_contract_consistency(
    *,
    recon: dict[str, Any],
    state_packet: dict[str, Any] | None,
    envelope: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    canonical_slim_packet = _as_dict(recon.get("slim_packet_inputs"))
    state_slim_packet = _as_dict(_as_dict(state_packet).get("slim_packet"))
    if canonical_slim_packet and state_slim_packet and canonical_slim_packet != state_slim_packet:
        return _contract_drift_result(
            reason_code="SLIM_PACKET_STATE_DRIFT",
            expected=canonical_slim_packet,
            actual=state_slim_packet,
            envelope=envelope,
        )
    if not envelope:
        return None
    execution_payload = _as_dict(_as_dict(_as_dict(envelope.get("envelope")).get("data")).get("execution"))
    dispatch_slim_packet = _as_dict(execution_payload.get("slim_packet"))
    telemetry_slim_packet = _as_dict(_as_dict(execution_payload.get("telemetry")).get("slim_packet"))
    for reason_code, candidate in (
        ("SLIM_PACKET_DISPATCH_DRIFT", dispatch_slim_packet),
        ("SLIM_PACKET_TELEMETRY_DRIFT", telemetry_slim_packet),
    ):
        if canonical_slim_packet and candidate and canonical_slim_packet != candidate:
            return _contract_drift_result(
                reason_code=reason_code,
                expected=canonical_slim_packet,
                actual=candidate,
                envelope=envelope,
            )
    return None


def _inject_validation_steps(*, plan: dict[str, Any], recon: dict[str, Any]) -> dict[str, Any]:
    updated = dict(plan)
    steps = [dict(item) for item in list(plan.get("steps") or []) if isinstance(item, dict)]
    target_files = [
        str(item).strip()
        for item in (
            list(plan.get("file_targets") or []) or list(recon.get("target_files") or []) or [recon.get("target_file")]
        )
        if str(item).strip()
    ]
    existing_commands = [
        str(_as_dict(step.get("args")).get("command_text") or "").strip()
        for step in steps
        if str(step.get("tool") or step.get("type") or "").strip() == "cmd.run"
        and str(_as_dict(step.get("args")).get("command_text") or "").strip()
    ]
    declared_validation = str(plan.get("declared_validation") or recon.get("validation_target") or "").strip()
    if declared_validation and declared_validation not in existing_commands:
        steps.append({"tool": "cmd.run", "args": {"command_text": declared_validation}})
        existing_commands.append(declared_validation)
    for command_text in inject_file_type_validation_commands(
        changed_targets=target_files,
        existing_commands=existing_commands,
    ):
        if command_text in existing_commands:
            continue
        steps.append({"tool": "cmd.run", "args": {"command_text": command_text}})
        existing_commands.append(command_text)
    updated["steps"] = steps
    return updated


def _validate_materialized_execution_plan(*, plan: dict[str, Any]) -> dict[str, Any] | None:
    planner_packet = _as_dict(plan.get("planner_packet"))
    if str(planner_packet.get("planner_mode") or "").strip() != "packet_bound_replace_function":
        return None
    target_file = str(planner_packet.get("target_file") or "").strip().replace("\\", "/").strip("/")
    target_symbol = str(planner_packet.get("target_symbol") or "").strip()
    if not target_file or not target_symbol:
        return _contract_drift_result(
            reason_code="MATERIALIZED_REPLACE_FUNCTION_PACKET_INCOMPLETE",
            expected={"target_file": target_file or None, "target_symbol": target_symbol or None},
            actual=planner_packet,
        )
    for step in list(plan.get("steps") or []):
        if not isinstance(step, dict):
            continue
        step_name = str(step.get("tool") or step.get("type") or "").strip()
        if step_name != "replace_function":
            continue
        args = _as_dict(step.get("args"))
        relative_path = str(args.get("relative_path") or args.get("file") or step.get("file") or "").strip()
        relative_path = relative_path.replace("\\", "/").strip("/")
        qualified_symbol = str(
            args.get("qualified_symbol") or args.get("function_name") or step.get("qualified_symbol") or ""
        ).strip()
        replacement = str(args.get("replacement") or step.get("replacement") or "").strip()
        if relative_path != target_file or qualified_symbol != target_symbol or not replacement:
            return _contract_drift_result(
                reason_code="MATERIALIZED_REPLACE_FUNCTION_FIELDS_REQUIRED",
                expected={
                    "relative_path": target_file,
                    "qualified_symbol": target_symbol,
                    "replacement": "required",
                },
                actual={
                    "relative_path": relative_path or None,
                    "qualified_symbol": qualified_symbol or None,
                    "replacement_present": bool(replacement),
                },
            )
        return None
    return _contract_drift_result(
        reason_code="MATERIALIZED_REPLACE_FUNCTION_STEP_REQUIRED",
        expected={"tool": "replace_function", "relative_path": target_file, "qualified_symbol": target_symbol},
        actual={
            "step_tools": [
                str(item.get("tool") or item.get("type") or "")
                for item in list(plan.get("steps") or [])
                if isinstance(item, dict)
            ]
        },
    )


def execute_plan(
    *,
    project_id: str,
    run_id: str,
    task_payload: dict[str, Any],
    recon: dict[str, Any],
    plan: dict[str, Any],
    workspace_root: str,
    repo_key: str,
    explicit_profile_id: str | None,
    state_packet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from backend.services.aix_enveloped_run_service import execute_enveloped_run

    effective_plan = _inject_validation_steps(plan=plan, recon=recon)
    materialization_failure = _validate_materialized_execution_plan(plan=effective_plan)
    if materialization_failure is not None:
        return materialization_failure
    contract_drift = _validate_contract_consistency(recon=recon, state_packet=state_packet)
    if contract_drift is not None:
        return contract_drift
    proposal_state: dict[str, Any] | None = None
    effective_workspace_root = workspace_root
    try:
        effective_workspace_root, proposal_state = _prepare_execution_workspace(
            project_id=project_id,
            run_id=run_id,
            task_payload=task_payload,
            plan=effective_plan,
            workspace_root=workspace_root,
        )
    except ProposalWorkspaceError as exc:
        return ExecutionResult(
            status="failed",
            run_result={},
            changed_files=[],
            commands=[],
            tests={},
            diff_refs=[],
            failure={
                "code": "PROPOSAL_WORKSPACE_PREP_FAILED",
                "message": str(exc),
                "retryable": False,
                "details": {"workspace_root": workspace_root},
            },
            trace={"proposal_workspace": {"error": str(exc), "workspace_root": workspace_root}},
            envelope={},
        ).model_dump(mode="python")

    envelope = build_execution_envelope(
        project_id=project_id,
        run_id=run_id,
        task_payload=task_payload,
        recon=recon,
        plan=effective_plan,
        workspace_root=effective_workspace_root,
        repo_key=repo_key,
        explicit_profile_id=explicit_profile_id,
    )
    contract_drift = _validate_contract_consistency(recon=recon, state_packet=state_packet, envelope=envelope)
    if contract_drift is not None:
        return contract_drift
    result = execute_enveloped_run(
        envelope,
        ingress="aix_orchestrator_v2.execution",
        auth_ctx={"authorization_present": True},
    )
    run_result = result.to_dict()
    proposal_artifacts = _finalize_execution_workspace(run_result=run_result, proposal_state=proposal_state)
    evidence = _as_dict(run_result.get("evidence"))
    return ExecutionResult(
        status=str(result.status),
        run_result=run_result,
        changed_files=list(evidence.get("changed_files") or []),
        commands=list(evidence.get("commands") or []),
        tests=evidence.get("tests") or {},
        diff_refs=list(run_result.get("evidence_refs") or []),
        failure=result.error.to_dict() if result.error else None,
        trace={
            "telemetry": result.telemetry,
            "evidence": evidence,
            "evidence_refs": run_result.get("evidence_refs") or [],
            "proposal_artifacts": proposal_artifacts,
        },
        envelope=envelope,
    ).model_dump(mode="python")
