from __future__ import annotations

import re
from typing import Any

from backend.legacy.orphaned_services.aix_ayrpack_authority_service import resolve_lifecycle_authority
from backend.legacy.orphaned_services.aix_command_identity_service import coerce_command, coerce_command_list, command_hashes
from backend.services.devexecutor_mutation_tool_taxonomy_service import (
    ALL_MUTATION_TOOLS,
    LIVE_ORCHESTRATION_MUTATION_TOOLS,
    ORCHESTRATION_FALLBACK_MUTATION_TOOLS,
    ORCHESTRATION_REMOVED_MUTATION_TOOLS,
)

TEMPORAL_AUDIT_AUTHORITY_SOURCE = "temporal.state_machine.history"


def _normalize_text(value: Any) -> str | None:
    if hasattr(value, "value"):
        value = value.value
    text = str(value or "").strip()
    return text or None


def _normalize_contract_ref_text(value: Any) -> str | None:
    text = _normalize_text(value)
    if not text:
        return None
    workhub_match = re.search(r"(workhub://tasks/\d+/ticket_contract_current(?:@[A-Za-z0-9_.-]+)?)", text)
    if workhub_match:
        return workhub_match.group(1)
    local_match = re.search(r"(local://task_ticket_contract_current(?:@[A-Za-z0-9_.-]+)?)", text)
    if local_match:
        return local_match.group(1)
    return text


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in list(value or []) if str(item).strip()]


