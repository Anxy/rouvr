from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict

from backend.hyeve_aix_service.config import settings
from backend.services.aix_execution_packet_validator import (
    AixExecutionPacketValidationError,
    validate_aix_execution_packet,
)
from backend.services.aix_run_idempotency_service import aix_run_idempotency_store
from backend.services.devexecutor_command_runner_service import cleanup_managed_command_runtime_artifacts
from backend.services.devexecutor_compact_carrier_service import (
    CompactCarrierTranslationError,
    CompactCarrierValidationError,
    resolve_compact_or_steps,
)
from backend.services.devexecutor_mutation_runtime_service import cleanup_managed_mutation_runtime_artifacts
from backend.services.devexecutor_runtime_registry_service import create_run, execute_run

DEFAULT_PROFILES_DIR = str(Path(__file__).resolve().parent.parent / "config" / "repo_profiles")

_AIX_LOGGER = logging.getLogger("hyeve_aix_service.aix_v2_runtime")
_BOUNDARY_LOGGER = structlog.get_logger("hyeve_aix_service.aix_v2_runtime")
ENVELOPE_FORMAT_INVALID = "ENVELOPE_FORMAT_INVALID"


class RunError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingress: str
    schema_id: str | None
    executable: bool
    status: str
    run_id: str | None
    task_id: str | None
    task_number: int | None
    project_id: str | None
    correlation_id: str | None
    packet_id: str | None
    replayed: bool
    evidence_refs: list[str]
    evidence: dict[str, Any]
    telemetry: dict[str, Any]
    ignored_reason: str | None = None
    error: RunError | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def execute_enveloped_run(
    payload: dict[str, Any],
    *,
    ingress: str,
    auth_ctx: dict[str, Any] | None = None,
    heartbeat_hook: Any = None,
) -> RunResult:
    """
    Canonical envelope-driven execution pipeline.

    Expected shape:
    - payload["envelope"] => CloudEvents v1 envelope.
    - payload["envelope"]["data"]["packet"] => canonical AIX execution packet payload.
    - payload["envelope"]["data"]["execution"] => runtime directives:
      workspace_ref.workspace_root | workspace_root, task_number, repo_key,
      explicit_profile_id, steps | compact_carrier, max_runtime_seconds,
      max_patch_attempts.
    """
    packet, execution, envelope_rejection = _extract_runtime_payload(payload, ingress=ingress)
    if envelope_rejection is not None:
        return envelope_rejection

    try:
        schema_id = validate_aix_execution_packet(packet, locus=ingress)
    except AixExecutionPacketValidationError as exc:
        return RunResult(
            ingress=ingress,
            schema_id=exc.schema_name,
            executable=False,
            status="failed",
            run_id=None,
            task_id=None,
            task_number=None,
            project_id=None,
            correlation_id=None,
            packet_id=None,
            replayed=False,
            evidence_refs=[],
            evidence={},
            telemetry={},
            error=RunError(code=exc.code, message=exc.message, retryable=exc.retryable, details=exc.to_dict()),
        )

    project_id = str(packet.get("project_id") or "").strip() or None
    correlation_id = str(packet.get("correlation_id") or "").strip() or None
    packet_id = str(packet.get("packet_id") or "").strip() or None

    workspace_root = _resolve_workspace_root(execution)
    workspace = Path(workspace_root).resolve()
    if not workspace.exists():
        return RunResult(
            ingress=ingress,
            schema_id=schema_id,
            executable=True,
            status="failed",
            run_id=None,
            task_id=None,
            task_number=_resolve_task_number(execution, packet),
            project_id=project_id,
            correlation_id=correlation_id,
            packet_id=packet_id,
            replayed=False,
            evidence_refs=[],
            evidence={},
            telemetry=_base_telemetry(packet=packet, execution=execution),
            error=RunError(
                code="workspace_missing",
                message=f"workspace_root does not exist: {workspace}",
                retryable=False,
            ),
        )

    steps = execution.get("steps")
    compact_carrier = execution.get("compact_carrier")
    try:
        resolved_steps, translator_diag = resolve_compact_or_steps(
            steps=steps if isinstance(steps, list) else [],
            compact_carrier=compact_carrier if isinstance(compact_carrier, dict) else None,
        )
    except (CompactCarrierValidationError, CompactCarrierTranslationError) as exc:
        return RunResult(
            ingress=ingress,
            schema_id=schema_id,
            executable=True,
            status="failed",
            run_id=None,
            task_id=None,
            task_number=_resolve_task_number(execution, packet),
            project_id=project_id,
            correlation_id=correlation_id,
            packet_id=packet_id,
            replayed=False,
            evidence_refs=[],
            evidence={},
            telemetry=_base_telemetry(packet=packet, execution=execution),
            error=RunError(
                code="compact_translation_failed",
                message=str(exc),
                retryable=False,
            ),
        )

    # Keep execution deterministic when contract-only execution packet is provided.
    if not resolved_steps:
        resolved_steps = [{"tool": "get_status", "args": {}}]
    if heartbeat_hook is not None:
        try:
            heartbeat_hook(
                {
                    "stage": "slice_validation_complete",
                    "summary": f"validated execution slice with {len(resolved_steps)} steps",
                    "details": {
                        "step_count": len(resolved_steps),
                        "translator_input_mode": str((translator_diag or {}).get("input_mode") or "").strip() or None,
                    },
                }
            )
        except Exception:
            pass

    tenant_id = str(packet.get("tenant_id") or "default")
    idempotency_key = _resolve_idempotency_key(execution, packet)
    if idempotency_key:
        inserted, existing = aix_run_idempotency_store.claim(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            project_id=project_id,
            task_id=str(execution.get("task_id") or "") or None,
        )
        if (not inserted) and existing.status in {"completed", "done"} and isinstance(existing.result_payload, dict):
            result_payload = cast(dict[str, Any], existing.result_payload)
            restored_payload = dict(result_payload)
            restored_payload["ingress"] = str(restored_payload.get("ingress") or ingress)
            restored_payload["schema_id"] = str(restored_payload.get("schema_id") or schema_id or "") or None
            restored_payload["project_id"] = str(restored_payload.get("project_id") or project_id or "") or None
            restored_payload["correlation_id"] = (
                str(restored_payload.get("correlation_id") or correlation_id or "") or None
            )
            restored_payload["packet_id"] = str(restored_payload.get("packet_id") or packet_id or "") or None
            restored_payload["replayed"] = True
            restored_payload["evidence_refs"] = list(restored_payload.get("evidence_refs") or [])
            restored_payload["evidence"] = _as_dict(restored_payload.get("evidence"))
            restored_payload["telemetry"] = _as_dict(restored_payload.get("telemetry"))
            return RunResult.model_validate(restored_payload)

    command_runtime = _merge_runtime_config(
        settings.devexecutor_command_runtime,
        execution.get("command_runtime") if isinstance(execution.get("command_runtime"), dict) else None,
    )
    mutation_runtime = _merge_runtime_config(
        settings.devexecutor_mutation_runtime,
        execution.get("mutation_runtime") if isinstance(execution.get("mutation_runtime"), dict) else None,
    )
    try:
        run = create_run(
            project_id=project_id or "unknown",
            task_number=_resolve_task_number(execution, packet),
            correlation_id=correlation_id or "unknown",
            idempotency_key=idempotency_key,
            workspace_root=str(workspace),
            repo_key=str(execution.get("repo_key") or "hyeve-aix-service"),
            profile_id=(
                str(execution.get("explicit_profile_id"))
                if execution.get("explicit_profile_id") is not None
                and str(execution.get("explicit_profile_id")).strip()
                else None
            ),
            steps=resolved_steps,
            token_budget=(
                int(execution["token_budget"])
                if execution.get("token_budget") is not None and str(execution.get("token_budget")).strip()
                else None
            ),
            resume_run_id=(
                str(execution.get("resume_run_id"))
                if execution.get("resume_run_id") is not None and str(execution.get("resume_run_id")).strip()
                else None
            ),
        )
        import json as _json
        import sys as _sys

        print(
            _json.dumps(
                {
                    "event": "aix_boundary",
                    "layer": "executor",
                    "code": "EXECUTION_STARTED",
                    "decision": "started",
                    "input_summary": {
                        "run_id": run.run_id,
                        "step_count": len(resolved_steps),
                        "workspace_root": str(workspace),
                        "task_number": _resolve_task_number(execution, packet),
                    },
                }
            ),
            flush=True,
            file=_sys.stdout,
        )
        finished = execute_run(
            run_id=run.run_id,
            profiles_dir=str(execution.get("profiles_dir") or DEFAULT_PROFILES_DIR),
            max_runtime_seconds=float(execution.get("max_runtime_seconds") or 120.0),
            max_patch_attempts=int(execution.get("max_patch_attempts") or 10),
            command_runtime=command_runtime,
            mutation_runtime=mutation_runtime,
            heartbeat_hook=heartbeat_hook,
        )

        evidence = _collect_evidence(
            workspace_root=workspace,
            transcript=finished.transcript,
            diagnostics=finished.diagnostics,
            translator=translator_diag,
        )
        terminal_error = _infer_terminal_run_error(transcript=finished.transcript)
        if terminal_error is not None:
            evidence["terminal_failure"] = terminal_error.to_dict()
        telemetry = _base_telemetry(packet=packet, execution=execution)
        telemetry["runtime"] = _runtime_telemetry(transcript=finished.transcript)
        output = RunResult(
            ingress=ingress,
            schema_id=schema_id,
            executable=True,
            status=finished.status,
            run_id=finished.run_id,
            task_id=str(execution.get("task_id") or "") or None,
            task_number=finished.task_number,
            project_id=finished.project_id,
            correlation_id=finished.correlation_id,
            packet_id=packet_id,
            replayed=bool(finished.replayed),
            evidence_refs=list(evidence.get("refs") or []),
            evidence=evidence,
            telemetry=telemetry,
            error=terminal_error,
        )
        if idempotency_key:
            aix_run_idempotency_store.mark_completed(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                project_id=output.project_id,
                run_id=output.run_id,
                task_id=output.task_id,
                status=output.status,
                result_payload=output.to_dict(),
            )
        return output
    finally:
        cleanup_managed_command_runtime_artifacts(runtime=command_runtime)
        cleanup_managed_mutation_runtime_artifacts(runtime=mutation_runtime)


