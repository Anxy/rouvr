from __future__ import annotations

import json
from typing import Any

FINAL_BUCKETS = {
    "recon_blocked",
    "planning_blocked",
    "execution_failed",
    "verification_failed",
    "writeback_failed",
    "success",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_size_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
    except Exception:
        return 0


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


def _packet_stage_snapshot(*, stage: str, authority_source: str, packet: dict[str, Any]) -> dict[str, Any]:
    field_paths = sorted(_field_paths(packet))
    return {
        "stage": stage,
        "authority_source": authority_source,
        "present": bool(packet),
        "size_bytes": _json_size_bytes(packet),
        "root_keys": sorted(str(key).strip() for key in packet.keys() if str(key).strip()),
        "field_count": len(field_paths),
        "field_paths": field_paths,
    }


def _planner_packet_metrics(packet_payload: dict[str, Any] | None) -> dict[str, Any]:
    packet = dict(packet_payload or {})
    constraints = [str(item).strip() for item in packet.get("constraints") or [] if str(item).strip()]
    return {
        "strict_packet_present": bool(packet),
        "target_file_count": len([str(item).strip() for item in packet.get("target_files") or [] if str(item).strip()])
        or (1 if str(packet.get("target_file") or "").strip() else 0),
        "target_symbol_count": len(
            [str(item).strip() for item in packet.get("symbol_targets") or [] if str(item).strip()]
        )
        or (1 if str(packet.get("target_symbol") or "").strip() else 0),
        "validation_command_count": 1 if str(packet.get("validation_command") or "").strip() else 0,
        "bounded_edit_surface_count": len(
            [str(item).strip() for item in packet.get("bounded_edit_surface") or [] if str(item).strip()]
        ),
        "allowed_mutation_tool_count": len(
            [
                str(item).strip()
                for item in (packet.get("tool_policy") or {}).get("allowed_mutation_tools") or []
                if str(item).strip()
            ]
        ),
        "stop_condition_count": len(
            [item for item in packet.get("stop_conditions") or [] if isinstance(item, dict) and item.get("code")]
        ),
        "acceptance_criteria_count": len(
            [str(item).strip() for item in packet.get("acceptance_criteria") or [] if str(item).strip()]
        ),
        "constraint_count": len(constraints),
        "codeintel_anchor_present": isinstance(packet.get("codeintel_anchor"), dict),
        "size_bytes": _json_size_bytes(packet),
    }


def _artifact_names(state: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in list(state.get("artifacts") or []):
        name = str(_as_dict(item).get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _artifact_refs(state: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for item in list(state.get("artifacts") or []):
        ref = str(_as_dict(item).get("ref") or "").strip()
        if ref:
            refs.append(ref)
    execution = _as_dict(state.get("execution"))
    for ref in list(execution.get("diff_refs") or []):
        text = str(ref or "").strip()
        if text and text not in refs:
            refs.append(text)
    return refs


def _append_artifact_gap(artifact_gaps: list[dict[str, str]], artifact: str, reason: str) -> None:
    for existing in artifact_gaps:
        if existing.get("artifact") == artifact:
            return
    artifact_gaps.append({"artifact": artifact, "reason": reason})


def _detect_execution_artifact_gaps(
    artifact_names: list[str],
    execution: dict[str, Any],
) -> list[dict[str, str]]:
    artifact_name_set = {name.strip() for name in artifact_names if str(name or "").strip()}
    artifact_gaps: list[dict[str, str]] = []
    if not execution:
        _append_artifact_gap(artifact_gaps, "execution_summary", "execution did not run")
        _append_artifact_gap(artifact_gaps, "diff_refs", "execution did not run")
        return artifact_gaps
    if "execution_summary" not in artifact_name_set:
        _append_artifact_gap(artifact_gaps, "execution_summary", "execution ran but summary artifact was not persisted")
    if list(execution.get("changed_files") or []) and "changed_files" not in artifact_name_set:
        _append_artifact_gap(
            artifact_gaps, "changed_files", "execution changed files but changed_files artifact was not persisted"
        )
    if list(execution.get("diff_refs") or []) and "diff_refs" not in artifact_name_set:
        _append_artifact_gap(
            artifact_gaps, "diff_refs", "execution produced diff refs but diff_refs artifact was not persisted"
        )
    return artifact_gaps


def _detect_core_artifact_gaps(state: dict[str, Any], artifact_names: list[str]) -> list[dict[str, str]]:
    artifact_name_set = {name.strip() for name in artifact_names if str(name or "").strip()}
    gaps: list[dict[str, str]] = []
    required = ("recon", "planner_input_summary", "planner_raw", "normalized_plan", "verifier_result", "writeback")
    for artifact in required:
        if artifact not in artifact_name_set:
            _append_artifact_gap(gaps, artifact, f"{artifact} artifact missing")
    return gaps


def _detect_snapshot_drift(state: dict[str, Any]) -> list[dict[str, str]]:
    metadata = _as_dict(_as_dict(state.get("task_payload")).get("metadata_json"))
    readiness_state = str(metadata.get("readiness_state") or "").strip().lower()
    verification = _as_dict(state.get("verification"))
    if readiness_state and readiness_state in {"needs_enrichment", "clarification_required"}:
        if str(verification.get("classification") or "") == "success":
            return [
                {
                    "artifact": "ticket_contract_snapshot",
                    "reason": "stored readiness snapshot contradicts final success",
                }
            ]
    return []


def _latest_remediation_control(state: dict[str, Any]) -> dict[str, Any] | None:
    artifacts = list(state.get("artifacts") or [])
    for item in reversed(artifacts):
        artifact = _as_dict(item)
        if str(artifact.get("name") or "").strip() == "remediation_control":
            return _as_dict(artifact.get("payload"))
    events = list(_as_dict(state.get("validator_ledger")).get("events") or [])
    for item in reversed(events):
        event = _as_dict(item)
        if str(event.get("event") or "").strip() == "remediation_control_decision":
            return {
                "phase": str(event.get("phase") or "").strip() or None,
                "reason_code": str(event.get("reason_code") or "").strip() or None,
                "defect_class": str(event.get("defect_class") or "").strip() or None,
                "fingerprint_key": str(event.get("fingerprint_key") or "").strip() or None,
                "effective_action": str(event.get("effective_action") or "").strip() or None,
                "effective_reason_code": str(event.get("effective_reason_code") or "").strip() or None,
                "existing_blocker": {
                    "task_number": int(event.get("attached_task_number") or 0) or None,
                }
                if event.get("attached_task_number")
                else None,
            }
    return None


def classify_phase_failure(state: dict[str, Any]) -> str | None:
    failure = _as_dict(state.get("failure"))
    if str(failure.get("classification") or "").strip() == "blocked_needs_human":
        return "blocked_needs_human"
    phase = str(failure.get("phase") or "").strip().lower()
    plan = _as_dict(state.get("plan"))
    if phase == "recon":
        return "recon_failed"
    if phase == "planner":
        return "plan_invalid" if list(plan.get("validation_failures") or []) else "planning_failed"
    if phase == "execution":
        return "execution_failed"
    if phase == "verification":
        return "verification_failed"
    if phase == "writeback":
        return "writeback_failed"
    return None


def build_packet_audit(state: dict[str, Any]) -> dict[str, Any]:
    recon = _as_dict(state.get("recon"))
    execution = _as_dict(state.get("execution"))
    envelope = _as_dict(execution.get("envelope"))
    envelope_data = _as_dict(_as_dict(envelope.get("envelope")).get("data"))
    dispatch_packet = _as_dict(_as_dict(envelope_data.get("execution")).get("slim_packet"))
    execute_packet = _as_dict(_as_dict(_as_dict(envelope_data.get("execution")).get("telemetry")).get("slim_packet"))
    create_packet = _as_dict(recon.get("slim_packet_inputs"))
    enrich_packet = _as_dict(_as_dict(state.get("packet")).get("slim_packet"))
    planner_packet = _as_dict(_as_dict(state.get("plan")).get("planner_packet"))
    stages = [
        _packet_stage_snapshot(
            stage="create",
            authority_source="recon.slim_packet_inputs",
            packet=create_packet,
        ),
        _packet_stage_snapshot(
            stage="enrich",
            authority_source="state.packet.slim_packet",
            packet=enrich_packet,
        ),
        _packet_stage_snapshot(
            stage="dispatch",
            authority_source="execution.envelope.data.execution.slim_packet",
            packet=dispatch_packet,
        ),
        _packet_stage_snapshot(
            stage="execute",
            authority_source="execution.envelope.data.execution.telemetry.slim_packet",
            packet=execute_packet,
        ),
    ]
    transitions: list[dict[str, Any]] = []
    for previous, current in zip(stages, stages[1:]):
        previous_fields = set(previous.get("field_paths") or [])
        current_fields = set(current.get("field_paths") or [])
        transitions.append(
            {
                "from_stage": previous["stage"],
                "to_stage": current["stage"],
                "removed_fields": sorted(previous_fields - current_fields),
                "added_fields": sorted(current_fields - previous_fields),
            }
        )
    duplicate_sections: list[dict[str, str]] = []
    if dispatch_packet and execute_packet and dispatch_packet == execute_packet:
        duplicate_sections.append(
            {
                "path": "execution.telemetry.slim_packet",
                "matches": "execution.slim_packet",
                "reason": "runtime telemetry duplicates dispatched slim packet",
            }
        )
    return {
        "stages": stages,
        "transitions": transitions,
        "duplicate_sections": duplicate_sections,
        "planner_packet_metrics": _planner_packet_metrics(planner_packet),
        "runtime_authority": {
            "create_matches_enrich": bool(create_packet) and create_packet == enrich_packet,
            "create_matches_dispatch": bool(create_packet) and create_packet == dispatch_packet,
            "dispatch_matches_execute": bool(dispatch_packet) and dispatch_packet == execute_packet,
            "live_slim_packet_used": bool(dispatch_packet) and dispatch_packet == execute_packet,
            "authority_sources": [
                "execution.envelope.data.execution.slim_packet",
                "execution.envelope.data.execution.telemetry.slim_packet",
            ],
            "contradictory_state": bool(dispatch_packet and execute_packet and dispatch_packet != execute_packet),
        },
    }


def classify_run_bucket(state: dict[str, Any]) -> str:
    # These are reporting buckets for summaries and ledgers, not raw state.failure.classification values.
    failure = _as_dict(state.get("failure"))
    execution = _as_dict(state.get("execution"))
    verification = _as_dict(state.get("verification"))
    writeback = _as_dict(state.get("writeback"))
    if (
        str(state.get("status") or "").strip() == "writeback_complete"
        or str(verification.get("classification") or "") == "success"
    ):
        return "success"
    failure_phase = str(failure.get("phase") or "").strip().lower()
    if failure_phase == "recon":
        return "recon_blocked"
    if failure_phase == "planner" or (str(state.get("status") or "").strip() == "blocked" and not execution):
        return "planning_blocked"
    if failure_phase == "execution":
        return "execution_failed"
    if failure_phase == "verification":
        return "verification_failed"
    if failure_phase == "writeback" or any(
        str(writeback.get(key) or "").strip()
        for key in ("verification_error", "transition_error", "evidence_error", "closeout_error")
    ):
        return "writeback_failed"
    return "planning_blocked" if str(verification.get("classification") or "") == "blocked" else "verification_failed"


def build_run_summary(state: dict[str, Any]) -> dict[str, Any]:
    execution = _as_dict(state.get("execution"))
    verification = _as_dict(state.get("verification"))
    failure = _as_dict(state.get("failure"))
    final_phase = str(state.get("current_phase") or failure.get("phase") or "").strip() or "unknown"
    key_reason = (
        str(failure.get("message") or "").strip()
        or str(verification.get("summary") or "").strip()
        or str(_as_dict(state.get("plan")).get("blocked_reason") or "").strip()
        or None
    )
    artifact_names = _artifact_names(state)
    artifact_gaps = _detect_core_artifact_gaps(state, artifact_names)
    artifact_gaps.extend(_detect_execution_artifact_gaps(artifact_names=artifact_names, execution=execution))
    artifact_gaps.extend(_detect_snapshot_drift(state))
    packet_audit = build_packet_audit(state)
    return {
        "task_id": state.get("task_id"),
        "run_id": state.get("run_id"),
        "classification": classify_run_bucket(state),
        "phase_failure_classification": classify_phase_failure(state),
        "final_phase": final_phase,
        "key_reason": key_reason,
        "changed_files": list(execution.get("changed_files") or []),
        "commands_run": [
            str(_as_dict(item).get("command_text") or _as_dict(item).get("command") or "").strip()
            for item in list(execution.get("commands") or [])
            if str(_as_dict(item).get("command_text") or _as_dict(item).get("command") or "").strip()
        ],
        "artifact_names": artifact_names,
        "artifact_refs": _artifact_refs(state),
        "artifact_gaps": artifact_gaps,
        "packet_audit": packet_audit,
        "remediation_control": _latest_remediation_control(state),
    }