def _execution_proof_validation_commands(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    proof_bundle = _as_dict(evidence.get("execution_proof_bundle"))
    commands: list[dict[str, Any]] = []
    for item in list(proof_bundle.get("validation_results") or []):
        if not isinstance(item, dict):
            continue
        try:
            exit_code = int(item.get("exit_code") or 0)
        except (TypeError, ValueError):
            continue
        if exit_code != 0:
            continue
        command = coerce_command(item)
        if command:
            commands.append(command)
    return commands


def _durable_phase_entries(runtime_trace: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in list(runtime_trace.get("durable_phases") or []):
        if not isinstance(item, dict):
            continue
        phase_name = _normalize_text(item.get("phase_name"))
        status = _normalize_text(item.get("status"))
        if not phase_name or not status:
            continue
        entries.append(
            {
                "phase_name": phase_name,
                "attempt": item.get("attempt"),
                "status": status,
                "reason_code": _normalize_text(item.get("reason_code")),
                "ref": _normalize_text(item.get("ref")),
                "task_latest_ref": _normalize_text(item.get("task_latest_ref")),
                "invocation_latest_ref": _normalize_text(item.get("invocation_latest_ref")),
            }
        )
    return entries


def _phase_summary(runtime_trace: dict[str, Any]) -> dict[str, Any]:
    entries = _durable_phase_entries(runtime_trace)
    latest = entries[-1] if entries else {}
    failed = next(
        (item for item in reversed(entries) if item.get("status") in {"failed", "timed_out", "cancelled"}), {}
    )
    return {
        "source_classification": "projection",
        "phase_count": len(entries),
        "current_phase": _normalize_text(latest.get("phase_name")),
        "current_status": _normalize_text(latest.get("status")),
        "current_attempt": latest.get("attempt"),
        "failed_phase": _normalize_text(failed.get("phase_name")),
        "failed_reason_code": _normalize_text(failed.get("reason_code")),
        "completed_phases": [
            str(item.get("phase_name")).strip()
            for item in entries
            if str(item.get("status") or "").strip() == "completed"
        ],
        "entries": entries,
    }


def _packet_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    task_packet = _as_dict(payload.get("task_packet"))
    metadata = _as_dict(task_packet.get("metadata"))
    packet = metadata.get("ayrpack")
    return dict(packet) if isinstance(packet, dict) else {}


def _spine(packet: dict[str, Any]) -> list[dict[str, Any]]:
    raw_entries = packet.get("audit_events") or packet.get("spine") or []
    return [dict(item) for item in raw_entries if isinstance(item, dict)]


def _tool_sequence(evidence: dict[str, Any]) -> list[str]:
    runtime = _as_dict(evidence.get("runtime"))
    runtime_sequence = _string_list(runtime.get("tool_sequence"))
    if runtime_sequence:
        return runtime_sequence
    sequence: list[str] = []
    for item in list(evidence.get("transcript") or []):
        if not isinstance(item, dict):
            continue
        tool = _normalize_text(item.get("tool"))
        if tool:
            sequence.append(tool)
    return sequence


def _changed_files(evidence: dict[str, Any]) -> list[str]:
    proposal_artifacts = _as_dict(evidence.get("proposal_artifacts"))
    for candidate in (
        proposal_artifacts.get("changed_files"),
        evidence.get("changed_files"),
    ):
        paths = _string_list(candidate)
        if paths:
            return list(dict.fromkeys(path.replace("\\", "/") for path in paths))
    changed: list[str] = []
    for item in list(evidence.get("transcript") or []):
        if not isinstance(item, dict):
            continue
        result = _as_dict(item.get("result"))
        for path in _string_list(result.get("changed_files")):
            normalized = path.replace("\\", "/")
            if normalized not in changed:
                changed.append(normalized)
    return changed


def _mutation_entries(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in list(evidence.get("transcript") or []):
        if not isinstance(item, dict):
            continue
        tool = _normalize_text(item.get("tool"))
        if tool in ALL_MUTATION_TOOLS:
            entries.append(dict(item))
    if entries:
        return entries
    runtime = _as_dict(evidence.get("runtime"))
    for tool in _string_list(runtime.get("tool_sequence")):
        if tool in ALL_MUTATION_TOOLS:
            entries.append({"tool": tool})
    return entries


def _latest_mutation_result(evidence: dict[str, Any]) -> dict[str, Any]:
    for entry in reversed(_mutation_entries(evidence)):
        result = entry.get("result")
        if isinstance(result, dict):
            return dict(result)
    return {}


def _mutation_validator_rejection_code(
    *, latest_mutation_result: dict[str, Any], evidence: dict[str, Any]
) -> str | None:
    code = _normalize_text(latest_mutation_result.get("reason_code")) or _normalize_text(
        latest_mutation_result.get("code")
    )
    if code:
        return code
    terminal_failure = _as_dict(evidence.get("terminal_failure"))
    details = _as_dict(terminal_failure.get("details"))
    return _normalize_text(details.get("reason_code")) or _normalize_text(details.get("code"))


def _mutation_validator_rejection_summary(
    *,
    latest_mutation_result: dict[str, Any],
    evidence: dict[str, Any],
) -> str | None:
    summary = (
        _normalize_text(latest_mutation_result.get("reason_summary"))
        or _normalize_text(latest_mutation_result.get("message"))
        or _normalize_text(latest_mutation_result.get("error"))
    )
    if summary:
        return summary
    terminal_failure = _as_dict(evidence.get("terminal_failure"))
    details = _as_dict(terminal_failure.get("details"))
    return (
        _normalize_text(details.get("message"))
        or _normalize_text(details.get("stderr"))
        or _normalize_text(details.get("stdout"))
    )


def _fallback_entries(packet: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in _spine(packet):
        kind = str(getattr(item.get("kind"), "value", item.get("kind")) or "").strip().lower()
        summary = _normalize_text(item.get("summary"))
        if kind != "decision" or summary != "fallback edit primitive used":
            continue
        details = _as_dict(item.get("details"))
        entries.append(
            {
                "timestamp_utc": _normalize_text(item.get("timestamp_utc")),
                "primitive": _normalize_text(details.get("primitive")) or _normalize_text(item.get("title")),
                "reason": _normalize_text(details.get("reason")),
                "scope": _normalize_text(details.get("scope")),
            }
        )
    return entries


def _latest_failed_command(evidence: dict[str, Any]) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    for item in list(evidence.get("commands") or []):
        if isinstance(item, dict):
            commands.append(dict(item))
    tests = _as_dict(evidence.get("tests"))
    for item in list(tests.get("commands") or []):
        if isinstance(item, dict):
            commands.append(dict(item))
    for item in reversed(commands):
        ok = item.get("ok")
        returncode = item.get("returncode")
        if ok is False or (returncode is not None and int(returncode or 0) != 0):
            return item
    return {}


def _tail_text(value: Any, *, limit: int = 400) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[-limit:]


def build_execution_digest_view(
    payload: dict[str, Any],
    *,
    run_ledger_ref: str | None = None,
    run_ledger_latest_ref: str | None = None,
) -> dict[str, Any]:
    result = _as_dict(payload.get("result"))
    evidence = _as_dict(result.get("evidence"))
    quality_gate = _as_dict(payload.get("quality_gate"))
    control_plane = _as_dict(payload.get("control_plane_outcome"))
    runtime_trace = _as_dict(payload.get("runtime_trace"))
    packet = _packet_from_payload(payload)
    frame = _as_dict(packet.get("active_frame"))
    lifecycle_resolution = resolve_lifecycle_authority(
        packet,
        control_plane=control_plane,
        final_status=payload.get("final_status") or result.get("status"),
    )
    lifecycle_authoritative = bool(lifecycle_resolution.get("authoritative"))
    authoritative_frame = frame if lifecycle_authoritative else {}
    packet_contract_ref = _as_dict(packet.get("contract_ref"))
    frame_contract_ref = _as_dict(authoritative_frame.get("contract_ref"))
    contract_ref = frame_contract_ref or packet_contract_ref
    touched_files = _changed_files(evidence)
    tool_sequence = _tool_sequence(evidence)
    mutation_entries = _mutation_entries(evidence)
    latest_mutation_result = _latest_mutation_result(evidence)
    fallback_entries = _fallback_entries(packet)
    execution_attestation = _as_dict(authoritative_frame.get("execution_capability_attestation"))
    failed_command = _latest_failed_command(evidence)
    expected_commands = coerce_command_list(_as_dict(payload.get("validation_contract")).get("expected_commands"))
    executed_command_candidates: list[dict[str, Any]] = []
    for item in [dict(item) for item in list(evidence.get("commands") or []) if isinstance(item, dict)]:
        command = coerce_command(item)
        if command:
            executed_command_candidates.append(command)
    executed_command_candidates.extend(_execution_proof_validation_commands(evidence))
    executed_commands = coerce_command_list(executed_command_candidates)
    executed_hashes = command_hashes(executed_commands)
    mutation_tools = [tool for tool in tool_sequence if tool in ALL_MUTATION_TOOLS]
    live_mutation_tools = [tool for tool in mutation_tools if tool in LIVE_ORCHESTRATION_MUTATION_TOOLS]
    observed_fallback_tools = [tool for tool in mutation_tools if tool in ORCHESTRATION_FALLBACK_MUTATION_TOOLS]
    existing_fallback_primitives = {
        _normalize_text(item.get("primitive"))
        for item in fallback_entries
        if isinstance(item, dict) and _normalize_text(item.get("primitive"))
    }
    for tool in observed_fallback_tools:
        if tool in existing_fallback_primitives:
            continue
        fallback_entries.append({"primitive": tool, "reason": "legacy_fallback_tool_observed"})
    legacy_removed_tools_seen = [tool for tool in mutation_tools if tool in set(ORCHESTRATION_REMOVED_MUTATION_TOOLS)]
    current_state = (
        _normalize_text(authoritative_frame.get("status"))
        or _normalize_text(lifecycle_resolution.get("current_state"))
        or _normalize_text(control_plane.get("handoff_status"))
        or _normalize_text(payload.get("final_status"))
        or _normalize_text(result.get("status"))
    )
    lifecycle_source = str(lifecycle_resolution.get("lifecycle_source") or "fallback")
    final_gate = {
        "source_classification": "projection",
        "passed": bool(quality_gate.get("passed")),
        "code": _normalize_text(quality_gate.get("code")),
        "message": _normalize_text(quality_gate.get("message")),
        "reasons": _string_list(quality_gate.get("reasons")),
    }
    reason = {
        "source_classification": "projection",
        "code": _normalize_text(control_plane.get("reason_code"))
        or _normalize_text((result.get("error") or {}).get("code")),
        "summary": _normalize_text(control_plane.get("reason_summary"))
        or _normalize_text((result.get("error") or {}).get("message")),
        "blocked_reason": _normalize_text(control_plane.get("blocked_reason")),
        "failure_category": _normalize_text(control_plane.get("failure_category")),
    }
    precheck = {
        "source_classification": "projection",
        "passed": latest_mutation_result.get("semantic_precheck_passed"),
        "reason_code": (
            _mutation_validator_rejection_code(latest_mutation_result=latest_mutation_result, evidence=evidence)
            if latest_mutation_result.get("semantic_precheck_passed") is False
            else None
        ),
        "output": str(latest_mutation_result.get("semantic_precheck_output") or "").strip() or None,
    }
    postcheck = {
        "source_classification": "projection",
        "passed": latest_mutation_result.get("pyright_passed"),
        "reason_code": (
            _mutation_validator_rejection_code(latest_mutation_result=latest_mutation_result, evidence=evidence)
            if latest_mutation_result.get("pyright_passed") is False
            else None
        ),
        "scope": _normalize_text(latest_mutation_result.get("post_apply_validation_scope")),
        "targets": _string_list(latest_mutation_result.get("post_apply_validation_targets")),
        "results": list(latest_mutation_result.get("post_apply_validation_results") or []),
        "output": str(latest_mutation_result.get("pyright_output") or "").strip() or None,
    }
    lifecycle = {
        "source_classification": "source_of_truth" if lifecycle_authoritative else "projection",
        "audit_authority_source": TEMPORAL_AUDIT_AUTHORITY_SOURCE,
        "audit_authority_classification": "source_of_truth",
        "current_state": current_state,
        "lifecycle_source": lifecycle_source,
        "active_frame_kind": _normalize_text(authoritative_frame.get("kind")),
        "active_frame_phase": _normalize_text(authoritative_frame.get("phase")),
        "active_frame_summary": _normalize_text(authoritative_frame.get("summary")),
        "status_source": "ayrpack.active_frame.status" if lifecycle_authoritative else "control_plane_projection",
    }
    mutation = {
        "source_classification": "projection",
        "tool_sequence_classification": "audit",
        "chosen_mutation_path": " -> ".join(live_mutation_tools) or None,
        "primary_mutation_tool": live_mutation_tools[0] if live_mutation_tools else None,
        "tool_sequence": tool_sequence,
        "legacy_removed_tools_seen": legacy_removed_tools_seen,
        "fallback_occurred": bool(fallback_entries),
        "fallback_entries": fallback_entries,
        "touched_files": touched_files,
        "mutation_attempt_count": len(mutation_entries),
        "validator_rejection_code": _mutation_validator_rejection_code(
            latest_mutation_result=latest_mutation_result,
            evidence=evidence,
        ),
        "validator_rejection_summary": _mutation_validator_rejection_summary(
            latest_mutation_result=latest_mutation_result,
            evidence=evidence,
        ),
    }
    attestation = {
        "source_classification": (
            "source_of_truth" if execution_attestation and lifecycle_authoritative else "projection"
        ),
        "passed": execution_attestation.get("passed"),
        "reason_code": _normalize_text(execution_attestation.get("reason_code")),
        "reason_summary": _normalize_text(execution_attestation.get("reason_summary")),
        "failed_checks": [
            dict(item) for item in list(execution_attestation.get("failed_checks") or []) if isinstance(item, dict)
        ],
        "checks": _as_dict(execution_attestation.get("checks")),
    }
    task = {
        "source_classification": "projection",
        "task_id": _normalize_text(payload.get("task_id")) or _normalize_text(result.get("task_id")),
        "task_number": int(payload.get("task_number") or result.get("task_number") or 0) or None,
        "project_id": _normalize_text(payload.get("project_id")) or _normalize_text(result.get("project_id")),
        "contract_version": _normalize_text(contract_ref.get("version")),
        "contract_ref": _normalize_contract_ref_text(contract_ref.get("ref")),
        "contract_summary": _normalize_text(contract_ref.get("summary")),
    }
    digest = {
        "schema_version": "aix.execution_digest.current",
        "digest_classification": "projection",
        "audit_authority_source": TEMPORAL_AUDIT_AUTHORITY_SOURCE,
        "audit_authority_classification": "source_of_truth",
        "artifact_projections": {
            "run_ledger": "projection",
            "opsbus": "projection",
        },
        "authoritative_sources": {
            "lifecycle": "ayrpack.active_frame.status",
            "audit": TEMPORAL_AUDIT_AUTHORITY_SOURCE,
            "contract_ref": "ayrpack.contract_ref",
            "attestation": "ayrpack.active_frame.execution_capability_attestation",
            "mutation": "result.evidence.transcript + result.evidence.proposal_artifacts",
            "final_gate": "quality_gate",
        },
        "task": task,
        "lifecycle": lifecycle,
        "mutation": mutation,
        "attestation": attestation,
        "precheck": precheck,
        "postcheck": postcheck,
        "final_gate": final_gate,
        "outcome": {
            "source_classification": "projection",
            "final_status": _normalize_text(payload.get("final_status")),
            "handoff_status": _normalize_text(control_plane.get("handoff_status")),
            "reason": reason,
        },
        "summary": reason["summary"]
        or reason["code"]
        or _normalize_text(payload.get("final_status"))
        or "run recorded",
        "current_stage": _normalize_text(control_plane.get("phase"))
        or lifecycle["active_frame_phase"]
        or _normalize_text(result.get("status"))
        or "execution",
        "final_status": _normalize_text(payload.get("final_status")),
        "execution_state": {
            "source_classification": "projection",
            "audit_authority_source": TEMPORAL_AUDIT_AUTHORITY_SOURCE,
            "audit_authority_classification": "source_of_truth",
            "state": current_state,
            "lifecycle_source": lifecycle_source,
            "source_status": (
                current_state
                if lifecycle_authoritative
                else (_normalize_text(control_plane.get("status")) or _normalize_text(payload.get("final_status")))
            ),
            "source_substate": None if lifecycle_authoritative else _normalize_text(control_plane.get("substate")),
            "source_phase": (
                lifecycle["active_frame_phase"]
                if lifecycle_authoritative
                else (_normalize_text(control_plane.get("phase")) or lifecycle["active_frame_phase"])
            ),
            "reason_code": reason["code"],
        },
        "handoff_status": _normalize_text(control_plane.get("handoff_status")),
        "reason_code": reason["code"],
        "reason_summary": reason["summary"],
        "failure_category": reason["failure_category"],
        "failing_tool": _normalize_text((result.get("error") or {}).get("tool"))
        or _normalize_text(_as_dict(evidence.get("terminal_failure")).get("details", {}).get("tool"))
        or (mutation_tools[-1] if mutation_tools else None),
        "failing_command": _normalize_text(failed_command.get("command_text") or failed_command.get("command")),
        "stderr_tail": _tail_text(failed_command.get("stderr"))
        or _tail_text(failed_command.get("stdout"))
        or _tail_text(_as_dict(_as_dict(evidence.get("terminal_failure")).get("details")).get("stderr"))
        or _tail_text(_as_dict(_as_dict(evidence.get("terminal_failure")).get("details")).get("stdout")),
        "runtime": {
            "runtime_mode": _normalize_text(_as_dict(failed_command.get("runtime")).get("mode"))
            or _normalize_text(_as_dict(evidence.get("runtime")).get("mode")),
            "runtime_image": _normalize_text(_as_dict(failed_command.get("runtime")).get("image"))
            or _normalize_text(_as_dict(evidence.get("runtime")).get("image")),
            "container_required": bool(
                _as_dict(failed_command.get("runtime")).get("container_required")
                if _as_dict(failed_command.get("runtime"))
                else _as_dict(evidence.get("runtime")).get("container_required")
            ),
            "tool_sequence": tool_sequence,
            "stage_current": _as_dict(runtime_trace.get("stage_current")),
            "stage_events": [
                dict(item) for item in list(runtime_trace.get("stage_events") or []) if isinstance(item, dict)
            ],
            "phase_summary": _phase_summary(runtime_trace),
        },
        "validation": {
            "expected_commands": expected_commands,
            "executed_commands": executed_commands,
            "missing_commands": [
                item for item in expected_commands if str(item.get("hash") or "") not in executed_hashes
            ],
            "tests_detected": bool(_as_dict(evidence.get("tests")).get("detected")),
            "quality_gate_code": final_gate["code"],
            "quality_gate_passed": final_gate["passed"],
            "touched_files": touched_files,
            "precheck": precheck,
            "postcheck": {
                "passed": postcheck["passed"],
                "scope": postcheck["scope"],
                "targets": postcheck["targets"],
                "results": postcheck["results"],
            },
        },
        "guidance": {
            "remediation": _string_list(control_plane.get("remediation")),
            "next_best_actions": _string_list(control_plane.get("next_best_actions")),
        },
        "refs": {
            "source_classification": "projection",
            "run_ledger_ref": _normalize_text(run_ledger_ref),
            "run_ledger_latest_ref": _normalize_text(run_ledger_latest_ref),
            "transcript_ref": _normalize_text(evidence.get("transcript_ref")),
            "tests_ref": _normalize_text(evidence.get("tests_ref")),
            "logs_ref": _normalize_text(evidence.get("logs_ref")),
        },
        "changed_files": touched_files,
    }
    return digest


__all__ = ["build_execution_digest_view"]