def _infer_terminal_run_error(*, transcript: list[dict[str, Any]]) -> RunError | None:
    if not transcript:
        return None
    last = transcript[-1] if isinstance(transcript[-1], dict) else {}
    if bool(last.get("ok", True)):
        return None
    tool = str(last.get("tool") or "unknown")
    error_message = str(last.get("error") or "").strip()
    if not error_message:
        error_message = f"tool step failed: {tool}"
    index_value = last.get("index")
    details: dict[str, Any] = {"tool": tool}
    if index_value is not None:
        details["index"] = index_value
    result = _as_dict(last.get("result"))
    result_code = str(result.get("code") or "").strip()
    for key in (
        "returncode",
        "timed_out",
        "stderr",
        "stdout",
        "command",
        "path",
        "relative_path",
        "message",
        "reason_code",
        "workspace_root",
        "dirty_paths",
        "search_text",
        "block_index",
    ):
        if result.get(key) is not None:
            details[key] = result.get(key)
    for key in ("budget", "used", "step_failed"):
        if result.get(key) is not None:
            details[key] = result.get(key)
    return RunError(
        code=result_code or "executor_terminal_step_failed",
        message=error_message,
        retryable=False,
        details=details,
    )


def _resolve_workspace_root(execution: dict[str, Any]) -> str:
    workspace_ref = _as_dict(execution.get("workspace_ref"))
    root = str(workspace_ref.get("workspace_root") or "").strip()
    if root:
        return root
    root = str(execution.get("workspace_root") or "").strip()
    if root:
        return root
    return str(settings.RUNTIME_POOL_DEFAULT_WORKSPACE_ROOT)


def _resolve_idempotency_key(execution: dict[str, Any], packet: dict[str, Any]) -> str | None:
    from_exec = str(execution.get("idempotency_key") or "").strip()
    if from_exec:
        return from_exec
    from_packet = str(packet.get("idempotency_key") or "").strip()
    return from_packet or None


def _resolve_task_number(execution: dict[str, Any], packet: dict[str, Any]) -> int:
    raw: Any = execution.get("task_number")
    if raw is None:
        raw = _as_dict(packet.get("step")).get("index")
    try:
        return max(1, int(raw))
    except Exception:
        return 1


def _validation_trace_parsed_result(result: dict[str, Any]) -> str | None:
    contract_validation = _as_dict(result.get("contract_validation"))
    attempts = list(contract_validation.get("validation_attempts") or [])
    for item in reversed(attempts):
        if not isinstance(item, dict):
            continue
        parsed_result = str(item.get("parser_result") or "").strip()
        if parsed_result:
            return parsed_result
    if str(contract_validation.get("status") or "").strip():
        return str(contract_validation.get("status") or "").strip()
    return None


def _validation_trace_allowlist_value(result: dict[str, Any], field: str) -> str | None:
    contract_validation = _as_dict(result.get("contract_validation"))
    direct_value = str(contract_validation.get(field) or "").strip()
    if direct_value:
        return direct_value
    attempts = list(contract_validation.get("validation_attempts") or [])
    for item in reversed(attempts):
        if not isinstance(item, dict):
            continue
        value = str(item.get(field) or "").strip()
        if value:
            return value
    return None


def _validation_trace_classification_mapping(result: dict[str, Any], *, ok: bool) -> dict[str, Any] | None:
    contract_validation = _as_dict(result.get("contract_validation"))
    if not contract_validation:
        return None
    explicit_mapping = _as_dict(contract_validation.get("classification_mapping"))
    if explicit_mapping:
        return {
            "classification": str(explicit_mapping.get("classification") or "").strip() or None,
            "reason_code": str(explicit_mapping.get("reason_code") or "").strip() or None,
            "contract_status": str(contract_validation.get("status") or "").strip() or None,
        }
    classification = "success" if ok else "VERIFICATION_FAIL"
    reason_code = "VALIDATION_PASSED" if ok else "VALIDATION_FAILED"
    return {
        "classification": classification,
        "reason_code": reason_code,
        "contract_status": str(contract_validation.get("status") or "").strip() or None,
    }


def _collect_evidence(
    *,
    workspace_root: Path,
    transcript: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    translator: dict[str, Any],
) -> dict[str, Any]:
    changed_files: list[str] = []
    command_outputs: list[dict[str, Any]] = []
    test_commands: list[dict[str, Any]] = []
    grounded_symbols: list[dict[str, Any]] = []
    for item in transcript:
        result = _as_dict(item.get("result"))
        files = result.get("changed_files")
        if isinstance(files, list):
            for entry in files:
                val = str(entry).strip()
                if val:
                    changed_files.append(val)
        grounded = _as_dict(result.get("grounded_symbol"))
        if grounded:
            grounded_symbols.append(grounded)
        if str(item.get("tool") or "") == "cmd.run":
            command = result.get("command")
            command_text = " ".join(str(x) for x in command) if isinstance(command, list) else str(command or "")
            exit_code = result.get("returncode")
            parsed_result = _validation_trace_parsed_result(result)
            classification_mapping = _validation_trace_classification_mapping(result, ok=bool(item.get("ok")))
            entry = {
                "command": command,
                "command_text": command_text,
                "returncode": result.get("returncode"),
                "exit_code": exit_code,
                "stdout": result.get("stdout"),
                "stderr": result.get("stderr"),
                "ok": bool(item.get("ok")),
                "parsed_result": parsed_result,
                "classification_mapping": classification_mapping,
                "allowlist_compliance": _validation_trace_allowlist_value(result, "allowlist_compliance"),
                "allowlist_source": _validation_trace_allowlist_value(result, "allowlist_source"),
            }
            command_outputs.append(entry)
            if "pytest" in command_text or "unittest" in command_text:
                test_commands.append(entry)

    uniq_files = sorted(set(changed_files))
    transcript_ref = f"local://devexecutor/runs/{len(transcript)}-steps"
    changed_files_ref = f"local://workspace/{workspace_root.as_posix()}/changed_files" if uniq_files else None
    diff_ref = f"local://workspace/{workspace_root.as_posix()}/diff.patch" if uniq_files else None
    logs_ref = f"local://devexecutor/runs/{len(transcript)}-steps/commands.log" if command_outputs else None
    tests_ref = f"local://devexecutor/runs/{len(transcript)}-steps/tests.json" if test_commands else None
    refs = [ref for ref in [transcript_ref, changed_files_ref, diff_ref, logs_ref, tests_ref] if ref]
    return {
        "contract_version": "evidence_bundle.current",
        "transcript_ref": transcript_ref,
        "changed_files_ref": changed_files_ref,
        "diff_ref": diff_ref,
        "logs_ref": logs_ref,
        "tests_ref": tests_ref,
        "refs": refs,
        "transcript_count": len(transcript),
        "changed_files": uniq_files,
        "grounded_symbols": grounded_symbols,
        "commands": command_outputs,
        "tests": {
            "commands": test_commands,
            "detected": bool(test_commands),
        },
        "runtime": _runtime_telemetry(transcript=transcript),
        "diagnostics": diagnostics,
        "translator": translator,
    }


def _base_telemetry(*, packet: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    sender = _as_dict(packet.get("sender"))
    exec_telemetry = _as_dict(execution.get("telemetry"))
    usage = {
        "latency_ms": exec_telemetry.get("latency_ms"),
        "tokens_in": exec_telemetry.get("tokens_in"),
        "tokens_out": exec_telemetry.get("tokens_out"),
        "tokens_total": exec_telemetry.get("tokens_total"),
        "cost_usd": exec_telemetry.get("cost_usd"),
    }
    return {
        "model": {
            "agent_id": sender.get("agent_id"),
            "role": sender.get("role"),
            "provider": sender.get("provider"),
            "model": sender.get("model"),
        },
        "usage": usage,
    }


def _runtime_telemetry(*, transcript: list[dict[str, Any]]) -> dict[str, Any]:
    elapsed_ms = 0
    tool_sequence: list[str] = []
    for item in transcript:
        diagnostics = _as_dict(item.get("diagnostics"))
        elapsed_ms += int(diagnostics.get("elapsed_ms") or 0)
        tool = str(item.get("tool") or "").strip()
        if tool:
            tool_sequence.append(tool)
    return {
        "step_count": len(transcript),
        "elapsed_ms": elapsed_ms,
        "tool_sequence": tool_sequence,
    }


def _merge_runtime_config(
    base_runtime: dict[str, Any],
    override_runtime: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(base_runtime or {})
    for key, value in dict(override_runtime or {}).items():
        if key == "env_overrides":
            env = dict(merged.get("env_overrides") or {})
            env.update(dict(value or {}))
            merged["env_overrides"] = env
            continue
        merged[key] = value
    return merged


def _as_dict(value: Any, *, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    if fallback is not None:
        return fallback
    return {}


def _extract_runtime_payload(
    payload: dict[str, Any], *, ingress: str
) -> tuple[dict[str, Any], dict[str, Any], RunResult | None]:
    envelope = _as_dict(payload.get("envelope"))
    if not envelope:
        return (
            {},
            {},
            _build_envelope_rejection(
                payload=payload,
                received_format=_infer_received_format(payload),
                ingress=ingress,
            ),
        )
    specversion = str(envelope.get("specversion") or "").strip()
    data = _as_dict(envelope.get("data"))
    if specversion != "1.0" or not data:
        return (
            {},
            {},
            _build_envelope_rejection(
                payload=payload,
                received_format="cloudevents_invalid",
                ingress=ingress,
            ),
        )
    packet = _as_dict(data.get("packet"))
    execution = _as_dict(data.get("execution"))
    if not packet or not execution:
        return (
            {},
            {},
            _build_envelope_rejection(
                payload=payload,
                received_format="cloudevents_invalid",
                ingress=ingress,
            ),
        )
    return packet, execution, None


def _infer_received_format(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("packet"), dict) or isinstance(payload.get("execution"), dict):
        return "ayrpack_current"
    return "unknown"


def _build_envelope_rejection(*, payload: dict[str, Any], received_format: str, ingress: str) -> RunResult:
    legacy_packet = _as_dict(payload.get("packet"))
    legacy_execution = _as_dict(payload.get("execution"))
    envelope = _as_dict(payload.get("envelope"))
    envelope_data = _as_dict(envelope.get("data"))
    packet = _as_dict(envelope_data.get("packet"), fallback=legacy_packet)
    execution = _as_dict(envelope_data.get("execution"), fallback=legacy_execution)
    task_id = str(execution.get("task_id") or "").strip() or None
    task_number = _resolve_task_number(execution, packet)
    project_id = str(packet.get("project_id") or execution.get("project_id") or "").strip() or None
    packet_id = str(packet.get("packet_id") or "").strip() or None
    trace_id = (
        str(envelope.get("trace_id") or "").strip()
        or str(envelope.get("id") or "").strip()
        or str(packet.get("correlation_id") or "").strip()
        or None
    )
    rejected_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    details = {
        "rejection_code": ENVELOPE_FORMAT_INVALID,
        "expected_format": "cloudevents_current",
        "received_format": received_format,
        "task_id": task_id,
        "trace_id": trace_id,
        "rejected_at_utc": rejected_at_utc,
    }
    _emit_boundary_rejection_event(details=details)
    return RunResult(
        ingress=ingress,
        schema_id=None,
        executable=False,
        status="failed",
        run_id=None,
        task_id=task_id,
        task_number=task_number,
        project_id=project_id,
        correlation_id=trace_id,
        packet_id=packet_id,
        replayed=False,
        evidence_refs=[],
        evidence={},
        telemetry={},
        error=RunError(
            code=ENVELOPE_FORMAT_INVALID,
            message="CloudEvents v1 envelope required",
            retryable=False,
            details=details,
        ),
    )


def _emit_boundary_rejection_event(*, details: dict[str, Any]) -> None:
    import sys as _sys

    event = {
        "specversion": "1.0",
        "type": "com.aix.task.boundary.rejected",
        "source": "aix.enveloped_run",
        "id": f"evt-{uuid4().hex[:24]}",
        "time": details.get("rejected_at_utc"),
        "datacontenttype": "application/json",
        "data": dict(details),
    }
    print(json.dumps(event), flush=True, file=_sys.stdout)


__all__ = ["RunError", "RunResult", "execute_enveloped_run"]
