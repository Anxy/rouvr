from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.legacy.orphaned_services.aix_execution_digest_service import build_execution_digest_view
from backend.services.devexecutor_mutation_tool_taxonomy_service import ALL_MUTATION_TOOLS
from backend.services.devexecutor_runlog_resolver_service import get_run_ledger_root, get_runlog_root
from backend.legacy.orphaned_services.outcome_policy_registry_service import resolve_recovery_policy
from hyeve_core.task_lifecycle_registry import is_review_pending_status, normalize_task_status


def _resolve_runlog_root() -> Path:
    return get_runlog_root()


RUN_LEDGER_ROOT = get_run_ledger_root()
MEMORY_CAPSULE_ROOT = RUN_LEDGER_ROOT / "memory_capsules"
CONTROLLED_EVIDENCE_REF_TYPES = {
    "diff",
    "test_run",
    "execution_trace",
    "planner_trace",
    "workspace_status",
    "artifact_manifest",
}
RETRY_ENVELOPE_CODES = {"RETRY_BUDGET_EXHAUSTED"}

_RUNNER_NOOP_STATUS_MAP = {
    "NO_QUEUED_TASKS": "no_runnable_task",
    "TASK_NOT_QUEUED": "not_queued",
    "TASK_NOT_FOUND": "not_found",
    "TASK_RESOLUTION_FAILED": "resolution_failed",
    "TASK_ID_MISSING": "task_id_missing",
    "CLAIM_FAILED": "claim_failed",
}

TEMPORAL_AUDIT_AUTHORITY_SOURCE = "temporal.state_machine.history"
logger = logging.getLogger(__name__)


def _runtime_authority_markers() -> dict[str, Any]:
    return {
        "source_classification": "projection",
        "audit_authority_source": TEMPORAL_AUDIT_AUTHORITY_SOURCE,
        "audit_authority_classification": "source_of_truth",
    }


def _artifact_projection_markers() -> dict[str, Any]:
    return {
        "run_ledger_classification": "projection",
        "opsbus_classification": "projection",
    }


def _normalize_codeintel_query_identity(raw: Any) -> dict[str, Any]:
    payload = dict(raw or {}) if isinstance(raw, dict) else {}
    return {
        "entity_query": _normalize_text(payload.get("entity_query")),
        "query_fingerprint": _normalize_text(payload.get("query_fingerprint")),
    }


def _normalize_codeintel_citations(raw: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in list(raw or []):
        if not isinstance(item, dict):
            continue
        start_line = item.get("start_line")
        if start_line is None:
            start_line = item.get("line")
        end_line = item.get("end_line")
        if end_line is None:
            end_line = start_line
        normalized.append(
            {
                "repo_id": _normalize_text(item.get("repo_id")),
                "file_path": _normalize_text(item.get("file_path")) or _normalize_text(item.get("file")),
                "start_line": _safe_int(start_line),
                "end_line": _safe_int(end_line),
                "snapshot_id": _normalize_text(item.get("snapshot_id")) or _normalize_text(item.get("snapshot")),
                "commit_sha": _normalize_text(item.get("commit_sha")),
                "confidence": _normalize_float(item.get("confidence")),
            }
        )
    return normalized


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    temp_path.replace(path)


def _slugify_capsule_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


def _normalize_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _normalize_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _normalize_controlled_ref_type(value: Any) -> str:
    text = _normalize_text(value) or "artifact_manifest"
    return text if text in CONTROLLED_EVIDENCE_REF_TYPES else "artifact_manifest"


def _runner_noop_summary(reason: Any, *, current_status: Any = None, target_task_number: Any = None) -> str | None:
    code = _normalize_text(reason)
    normalized = str(code or "").upper()
    if not normalized:
        return None
    if normalized == "NO_QUEUED_TASKS":
        return "no queued tasks available"
    if normalized == "TASK_NOT_QUEUED":
        suffix = f" (current status: {current_status})" if _normalize_text(current_status) else ""
        return f"target task is not queued{suffix}"
    if normalized == "TASK_NOT_FOUND":
        suffix = f" (task #{target_task_number})" if target_task_number is not None else ""
        return f"target task not found{suffix}"
    if normalized == "TASK_RESOLUTION_FAILED":
        return "task resolution failed"
    if normalized == "TASK_ID_MISSING":
        return "resolved task is missing id"
    if normalized == "CLAIM_FAILED":
        return "runner could not claim task"
    return normalized.lower().replace("_", " ")


def _sha1_fingerprint(*parts: Any) -> str:
    payload = ":".join(str(part or "").strip() for part in parts)
    return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()


def _truncate_capsule_text(value: Any, *, limit: int = 200) -> str | None:
    text = _normalize_text(value)
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _build_runtime_memory_capsule(record: dict[str, Any]) -> dict[str, Any]:
    task_id = _normalize_text(record.get("task_id")) or "unknown-task"
    task_number = _safe_int(record.get("task_number")) or None
    project_id = _normalize_text(record.get("project_id"))
    recorded_at = _normalize_text(record.get("recorded_at_utc")) or datetime.now(UTC).isoformat()
    ticket_contract = dict(record.get("ticket_contract_current") or {})
    pack_contract = dict(record.get("pack_contract") or {})
    execution_slice = dict(ticket_contract.get("execution_slice") or {})
    target_surface = dict(ticket_contract.get("target_surface") or {})
    validation_commands = [
        str(item).strip()
        for item in list(execution_slice.get("validation_commands") or record.get("validation_commands") or [])
        if str(item).strip()
    ]
    target_files = [
        str(item).strip()
        for item in list(execution_slice.get("target_files") or target_surface.get("target_files") or [])
        if str(item).strip()
    ]
    prompt_symbol_targets = [
        dict(item)
        for item in list(
            target_surface.get("prompt_symbol_targets") or (record.get("ai_operator") or {}).get("symbol_targets") or []
        )
        if isinstance(item, dict)
    ]
    causal_fields: list[str] = []
    for item in prompt_symbol_targets:
        symbol = _normalize_text(item.get("symbol"))
        if symbol and symbol not in causal_fields:
            causal_fields.append(symbol)
    if not causal_fields:
        for item in list(ticket_contract.get("acceptance_criteria") or record.get("acceptance_criteria") or []):
            text = _normalize_text(item)
            if text and text not in causal_fields:
                causal_fields.append(text)
            if len(causal_fields) >= 2:
                break
    stop_conditions = [
        _normalize_text(item.get("summary") if isinstance(item, dict) else item)
        for item in list(ticket_contract.get("stop_conditions") or [])
    ]
    stop_conditions = [item for item in stop_conditions if item]
    constraints = [
        _normalize_text(item) for item in list(ticket_contract.get("constraints") or []) if _normalize_text(item)
    ]
    hard_invariants = [
        _normalize_text(item.get("summary") if isinstance(item, dict) else item)
        for item in list(pack_contract.get("hard_invariants") or [])
    ]
    hard_invariants = [item for item in hard_invariants if item]
    active_guardrails = list(dict.fromkeys([*stop_conditions, *constraints, *hard_invariants]))
    persona_name = _compact_actor(record)
    tool_perms = [
        str(item).strip()
        for item in list(
            ((record.get("planner") or {}).get("planner_packet_summary") or {}).get("allowed_mutation_tools") or []
        )
        if str(item).strip()
    ]
    if not tool_perms:
        tool_perms = [
            str(item).strip()
            for item in list(((pack_contract.get("tool_policy") or {}).get("allowed_mutation_tools")) or [])
            if str(item).strip()
        ]
    diff_ref = _normalize_text((record.get("refs") or {}).get("proposal_diff_ref"))
    local_trace_ref = _normalize_text((record.get("planner") or {}).get("local_trace_ref"))
    evidence_anchors = [item for item in [diff_ref, local_trace_ref, *target_files] if item]
    failure_reason_code = (
        _normalize_text((record.get("control_plane_outcome") or {}).get("reason_code"))
        or _normalize_text((record.get("quality_gate") or {}).get("code"))
        or _normalize_text((record.get("failure") or {}).get("code"))
    )
    changed_files = [
        str(item).strip()
        for item in list(((record.get("execution_digest") or {}).get("changed_files")) or [])
        if str(item).strip()
    ]
    diff_summary = (
        _truncate_capsule_text(
            ", ".join(changed_files)
            if changed_files
            else diff_ref or _normalize_text((record.get("failure") or {}).get("summary"))
        )
        or "no proposal diff recorded"
    )
    causal_focus = (
        causal_fields[0]
        if causal_fields
        else (_normalize_text(ticket_contract.get("objective")) or "the declared recovery slice")
    )
    semantic_grounding = (
        f"This slice must repair {causal_focus} using causal contract logic rather than non-causal fixture-only fields."
    )
    return {
        "capsule_id": f"capsule-{recorded_at}-{task_id}",
        "workhub_task": {
            "task_id": task_id,
            "contract_summary": {
                "intent": _normalize_text(record.get("task_intent")),
                "target_files": target_files,
                "critical_contracts": validation_commands[:2],
                "causal_fields": causal_fields[:4],
            },
        },
        "run_ledger_facts": {
            "slice_id": _normalize_text(
                ((record.get("planner") or {}).get("planner_packet_summary") or {}).get("slice_id")
            )
            or _normalize_text(execution_slice.get("slice_id")),
            "previous_slice_outcome": "success"
            if _normalize_text(record.get("final_status")) == "done"
            else (
                "blocked"
                if _normalize_text(record.get("final_status")) in {"blocked", "waiting_on_human", "needs_review"}
                else (
                    "terminal_failed"
                    if _normalize_text(record.get("final_status")) in {"failed", "error"}
                    else "failed"
                )
            ),
            "failure_reason_code": failure_reason_code,
            "evidence_anchors": evidence_anchors[:6],
            "accumulated_diffs_summary": diff_summary,
        },
        "persona_snapshot": {
            "name": persona_name,
            "tool_perms": tool_perms[:8],
            "output_contract": _normalize_text((record.get("control_plane_outcome") or {}).get("handoff_status"))
            or _normalize_text((record.get("control_plane_outcome") or {}).get("reason_summary"))
            or "bounded slice result",
        },
        "active_guardrails": active_guardrails[:8],
        "semantic_grounding": semantic_grounding,
        "_capsule_index": {
            "task_number": task_number,
            "project_id": project_id,
            "recorded_at_utc": recorded_at,
        },
    }


def _persist_runtime_memory_capsule(record: dict[str, Any], *, root: Path | None = None) -> dict[str, Any] | None:
    capsule = _build_runtime_memory_capsule(record)
    task_id = _normalize_text((capsule.get("workhub_task") or {}).get("task_id"))
    if not task_id:
        return None
    out_root = Path(root) if root is not None else MEMORY_CAPSULE_ROOT
    out_root.mkdir(parents=True, exist_ok=True)
    task_number = _safe_int((capsule.get("_capsule_index") or {}).get("task_number"))
    project_id = _normalize_text((capsule.get("_capsule_index") or {}).get("project_id"))
    recorded_at = _slugify_capsule_key((capsule.get("_capsule_index") or {}).get("recorded_at_utc"))
    capsule_path = out_root / f"{_slugify_capsule_key(task_id)}-{recorded_at}.json"
    latest_task_path = out_root / f"latest-task-{_slugify_capsule_key(task_id)}.json"
    _atomic_write_json(capsule_path, capsule)
    _atomic_write_json(latest_task_path, capsule)
    if project_id and task_number:
        latest_task_number_path = (
            out_root / f"latest-project-{_slugify_capsule_key(project_id)}-task-{task_number}.json"
        )
        _atomic_write_json(latest_task_number_path, capsule)
    logger.info("Capsule generated for task %s", task_id)
    return {"capsule": capsule, "ref": str(capsule_path), "latest_ref": str(latest_task_path)}


def get_latest_runtime_memory_capsule(
    *,
    task_id: str | None = None,
    project_id: str | None = None,
    task_number: int | None = None,
    root: Path | None = None,
) -> dict[str, Any] | None:
    out_root = Path(root) if root is not None else MEMORY_CAPSULE_ROOT
    if not out_root.exists():
        return None
    candidate_paths: list[Path] = []
    if task_id:
        candidate_paths.append(out_root / f"latest-task-{_slugify_capsule_key(task_id)}.json")
    if project_id and task_number:
        candidate_paths.append(
            out_root / f"latest-project-{_slugify_capsule_key(project_id)}-task-{int(task_number)}.json"
        )
    for candidate in candidate_paths:
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict) and payload:
            return payload
    return None


def _target_symbols_from_record(record: dict[str, Any]) -> list[str]:
    validation_contract = dict((record.get("ai_operator") or {}).get("validation_contract") or {})
    symbol_targets = []
    for item in list((record.get("ai_operator") or {}).get("symbol_targets") or []):
        if not isinstance(item, dict):
            continue
        symbol = _normalize_text(item.get("symbol"))
        if symbol and symbol not in symbol_targets:
            symbol_targets.append(symbol)
    for item in list(validation_contract.get("prompt_symbol_targets") or []):
        if not isinstance(item, dict):
            continue
        symbol = _normalize_text(item.get("symbol"))
        if symbol and symbol not in symbol_targets:
            symbol_targets.append(symbol)
    return symbol_targets


def _constraint_projection(record: dict[str, Any]) -> dict[str, Any]:
    validation_contract = dict((record.get("ai_operator") or {}).get("validation_contract") or {})
    raw_guardrails = dict(record.get("guardrails") or {}) if isinstance(record.get("guardrails"), dict) else {}
    forbidden_patterns = [
        str(item).strip()
        for item in list(raw_guardrails.get("forbidden_patterns") or record.get("forbidden_patterns") or [])
        if str(item).strip()
    ]
    dependency_rules = _normalize_text(raw_guardrails.get("dependency_rules")) or _normalize_text(
        record.get("dependency_rules")
    )
    mutation_bounds = _normalize_text(raw_guardrails.get("mutation_bounds")) or (
        "files_in_target_surface_only" if validation_contract.get("code_change_required") else "read_only"
    )
    approval_required = bool(raw_guardrails.get("approval_required") or record.get("approval_required"))
    return {
        "forbidden_patterns": forbidden_patterns,
        "dependency_rules": dependency_rules,
        "mutation_bounds": mutation_bounds,
        "approval_required": approval_required,
    }


def _compact_actor(record: dict[str, Any]) -> str:
    failure = dict(record.get("failure") or {})
    tool_class = _normalize_text(failure.get("tool_class"))
    if tool_class == "planner":
        return "planner"
    if tool_class == "verifier":
        return "verifier"
    if tool_class in {"executor", "runtime"}:
        return "runner"
    return "runner"


def _attestation_attester_role_for_record(record: dict[str, Any]) -> str:
    quality_gate = dict(record.get("quality_gate") or {})
    final_status = _normalize_text(record.get("final_status")) or _normalize_text(
        (record.get("control_plane_outcome") or {}).get("status")
    )
    if final_status in {"blocked", "waiting_on_human"}:
        return "runner"
    if bool(quality_gate.get("passed")) or final_status == "done":
        return "verifier"
    return _compact_actor(record)


def _compact_stage(record: dict[str, Any]) -> str:
    failure = dict(record.get("failure") or {})
    stage = _normalize_text(failure.get("stage")) or "execution"
    if stage in {"planning", "execution", "verification", "quality_gate"}:
        return stage
    if stage == "completed":
        return "verification"
    return "execution"


def _compact_failure_category(record: dict[str, Any]) -> str | None:
    control_plane = dict(record.get("control_plane_outcome") or {})
    explicit_contract_category = _normalize_text(control_plane.get("failure_category"))
    if explicit_contract_category:
        return explicit_contract_category
    explicit = _normalize_text(control_plane.get("reason_code"))
    failure = dict(record.get("failure") or {})
    category = _normalize_text(failure.get("category"))
    if category in {"success", None}:
        return None
    mapping = {
        "diagnostic_blocked": "bad_decomposition",
        "quality_gate_rejected": "validation_failed",
        "planner_failure": "bad_decomposition",
        "execution_failure": "execution_failed",
        "writeback_failure": "execution_failed",
    }
    if explicit in {"TASK_CLARIFICATION_REQUIRED", "TASK_DECOMPOSITION_REQUIRED"}:
        return "missing_input"
    return mapping.get(category or "", "execution_failed")


def build_execution_state_projection(record: dict[str, Any]) -> dict[str, Any]:
    control_plane = dict(record.get("control_plane_outcome") or {})
    transition_record = dict(record.get("transition_record") or {})
    temporal = dict(record.get("temporal") or {})
    state_machine = dict(temporal.get("state_machine") or {})
    temporal_state = _normalize_text(state_machine.get("current_state")) or _normalize_text(state_machine.get("state"))
    source_status = (
        _normalize_text(control_plane.get("status"))
        or _normalize_text(transition_record.get("new_state"))
        or _normalize_text(record.get("final_status"))
        or temporal_state
    )
    source_substate = _normalize_text(control_plane.get("substate"))
    source_phase = _normalize_text(control_plane.get("phase")) or _normalize_text(transition_record.get("stage"))
    reason_code = _normalize_text(control_plane.get("reason_code")) or _normalize_text(
        (record.get("failure") or {}).get("code")
    )
    normalized_source_status = normalize_task_status(source_status)

    if normalized_source_status == "done":
        state = "done"
    elif normalized_source_status in {"blocked", "escalated", "failed"}:
        state = "failed"
    elif normalized_source_status == "waiting_on_human":
        if source_substate in {"clarification_required", "decomposition_required"} or reason_code in {
            "TASK_CLARIFICATION_REQUIRED",
            "TASK_DECOMPOSITION_REQUIRED",
        }:
            state = "awaiting_clarification"
        else:
            state = "awaiting_human"
    elif is_review_pending_status(normalized_source_status):
        state = "awaiting_human"
    elif source_phase in {"quality_gate", "verification", "pre_writeback"} or temporal_state == "verifying":
        state = "validating"
    elif source_phase == "planning" or normalized_source_status == "planning" or temporal_state == "planning":
        state = "planning"
    elif (
        source_phase in {"execution", "executing"}
        or normalized_source_status == "running"
        or temporal_state == "running"
    ):
        state = "executing"
    else:
        state = "executing"

    return {
        "schema_version": "aix.execution_state.current",
        **_runtime_authority_markers(),
        "state": state,
        "source_status": normalized_source_status,
        "source_substate": source_substate,
        "source_phase": source_phase,
        "source_temporal_state": temporal_state,
        "reason_code": reason_code,
    }


def _control_plane_outcome(record: dict[str, Any]) -> dict[str, Any]:
    canonical = dict(record.get("control_plane_outcome") or {})
    attestation_authorization = dict(record.get("attestation_authorization") or {})
    unauthorized = attestation_authorization.get("authorized") is False
    execution_state = build_execution_state_projection(record)
    if canonical:
        outcome = {
            "status": _normalize_text(canonical.get("status")),
            "substate": _normalize_text(canonical.get("substate")),
            "phase": _normalize_text(canonical.get("phase")),
            "blocked_reason": _normalize_text(canonical.get("blocked_reason")),
            "reason_code": _normalize_text(canonical.get("reason_code")),
            "reason_summary": _normalize_text(canonical.get("reason_summary")),
            "handoff_status": _normalize_text(canonical.get("handoff_status"))
            or _normalize_text(canonical.get("status")),
            "failure_category": _normalize_text(canonical.get("failure_category")),
            "blocked_at": _normalize_text(canonical.get("blocked_at")),
            "remediation": [
                _normalize_text(item) for item in list(canonical.get("remediation") or []) if _normalize_text(item)
            ],
            "next_best_actions": [
                _normalize_text(item)
                for item in list(canonical.get("next_best_actions") or [])
                if _normalize_text(item)
            ],
            "execution_state": dict(canonical.get("execution_state") or execution_state),
        }
        if unauthorized:
            outcome["reason_code"] = _normalize_text(attestation_authorization.get("reason_code")) or outcome.get(
                "reason_code"
            )
            outcome["reason_summary"] = _normalize_text(attestation_authorization.get("reason_summary")) or outcome.get(
                "reason_summary"
            )
            outcome["failure_category"] = "validation_failed"
        return outcome
    failure = dict(record.get("failure") or {})
    reason_code = (
        _normalize_text(attestation_authorization.get("reason_code"))
        if unauthorized
        else _normalize_text(failure.get("code"))
    )
    reason_summary = (
        _normalize_text(attestation_authorization.get("reason_summary"))
        if unauthorized
        else _normalize_text(failure.get("summary"))
    )
    handoff_status = _normalize_text(record.get("final_status"))
    failure_category = "validation_failed" if unauthorized else _compact_failure_category(record)
    return {
        "status": _normalize_text(record.get("final_status")),
        "substate": _normalize_text(record.get("final_status")),
        "phase": None,
        "blocked_reason": None,
        "reason_code": reason_code,
        "reason_summary": reason_summary,
        "handoff_status": handoff_status,
        "failure_category": failure_category,
        "blocked_at": _normalize_text(record.get("blocked_at")),
        "remediation": [
            _normalize_text(item) for item in list(record.get("remediation") or []) if _normalize_text(item)
        ],
        "next_best_actions": [
            _normalize_text(item) for item in list(record.get("next_best_actions") or []) if _normalize_text(item)
        ],
        "execution_state": execution_state,
    }


def _metadata_from_task_packet(record_or_payload: dict[str, Any]) -> dict[str, Any]:
    task_packet = dict(record_or_payload.get("task_packet") or {})
    return dict(task_packet.get("metadata") or {})


def _repo_key_for_run_outcome(record_or_payload: dict[str, Any]) -> str | None:
    metadata = _metadata_from_task_packet(record_or_payload)
    repo_key = _normalize_text(metadata.get("repo_key"))
    if repo_key:
        return repo_key
    repo_profile = dict(record_or_payload.get("repo_profile") or {})
    profile_id = _normalize_text(repo_profile.get("profile_id")) or ""
    if "CODEINTEL" in profile_id:
        return "hyeve-codeintel-service"
    if "AIX" in profile_id or "FASTAPI" in profile_id:
        return "hyeve-aix-service"
    return None


def _task_family_tags_for_run_outcome(record_or_payload: dict[str, Any]) -> list[str]:
    metadata = _metadata_from_task_packet(record_or_payload)
    tags: list[str] = []
    for value in (
        metadata.get("slice_focus"),
        metadata.get("change_intent"),
        metadata.get("roadmap_phase"),
        record_or_payload.get("task_intent"),
    ):
        text = _normalize_text(value)
        if text and text not in tags:
            tags.append(text)
    return tags


def _merged_repair_loop(record_or_payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(record_or_payload.get("result") or {})
    evidence = dict(result.get("evidence") or {})
    return {
        **dict(record_or_payload.get("repair_loop") or {}),
        **dict(evidence.get("repair_loop") or {}),
    }


def _failure_lineage_envelope(record_or_payload: dict[str, Any]) -> dict[str, Any]:
    repair_loop = _merged_repair_loop(record_or_payload)
    control_plane = dict(record_or_payload.get("control_plane_outcome") or {})
    quality_gate = dict(record_or_payload.get("quality_gate") or {})
    diagnostic = dict(record_or_payload.get("diagnostic_outcome") or {})
    result = dict(record_or_payload.get("result") or {})
    error = dict(result.get("error") or {})
    classified_failure = classify_run_failure(record_or_payload)

    envelope_code = _normalize_text(repair_loop.get("failure_code"))
    root_cause_code = next(
        (
            code
            for code in (
                _normalize_text(repair_loop.get("root_cause_code")),
                _normalize_text(control_plane.get("reason_code")),
                _normalize_text(quality_gate.get("code")),
                _normalize_text(diagnostic.get("reason_code")),
                _normalize_text(error.get("code")),
                _normalize_text(classified_failure.get("code")),
            )
            if code and code not in RETRY_ENVELOPE_CODES
        ),
        envelope_code or _normalize_text(classified_failure.get("code")) or "UNKNOWN",
    )
    root_cause_summary = (
        _normalize_text(control_plane.get("reason_summary"))
        or _normalize_text(quality_gate.get("message"))
        or _normalize_text(diagnostic.get("reason_summary"))
        or _normalize_text(error.get("message"))
        or _normalize_text(classified_failure.get("summary"))
        or "run failed"
    )
    root_policy = resolve_recovery_policy(
        code=root_cause_code,
        workflow_stage=_normalize_text(classified_failure.get("stage")) or "execution",
        task_intent=_normalize_text(record_or_payload.get("task_intent")),
        repo_profile_id=_normalize_text((record_or_payload.get("repo_profile") or {}).get("profile_id")),
        provider=_normalize_text((record_or_payload.get("planner") or {}).get("provider")),
        model=_normalize_text((record_or_payload.get("planner") or {}).get("model")),
        category=_normalize_text(classified_failure.get("category")) or None,
        tool_class=_normalize_text(classified_failure.get("tool_class")) or None,
        quality_message=root_cause_summary,
    )
    retry_context = {
        "envelope_code": envelope_code if envelope_code in RETRY_ENVELOPE_CODES else None,
        "envelope_policy_action": _normalize_text(repair_loop.get("policy_action")),
        "budget_key": _normalize_text(repair_loop.get("budget_key")),
        "attempts_used": _safe_int(repair_loop.get("attempts_used")),
        "max_attempts": _safe_int(repair_loop.get("max_attempts")),
        "retryable": bool(repair_loop.get("retryable")),
        "same_turn_repair_active": bool(repair_loop.get("same_turn_repair_active")),
    }
    attempts_used = _safe_int(retry_context.get("attempts_used"))
    max_attempts = _safe_int(retry_context.get("max_attempts"))
    retry_context["attempts_used"] = attempts_used
    retry_context["max_attempts"] = max_attempts
    retry_context["exhausted"] = bool(max_attempts and attempts_used is not None and attempts_used >= max_attempts)
    return {
        "root_cause_code": root_cause_code,
        "root_cause_class": _normalize_text(root_policy.get("outcome_class"))
        or _normalize_text(root_policy.get("category")),
        "root_failure_family": _normalize_text(root_policy.get("failure_family"))
        or _compact_failure_category(record_or_payload),
        "root_policy_action": _normalize_text(root_policy.get("policy_action")),
        "root_summary": root_cause_summary,
        "retry_context": retry_context,
    }


def _preferred_failure_class(failure_lineage: dict[str, Any]) -> str | None:
    root_failure_family = _normalize_text(failure_lineage.get("root_failure_family"))
    root_cause_class = _normalize_text(failure_lineage.get("root_cause_class"))
    if root_failure_family == "grounding_failed" and root_cause_class:
        return root_cause_class
    return root_failure_family or root_cause_class or None


def _repair_projection(*, repair_loop: dict[str, Any], failure_lineage: dict[str, Any]) -> dict[str, Any]:
    retry_context = dict(failure_lineage.get("retry_context") or {})
    uses_envelope_root = bool(_normalize_text(retry_context.get("envelope_code")))
    if uses_envelope_root:
        failure_code = _normalize_text(failure_lineage.get("root_cause_code")) or _normalize_text(
            repair_loop.get("failure_code")
        )
        failure_class = _normalize_text(failure_lineage.get("root_failure_family")) or _normalize_text(
            repair_loop.get("failure_class")
        )
        policy_action = _normalize_text(failure_lineage.get("root_policy_action")) or _normalize_text(
            repair_loop.get("policy_action")
        )
    else:
        failure_code = _normalize_text(repair_loop.get("failure_code")) or _normalize_text(
            failure_lineage.get("root_cause_code")
        )
        failure_class = _normalize_text(repair_loop.get("failure_class")) or _normalize_text(
            failure_lineage.get("root_failure_family")
        )
        policy_action = _normalize_text(repair_loop.get("policy_action")) or _normalize_text(
            failure_lineage.get("root_policy_action")
        )
    return {
        "policy_action": policy_action,
        "failure_code": failure_code,
        "failure_class": failure_class,
        "retryable": bool(repair_loop.get("retryable")),
        "attempts_used": _safe_int(repair_loop.get("attempts_used")),
        "same_turn_repair_active": bool(repair_loop.get("same_turn_repair_active")),
        "retry_context": retry_context,
    }


def _slice_count_for_run_outcome(record_or_payload: dict[str, Any]) -> int:
    feature_slice_plan = [
        item for item in list(record_or_payload.get("feature_slice_plan") or []) if isinstance(item, dict)
    ]
    if feature_slice_plan:
        return len(feature_slice_plan)
    ticket_contract = dict(record_or_payload.get("ticket_contract_current") or {})
    execution_slice = dict(ticket_contract.get("execution_slice") or {})
    if execution_slice:
        return 1
    return 0


def _minimal_run_outcome(record_or_payload: dict[str, Any]) -> dict[str, Any]:
    failure = classify_run_failure(record_or_payload)
    result = dict(record_or_payload.get("result") or {})
    evidence = dict(result.get("evidence") or {})
    repair_loop = _merged_repair_loop(record_or_payload)
    assist_loop = {
        **dict(record_or_payload.get("assist_loop") or {}),
        **dict(evidence.get("assist_loop") or {}),
    }
    failure_lineage = _failure_lineage_envelope(record_or_payload)
    ai_operator = dict(record_or_payload.get("ai_operator") or {})
    task_packet = dict(record_or_payload.get("task_packet") or ai_operator.get("task_packet") or {})
    validation_contract = {
        **dict(record_or_payload.get("validation_contract") or {}),
        **dict(ai_operator.get("validation_contract") or {}),
    }
    evidence_refs = [
        str(item).strip()
        for item in list(ai_operator.get("evidence_refs") or result.get("evidence_refs") or [])
        if str(item).strip()
    ]
    refs = dict(record_or_payload.get("refs") or {})
    for ref_value in (
        refs.get("proposal_diff_ref"),
        refs.get("proposal_changed_files_ref"),
        refs.get("outcome_policy_trace_ref"),
        (record_or_payload.get("planner") or {}).get("local_trace_ref"),
        (record_or_payload.get("planner") or {}).get("trace_packet_id"),
    ):
        ref_text = _normalize_text(ref_value)
        if ref_text and ref_text not in evidence_refs:
            evidence_refs.append(ref_text)
    onlineai_guidance = [item for item in list(assist_loop.get("onlineai_guidance") or []) if isinstance(item, dict)]
    repair_projection = _repair_projection(
        repair_loop=repair_loop,
        failure_lineage=failure_lineage,
    )
    return {
        "schema_version": "aix.minimal_run_outcome.current",
        **_runtime_authority_markers(),
        **_artifact_projection_markers(),
        "status": "success" if bool(record_or_payload.get("ok")) else "failure",
        "final_status": _normalize_text(record_or_payload.get("final_status"))
        or _normalize_text(record_or_payload.get("status")),
        "execution_state": build_execution_state_projection(record_or_payload),
        "failure_class": _preferred_failure_class(failure_lineage)
        or _normalize_text(failure.get("outcome_class"))
        or _normalize_text(failure.get("category")),
        "failure_lineage": failure_lineage,
        "repair_taken": repair_projection,
        "human_correction_applied": any(bool(item.get("applied")) for item in onlineai_guidance),
        "slice_count": _slice_count_for_run_outcome(record_or_payload),
        "evidence_refs": evidence_refs,
        "repo_key": _repo_key_for_run_outcome(record_or_payload),
        "task_family_tags": _task_family_tags_for_run_outcome(record_or_payload),
        "task_number": int(record_or_payload.get("task_number") or task_packet.get("task_number") or 0) or None,
        "run_id": _normalize_text(record_or_payload.get("run_id")) or _normalize_text(result.get("run_id")),
        "correlation_id": _normalize_text(record_or_payload.get("correlation_id"))
        or _normalize_text(result.get("correlation_id")),
        "target_files": [
            str(item).strip()
            for item in list(task_packet.get("target_files") or validation_contract.get("target_files") or [])
            if str(item).strip()
        ],
    }


def _current_temporal_state(record: dict[str, Any]) -> str | None:
    temporal = dict(record.get("temporal") or {})
    state_machine = dict(temporal.get("state_machine") or {})
    return _normalize_text(state_machine.get("current_state")) or _normalize_text(state_machine.get("state"))


def _latest_temporal_event(record: dict[str, Any]) -> dict[str, Any] | None:
    temporal = dict(record.get("temporal") or {})
    state_machine = dict(temporal.get("state_machine") or {})
    history = [
        dict(item)
        for item in list(state_machine.get("history") or state_machine.get("events") or [])
        if isinstance(item, dict)
    ]
    if not history:
        return None
    return history[-1]


def build_transition_record_from_run_ledger_record(
    record: dict[str, Any], *, invocation_id: str | None = None
) -> dict[str, Any]:
    attestation = dict(
        record.get("attestation") or build_attestation_from_run_ledger_record(record, invocation_id=invocation_id)
    )
    latest_event = _latest_temporal_event(record)
    prior_state = None
    new_state = _current_temporal_state(record)
    transition_timestamp = _normalize_text(record.get("recorded_at_utc"))
    if latest_event:
        prior_state = _normalize_text(latest_event.get("from_state")) or _normalize_text(latest_event.get("from"))
        new_state = (
            _normalize_text(latest_event.get("to_state")) or _normalize_text(latest_event.get("to")) or new_state
        )
        transition_timestamp = (
            _normalize_text(latest_event.get("timestamp_utc"))
            or _normalize_text(latest_event.get("at_utc"))
            or transition_timestamp
        )
    if not new_state:
        new_state = (
            _normalize_text((record.get("control_plane_outcome") or {}).get("status"))
            or _normalize_text(record.get("final_status"))
            or _normalize_text(record.get("status"))
        )
    return {
        "transition_id": _sha1_fingerprint(
            "transition",
            _normalize_text(record.get("tenant_id")) or "default",
            _normalize_text(record.get("project_id")),
            int(record.get("task_number") or 0),
            _normalize_text(record.get("run_id")) or _normalize_text(invocation_id),
            _normalize_text(new_state),
            transition_timestamp,
        ),
        "ticket_id": _normalize_text(record.get("task_id")),
        "execution_unit_id": _normalize_text(attestation.get("execution_unit_id"))
        or _normalize_text(record.get("slice_id"))
        or _normalize_text(record.get("task_id")),
        "timestamp_utc": transition_timestamp,
        "prior_state": prior_state,
        "new_state": new_state,
        "attestation_id": _normalize_text(attestation.get("attestation_id")),
        "actor": _compact_actor(record),
        "stage": _compact_stage(record),
        "handoff_status": _control_plane_outcome(record).get("handoff_status"),
        "failure_category": _control_plane_outcome(record).get("failure_category"),
    }


def _compact_temporal_projection(record: dict[str, Any]) -> dict[str, Any]:
    transition = dict(record.get("transition_record") or build_transition_record_from_run_ledger_record(record))
    current_state = _normalize_text(transition.get("new_state")) or _current_temporal_state(record)
    control_plane = _control_plane_outcome(record)
    latest_transition = None
    if current_state:
        latest_transition = {
            "from": _normalize_text(transition.get("prior_state")),
            "to": current_state,
            "reason_code": _normalize_text(control_plane.get("reason_code"))
            or _normalize_text((record.get("failure") or {}).get("code")),
            "timestamp_utc": _normalize_text(transition.get("timestamp_utc"))
            or _normalize_text(record.get("recorded_at_utc")),
        }
    return {
        **_runtime_authority_markers(),
        "current_state": current_state,
        "prior_state": _normalize_text(transition.get("prior_state")),
        "new_state": current_state,
        "transition_id": _normalize_text(transition.get("transition_id")),
        "attestation_id": _normalize_text(transition.get("attestation_id")),
        "latest_transition": latest_transition,
    }


def _tail_text(value: Any, *, limit: int = 400) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[-limit:]


def _command_records_from_evidence(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in list(evidence.get("commands") or []):
        if isinstance(item, dict):
            records.append(dict(item))
    tests = dict(evidence.get("tests") or {})
    for item in list(tests.get("commands") or []):
        if isinstance(item, dict):
            records.append(dict(item))
    return records


def _latest_failed_command(evidence: dict[str, Any]) -> dict[str, Any]:
    commands = _command_records_from_evidence(evidence)
    for item in reversed(commands):
        ok = item.get("ok")
        returncode = item.get("returncode")
        if ok is False or (returncode is not None and int(returncode or 0) != 0):
            return item
    return {}


def _latest_failed_transcript_entry(evidence: dict[str, Any]) -> dict[str, Any]:
    transcript = [dict(item) for item in list(evidence.get("transcript") or []) if isinstance(item, dict)]
    for item in reversed(transcript):
        if bool(item.get("ok", True)) is False:
            return item
    return {}


def _runtime_projection_from_payload(payload: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    planner = dict(payload.get("planner") or {})
    failed_entry = _latest_failed_transcript_entry(evidence)
    failed_result = dict(failed_entry.get("result") or {})
    failed_runtime = dict(failed_result.get("runtime") or {})
    failed_command = _latest_failed_command(evidence)
    command_runtime = dict(failed_command.get("runtime") or {})
    return {
        "planner_model_lane": _normalize_text(planner.get("model_lane")),
        "planner_model_lane_role": _normalize_text(planner.get("model_lane_role")),
        "runtime_mode": _normalize_text(failed_runtime.get("mode")) or _normalize_text(command_runtime.get("mode")),
        "runtime_image": _normalize_text(failed_runtime.get("image")) or _normalize_text(command_runtime.get("image")),
        "container_required": bool(
            failed_runtime.get("container_required") if failed_runtime else command_runtime.get("container_required")
        ),
    }


def build_execution_digest(
    payload: dict[str, Any],
    *,
    run_ledger_ref: str | None = None,
    run_ledger_latest_ref: str | None = None,
) -> dict[str, Any]:
    digest = build_execution_digest_view(
        payload,
        run_ledger_ref=run_ledger_ref,
        run_ledger_latest_ref=run_ledger_latest_ref,
    )
    digest["refs"]["outcome_policy_trace_ref"] = _normalize_text(
        (payload.get("outcome_policy_trace") or {}).get("trace_ref")
    ) or _normalize_text((payload.get("outcome_policy_trace") or {}).get("local_trace_ref"))
    digest["temporal"] = dict(_compact_temporal_projection(payload))
    digest["runtime"] = {
        **dict(digest.get("runtime") or {}),
        **_runtime_projection_from_payload(
            dict(payload or {}), dict((dict(payload.get("result") or {})).get("evidence") or {})
        ),
    }
    recovery_submission = dict(payload.get("recovery_submission") or {})
    assist_loop = dict(payload.get("assist_loop") or {})
    repair_loop = dict(payload.get("repair_loop") or {})
    failure_lineage = _failure_lineage_envelope(payload)
    repair_projection = _repair_projection(
        repair_loop=repair_loop,
        failure_lineage=failure_lineage,
    )
    digest["guidance"] = {
        **dict(digest.get("guidance") or {}),
        "recovery_submission": {
            "submitted": bool(recovery_submission.get("submitted")),
            "task_number": int(recovery_submission.get("task_number") or 0) or None,
            "reason": _normalize_text(recovery_submission.get("reason")),
            "policy_action": _normalize_text(recovery_submission.get("policy_action")),
        },
        "assist": {
            "conversation_state": _normalize_text(assist_loop.get("conversation_state")),
            "awaiting_actor": _normalize_text(assist_loop.get("awaiting_actor")),
            "open_questions": [
                str(item).strip() for item in list(assist_loop.get("open_questions") or []) if str(item).strip()
            ],
        },
        "repair": {
            **repair_projection,
            "max_attempts": _safe_int(repair_loop.get("max_attempts")),
        },
    }
    if not list((digest.get("guidance") or {}).get("next_best_actions") or []):
        digest["guidance"]["next_best_actions"] = [
            _normalize_text(item)
            for item in (
                list(recovery_submission.get("next_best_actions") or [])
                or list((payload.get("failure") or {}).get("next_best_actions") or [])
            )
            if _normalize_text(item)
        ]
    if not _normalize_text(digest.get("summary")):
        control_plane = _control_plane_outcome(payload)
        digest["summary"] = (
            _fast_ledger_status_summary(
                status=_normalize_text(payload.get("final_status")) or _normalize_text(control_plane.get("status")),
                failure=dict(payload.get("failure") or {}),
                reason_code=_normalize_text(control_plane.get("reason_code")),
                reason_summary=_normalize_text(control_plane.get("reason_summary")),
                blocked_reason=_normalize_text(control_plane.get("blocked_reason")),
                attestation_authorization=dict(payload.get("attestation_authorization") or {}),
            )
            or "runner result recorded"
        )
    return digest


def _pack_id_for_record(record: dict[str, Any]) -> str:
    pack_contract = dict(record.get("pack_contract") or {})
    explicit_pack_id = _normalize_text(pack_contract.get("pack_id")) or _normalize_text(
        (record.get("admission") or {}).get("pack_contract", {}).get("pack_id")
    )
    if explicit_pack_id:
        return explicit_pack_id
    task_intent = _normalize_text(record.get("task_intent")) or ""
    if task_intent in {"code_change_required", "mutation_required", "implementation_required"}:
        return "coding"
    if task_intent == "unspecified":
        ai_operator = dict(record.get("ai_operator") or {})
        operator_validation_contract = dict(ai_operator.get("validation_contract") or {})
        if bool(operator_validation_contract.get("code_change_required")):
            return "coding"
        operator_task_packet = dict(ai_operator.get("task_packet") or {})
        operator_symbol_targets = [
            item
            for item in ai_operator.get("symbol_targets") or []
            if isinstance(item, dict)
            and _normalize_text(item.get("relative_path"))
            and _normalize_text(item.get("symbol"))
        ]
        ticket_contract = dict(record.get("ticket_contract_current") or {})
        target_surface = dict(ticket_contract.get("target_surface") or {})
        target_files = [str(item).strip() for item in target_surface.get("target_files") or [] if str(item).strip()]
        prompt_symbol_targets = [
            item
            for item in target_surface.get("prompt_symbol_targets") or []
            if isinstance(item, dict)
            and str(item.get("relative_path") or "").strip()
            and str(item.get("symbol") or "").strip()
        ]
        owning_symbols = [str(item).strip() for item in target_surface.get("owning_symbols") or [] if str(item).strip()]
        validation = dict((record.get("execution_digest") or {}).get("validation") or {})
        expected_commands = [
            str(item).strip() for item in validation.get("expected_commands") or [] if str(item).strip()
        ]
        if not target_files:
            target_files = [
                str(item).strip() for item in operator_task_packet.get("target_files") or [] if str(item).strip()
            ]
        if not expected_commands:
            expected_commands = [
                str(item).strip()
                for item in operator_validation_contract.get("expected_commands") or []
                if str(item).strip()
            ]
        if not expected_commands:
            expected_commands = [
                str(item).strip()
                for item in ((record.get("validation_contract") or {}) or {}).get("expected_commands", [])
                if str(item).strip()
            ]
        if target_files and (prompt_symbol_targets or operator_symbol_targets or owning_symbols or expected_commands):
            return "coding"
    return "generic"


def _attestation_scope_for_record(record: dict[str, Any]) -> dict[str, Any]:
    execution_slice = dict((record.get("ticket_contract_current") or {}).get("execution_slice") or {})
    scope_ref = (
        _normalize_text(execution_slice.get("slice_id"))
        or _normalize_text(execution_slice.get("execution_phase"))
        or _normalize_text(record.get("task_id"))
        or str(record.get("task_number") or "")
    )
    return {
        "scope_type": "execution_unit",
        "scope_ref": scope_ref,
    }


def _typed_ref_from_value(ref_value: Any, *, pack_id: str) -> dict[str, Any] | None:
    ref_id = _normalize_text(ref_value)
    if not ref_id:
        return None
    lowered = ref_id.lower()
    if "planner-traces" in lowered or re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{27,}", lowered):
        ref_type = "planner_trace"
        owned_by = "CONTROL_PLANE"
        owner_pack = None
    elif "outcome-policy" in lowered or "pkt-outcome-" in lowered:
        ref_type = "execution_trace"
        owned_by = "CONTROL_PLANE"
        owner_pack = None
    elif lowered.endswith("diff.patch") or lowered.endswith(".diff"):
        ref_type = "diff"
        owned_by = "PACK"
        owner_pack = pack_id
    elif "commands.log" in lowered or "tests.json" in lowered or "devexecutor/runs" in lowered:
        ref_type = "test_run"
        owned_by = "PACK"
        owner_pack = pack_id
    elif lowered.endswith("git-status.txt") or "changed_files" in lowered:
        ref_type = "workspace_status"
        owned_by = "PACK"
        owner_pack = pack_id
    else:
        ref_type = "artifact_manifest"
        owned_by = "PACK"
        owner_pack = pack_id
    return {
        "ref_id": ref_id,
        "ref_type": _normalize_controlled_ref_type(ref_type),
        "owned_by": owned_by,
        "pack_id": owner_pack,
    }


def _typed_evidence_refs(raw_refs: list[Any], *, pack_id: str) -> list[dict[str, Any]]:
    typed_refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_refs:
        typed = _typed_ref_from_value(item, pack_id=pack_id)
        if not typed:
            continue
        key = str(typed["ref_id"])
        if key in seen:
            continue
        seen.add(key)
        typed_refs.append(typed)
    return typed_refs


def _attestation_verdict_for_record(record: dict[str, Any]) -> str:
    quality_gate = dict(record.get("quality_gate") or {})
    final_status = _normalize_text(record.get("final_status")) or _normalize_text(
        (record.get("control_plane_outcome") or {}).get("status")
    )
    if final_status in {"blocked", "waiting_on_human"}:
        return "ESCALATE"
    if bool(quality_gate.get("passed")) or final_status == "done":
        return "PASS"
    if final_status in {"running", "queued"}:
        return "PARTIAL"
    return "FAIL"


def _attestation_type_for_record(record: dict[str, Any]) -> str:
    quality_gate = dict(record.get("quality_gate") or {})
    final_status = _normalize_text(record.get("final_status")) or ""
    control_plane = _control_plane_outcome(record)
    if final_status == "done" or _normalize_text(control_plane.get("status")) == "done":
        return "CLOSURE"
    if bool(quality_gate) or _normalize_text(control_plane.get("reason_code")):
        return "QUALITY_GATE"
    if final_status in {"blocked", "waiting_on_human"} or _normalize_text(control_plane.get("status")) in {
        "blocked",
        "waiting_on_human",
    }:
        return "ESCALATION_REQUEST"
    return "CHECKPOINT"


def build_attestation_from_run_ledger_record(
    record: dict[str, Any], *, invocation_id: str | None = None
) -> dict[str, Any]:
    control_plane = _control_plane_outcome(record)
    refs = dict(record.get("refs") or {})
    ai_operator = dict(record.get("ai_operator") or {})
    pack_id = _pack_id_for_record(record)
    raw_refs = list(ai_operator.get("evidence_refs") or [])
    for ref_value in (
        refs.get("proposal_diff_ref"),
        refs.get("proposal_changed_files_ref"),
        refs.get("outcome_policy_trace_ref"),
        (record.get("planner") or {}).get("local_trace_ref"),
        (record.get("planner") or {}).get("trace_packet_id"),
    ):
        ref_text = _normalize_text(ref_value)
        if ref_text:
            raw_refs.append(ref_text)
    attestation_type = _attestation_type_for_record(record)
    reason_code = (
        _normalize_text(control_plane.get("reason_code"))
        or _normalize_text((record.get("failure") or {}).get("code"))
        or "UNKNOWN"
    )
    pack_attestation_id = _sha1_fingerprint(
        _normalize_text(record.get("project_id")),
        int(record.get("task_number") or 0),
        _normalize_text(record.get("run_id")) or _normalize_text(invocation_id),
        attestation_type,
        reason_code,
    )
    return {
        "attestation_id": _sha1_fingerprint(
            "attestation",
            _normalize_text(record.get("tenant_id")) or "default",
            _normalize_text(record.get("project_id")),
            int(record.get("task_number") or 0),
            _normalize_text(record.get("run_id")) or _normalize_text(invocation_id),
            attestation_type,
            reason_code,
        ),
        "pack_attestation_id": pack_attestation_id,
        "ticket_id": _normalize_text(record.get("task_id")),
        "execution_unit_id": _normalize_text(record.get("slice_id"))
        or _normalize_text((_attestation_scope_for_record(record)).get("scope_ref"))
        or _normalize_text(record.get("task_id")),
        "pack_id": pack_id,
        "attester_role": _attestation_attester_role_for_record(record),
        "attestation_type": attestation_type,
        "verdict": _attestation_verdict_for_record(record),
        "scope": _attestation_scope_for_record(record),
        "reason_code": reason_code,
        "reason_summary": _normalize_text(control_plane.get("reason_summary"))
        or _normalize_text((record.get("failure") or {}).get("summary")),
        "evidence_refs": _typed_evidence_refs(raw_refs, pack_id=pack_id),
        "timestamp_utc": _normalize_text(record.get("recorded_at_utc")),
        "supersedes": None,
    }


def to_compact_run_ledger_record(
    record: dict[str, Any],
    *,
    invocation_id: str | None = None,
) -> dict[str, Any]:
    ai_operator = dict(record.get("ai_operator") or {})
    task_packet = dict(ai_operator.get("task_packet") or {})
    validation_contract = dict(ai_operator.get("validation_contract") or {})
    refs = dict(record.get("refs") or {})
    evidence_refs = [str(item).strip() for item in list(ai_operator.get("evidence_refs") or []) if str(item).strip()]
    for ref_value in (
        refs.get("proposal_diff_ref"),
        refs.get("proposal_changed_files_ref"),
        refs.get("outcome_policy_trace_ref"),
        (record.get("planner") or {}).get("local_trace_ref"),
        (record.get("planner") or {}).get("trace_packet_id"),
    ):
        ref_text = _normalize_text(ref_value)
        if ref_text and ref_text not in evidence_refs:
            evidence_refs.append(ref_text)
    target_files = [
        str(item).strip()
        for item in list(task_packet.get("target_files") or validation_contract.get("target_files") or [])
        if str(item).strip()
    ]
    validation_plan = [
        str(item).strip() for item in list(validation_contract.get("expected_commands") or []) if str(item).strip()
    ]
    execution_slice = dict((record.get("ticket_contract_current") or {}).get("execution_slice") or {})
    if not execution_slice:
        execution_slice = dict(
            (record.get("task_packet") or {}).get("ticket_contract_current", {}).get("execution_slice") or {}
        )
    explicit_transition_record = dict(record.get("transition_record") or {})
    transition_record = dict(
        record.get("transition_record")
        or build_transition_record_from_run_ledger_record(record, invocation_id=invocation_id)
    )
    control_plane = _control_plane_outcome(record)
    compact = {
        "schema_version": "aix.ledger.core.current",
        **_runtime_authority_markers(),
        **_artifact_projection_markers(),
        "event_id": _sha1_fingerprint(
            _normalize_text(record.get("tenant_id")) or "default",
            _normalize_text(record.get("project_id")),
            int(record.get("task_number") or 0),
            _normalize_text(record.get("run_id")) or _normalize_text(record.get("correlation_id")),
            _compact_actor(record),
            _compact_stage(record),
        ),
        "identity": {
            "tenant_id": _normalize_text(record.get("tenant_id")) or "default",
            "project_id": _normalize_text(record.get("project_id")),
            "task_number": int(record.get("task_number") or 0),
            "run_id": _normalize_text(record.get("run_id")),
            "invocation_id": _normalize_text(invocation_id),
        },
        "timestamp_utc": _normalize_text(record.get("recorded_at_utc")),
        "transition_record": transition_record,
        "workflow": {
            "actor": _compact_actor(record),
            "stage": _compact_stage(record),
            "handoff_status": control_plane.get("handoff_status"),
            "failure_category": control_plane.get("failure_category"),
        },
        "contract": {
            "objective": _normalize_text(task_packet.get("title")),
            "scope": {
                "in_scope": [
                    str(item).strip()
                    for item in list(
                        (record.get("ticket_contract_current") or {}).get("scope", {}).get("in_scope") or []
                    )
                    if str(item).strip()
                ],
                "out_of_scope": [
                    str(item).strip()
                    for item in list(
                        (record.get("ticket_contract_current") or {}).get("scope", {}).get("out_of_scope") or []
                    )
                    if str(item).strip()
                ],
            },
            "target_files": target_files,
            "target_symbols": _target_symbols_from_record(record),
            "acceptance_criteria": [
                str(item).strip()
                for item in list((record.get("ticket_contract_current") or {}).get("acceptance_criteria") or [])
                if str(item).strip()
            ],
            "validation_plan": validation_plan,
            "constraints": _constraint_projection(record),
            "execution_slice": {
                "slice_id": _normalize_text(execution_slice.get("slice_id"))
                or _normalize_text(execution_slice.get("execution_phase")),
                "phase": _normalize_text(execution_slice.get("execution_phase")),
                "target_files": [
                    str(item).strip() for item in list(execution_slice.get("target_files") or []) if str(item).strip()
                ],
                "validation_commands": [
                    str(item).strip()
                    for item in list(execution_slice.get("validation_commands") or [])
                    if str(item).strip()
                ],
                "instructions": _normalize_text(execution_slice.get("instructions")),
            },
        },
        "outcome": {
            "status": _normalize_text(explicit_transition_record.get("new_state"))
            or _normalize_text(control_plane.get("handoff_status"))
            or _normalize_text(record.get("final_status"))
            or _normalize_text(record.get("status")),
            "execution_state": build_execution_state_projection(record),
            "reason_code": control_plane.get("reason_code"),
            "reason_summary": control_plane.get("reason_summary"),
            "blocked_reason": _normalize_text(control_plane.get("blocked_reason")),
            "blocked_at": _normalize_text(control_plane.get("blocked_at")),
            "remediation": [
                _normalize_text(item) for item in list(control_plane.get("remediation") or []) if _normalize_text(item)
            ],
            "next_best_actions": [
                _normalize_text(item)
                for item in list(control_plane.get("next_best_actions") or [])
                if _normalize_text(item)
            ],
            "changed_file_count": _safe_int((record.get("result") or {}).get("changed_file_count")),
        },
        "repair": {
            "failure_code": _normalize_text((record.get("repair_loop") or {}).get("failure_code")),
            "budget_key": _normalize_text((record.get("repair_loop") or {}).get("budget_key")),
            "attempts_used": _safe_int((record.get("repair_loop") or {}).get("attempts_used")),
            "max_attempts": _safe_int((record.get("repair_loop") or {}).get("max_attempts")),
            "planner_attempts_total": _safe_int((record.get("repair_loop") or {}).get("planner_attempts_total")),
            "retry_attempts_recorded": _safe_int((record.get("repair_loop") or {}).get("retry_attempts_recorded")),
            "timeline": [
                {
                    key: value
                    for key, value in {
                        "attempt_index": _safe_int(item.get("attempt_index")),
                        "stage": _normalize_text(item.get("stage")),
                        "reason_code": _normalize_text(item.get("reason_code")),
                        "provider": _normalize_text(item.get("provider")),
                        "model": _normalize_text(item.get("model")),
                        "lane": _normalize_text(item.get("lane")),
                        "trace_ref": _normalize_text(item.get("trace_ref")),
                        "mutating_step_present": (
                            bool(item.get("mutating_step_present"))
                            if item.get("mutating_step_present") is not None
                            else None
                        ),
                        "context_source": _normalize_text(item.get("context_source")),
                    }.items()
                    if value is not None
                }
                for item in list((record.get("repair_loop") or {}).get("retry_history") or [])
                if isinstance(item, dict)
            ],
        },
        "minimal_run_outcome": dict(record.get("minimal_run_outcome") or _minimal_run_outcome(record)),
        "evidence_refs": evidence_refs,
        "attestation": build_attestation_from_run_ledger_record(record, invocation_id=invocation_id),
        "attestation_authorization": dict(record.get("attestation_authorization") or {}),
        "execution_digest": dict(record.get("execution_digest") or {}),
        "temporal": _compact_temporal_projection(record),
    }
    return compact


def classify_run_failure(record: dict[str, Any]) -> dict[str, Any]:
    quality_gate = dict(record.get("quality_gate") or {})
    planner = dict(record.get("planner") or {})
    writeback = dict(record.get("writeback") or {})
    diagnostic = dict(record.get("diagnostic_outcome") or {})
    result = dict(record.get("result") or {})
    error = dict(result.get("error") or {})

    def _failure_packet(*, stage: str, code: str, category: str, summary: str, tool_class: str = "") -> dict[str, Any]:
        policy = resolve_recovery_policy(
            code=code,
            workflow_stage=stage,
            task_intent=_normalize_text(record.get("task_intent")),
            repo_profile_id=_normalize_text((record.get("repo_profile") or {}).get("profile_id")),
            provider=_normalize_text(planner.get("provider")),
            model=_normalize_text(planner.get("model")),
            category=category,
            tool_class=tool_class,
            quality_message=summary,
        )
        return {
            "stage": stage,
            "code": _normalize_text(code) or "UNKNOWN",
            "category": _normalize_text(policy.get("category")) or category,
            "summary": _normalize_text(summary) or "run failed",
            "outcome_class": _normalize_text(policy.get("outcome_class")),
            "tool_class": _normalize_text(policy.get("tool_class")),
            "policy_action": _normalize_text(policy.get("policy_action")),
            "failure_family": _normalize_text(policy.get("failure_family")),
            "retry_budget_key": _normalize_text(policy.get("retry_budget_key")),
        }

    if diagnostic:
        diagnostic_code = _normalize_text(diagnostic.get("reason_code")) or "DIAGNOSTIC_BLOCKED"
        diagnostic_category = "diagnostic_blocked"
        if diagnostic_code in {
            "PLANNER_UPSTREAM_REQUEST_FAILED",
            "PLANNER_UPSTREAM_FAILED",
            "PROVIDER_EMPTY_RESPONSE",
            "PROVIDER_REASONING_EXHAUSTION",
        }:
            diagnostic_category = "execution_failure"
        return _failure_packet(
            stage="planning",
            code=diagnostic_code,
            category=diagnostic_category,
            summary=_normalize_text(diagnostic.get("reason_summary")) or "bounded diagnostic outcome blocked mutation",
            tool_class="planner",
        )
    if not bool(quality_gate.get("passed", True)):
        return _failure_packet(
            stage="quality_gate",
            code=_normalize_text(quality_gate.get("code")) or "QUALITY_GATE_REJECTED",
            category="quality_gate_rejected",
            summary=_normalize_text(quality_gate.get("message")) or "quality gate rejected the run",
            tool_class="verifier",
        )
    if _normalize_text(planner.get("error")):
        return _failure_packet(
            stage="planning",
            code=_normalize_text(planner.get("error")) or "PLANNER_FAILED",
            category="planner_failure",
            summary=_normalize_text(planner.get("error")) or "planner failed",
            tool_class="planner",
        )
    if _normalize_text(writeback.get("workhub_error")) or _normalize_text(writeback.get("opsbus_error")):
        return _failure_packet(
            stage="writeback",
            code=_normalize_text(writeback.get("workhub_error"))
            or _normalize_text(writeback.get("opsbus_error"))
            or "WRITEBACK_FAILED",
            category="writeback_failure",
            summary=_normalize_text(writeback.get("workhub_error"))
            or _normalize_text(writeback.get("opsbus_error"))
            or "writeback failed",
            tool_class="runtime",
        )
    if _normalize_text(error.get("code")) or _normalize_text(error.get("message")):
        return _failure_packet(
            stage="execution",
            code=_normalize_text(error.get("code")) or "EXECUTION_FAILED",
            category="execution_failure",
            summary=_normalize_text(error.get("message")) or "execution failed",
            tool_class="executor",
        )
    return {
        "stage": "completed",
        "code": "OK",
        "category": "success",
        "summary": "run completed successfully",
        "outcome_class": "success.verified",
        "tool_class": "verifier",
        "policy_action": "continue",
        "failure_family": None,
        "retry_budget_key": None,
    }


def _planner_packet_summary(planner: dict[str, Any]) -> dict[str, Any] | None:
    planner_packet = dict(planner.get("planner_packet") or {})
    planner_packet_status = _normalize_text(planner.get("planner_packet_status"))
    planner_packet_metrics = dict(planner.get("planner_packet_metrics") or {})
    planner_packet_clarification_gaps = [
        _normalize_text(item)
        for item in list(planner.get("planner_packet_clarification_gaps") or [])
        if _normalize_text(item)
    ]
    if (
        not planner_packet
        and not planner_packet_status
        and not planner_packet_metrics
        and not planner_packet_clarification_gaps
    ):
        return None
    target_files = [
        str(item).strip()
        for item in list(planner_packet.get("target_files") or planner_packet.get("admitted_target_files") or [])
        if str(item).strip()
    ]
    validation_commands = [
        str(item).strip()
        for item in list(
            planner_packet.get("validation_commands")
            or ((planner_packet.get("ticket_contract_current") or {}).get("execution_slice") or {}).get(
                "validation_commands"
            )
            or []
        )
        if str(item).strip()
    ]
    clarification_gaps = planner_packet_clarification_gaps or [
        _normalize_text(item)
        for item in list((planner_packet.get("ticket_contract_validation") or {}).get("missing_fields") or [])
        if _normalize_text(item)
    ]
    target_symbols = [
        str(item).strip() for item in list(planner_packet.get("admitted_symbol_targets") or []) if str(item).strip()
    ]
    return {
        "schema_name": _normalize_text(planner_packet.get("schema_name")),
        "schema_version": _normalize_text(planner_packet.get("schema_version")),
        "status": planner_packet_status or _normalize_text(planner_packet.get("status")),
        "slice_id": _normalize_text(planner_packet.get("slice_id")),
        "task_number": _safe_int(planner_packet.get("task_number")),
        "task_intent": _normalize_text(planner_packet.get("task_intent")),
        "target_symbol": _normalize_text(planner_packet.get("target_symbol"))
        or (target_symbols[0] if target_symbols else None),
        "target_files": target_files,
        "validation_commands": validation_commands,
        "execution_lane": _normalize_text((planner_packet.get("tool_policy") or {}).get("execution_lane")),
        "clarification_gaps": clarification_gaps,
        "metrics": planner_packet_metrics or None,
    }


def build_run_ledger_record(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload.get("result") or {})
    evidence = dict(result.get("evidence") or {})
    workspace = dict(payload.get("workspace") or {})
    planner = dict(payload.get("planner") or {})
    writeback = dict(payload.get("writeback") or {})
    codeintel = dict(payload.get("codeintel") or {})
    quality_gate = dict(payload.get("quality_gate") or {})
    diagnostic_outcome = dict(payload.get("diagnostic_outcome") or {})
    repair_loop = _merged_repair_loop(payload)
    assist_loop = {
        **dict(payload.get("assist_loop") or {}),
        **dict(evidence.get("assist_loop") or {}),
    }
    task_packet = dict(payload.get("task_packet") or {})
    validation_contract = dict(payload.get("validation_contract") or {})
    control_plane_outcome = dict(payload.get("control_plane_outcome") or {})
    temporal = dict(payload.get("temporal") or {})
    failure = classify_run_failure(payload)
    failure_lineage = _failure_lineage_envelope(payload)
    correlation_id = (
        _normalize_text(result.get("correlation_id")) or _normalize_text(payload.get("correlation_id")) or "unknown"
    )
    project_id = _normalize_text(payload.get("project_id")) or _normalize_text(result.get("project_id")) or "unknown"
    task_number = int(payload.get("task_number") or result.get("task_number") or 0)
    ai_operator = _build_ai_operator_record(
        task_packet=task_packet,
        validation_contract=validation_contract,
        planner=planner,
        result=result,
        evidence=evidence,
        quality_gate=quality_gate,
    )
    planner_packet_summary = _planner_packet_summary(planner)

    record = {
        "schema_version": "aix.run_ledger.current",
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "tenant_id": _normalize_text(payload.get("tenant_id")) or "default",
        "project_id": project_id,
        "task_number": task_number,
        "task_id": _normalize_text(payload.get("task_id")) or _normalize_text(result.get("task_id")),
        "correlation_id": correlation_id,
        "run_id": _normalize_text(result.get("run_id")),
        "status": _normalize_text(result.get("status")) or "unknown",
        "ok": bool(payload.get("ok")),
        "final_status": _normalize_text(payload.get("final_status")) or "unknown",
        "task_intent": _normalize_text(payload.get("task_intent")) or "unspecified",
        "control_plane_outcome": {
            "status": _normalize_text(control_plane_outcome.get("status")),
            "substate": _normalize_text(control_plane_outcome.get("substate")),
            "reason_code": _normalize_text(control_plane_outcome.get("reason_code")),
            "reason_summary": _normalize_text(control_plane_outcome.get("reason_summary")),
            "handoff_status": _normalize_text(control_plane_outcome.get("handoff_status")),
            "failure_category": _normalize_text(control_plane_outcome.get("failure_category")),
            "blocked_reason": _normalize_text(control_plane_outcome.get("blocked_reason")),
            "phase": _normalize_text(control_plane_outcome.get("phase")),
            "blocked_at": _normalize_text(control_plane_outcome.get("blocked_at")),
            "remediation": [
                _normalize_text(item)
                for item in list(control_plane_outcome.get("remediation") or [])
                if _normalize_text(item)
            ],
            "next_best_actions": [
                _normalize_text(item)
                for item in list(control_plane_outcome.get("next_best_actions") or [])
                if _normalize_text(item)
            ],
        },
        "ticket_contract_current": dict((task_packet.get("metadata") or {}).get("ticket_contract_current") or {}),
        "pack_contract": dict((payload.get("admission") or {}).get("pack_contract") or {}),
        "repo_profile": dict(payload.get("repo_profile") or {}),
        "temporal": {
            "enabled": bool(temporal.get("enabled")),
            "dispatch": dict(temporal.get("dispatch") or {}),
            "state_machine": dict(temporal.get("state_machine") or {}),
        },
        "workspace": {
            "source_workspace_root": _normalize_text(workspace.get("source_workspace_root")),
            "effective_workspace_root": _normalize_text(workspace.get("effective_workspace_root")),
            "proposal_branch": (
                _normalize_text((workspace.get("proposal_workspace") or {}).get("branch_name"))
                if isinstance(workspace.get("proposal_workspace"), dict)
                else None
            ),
        },
        "planner": {
            "planner_used": bool(planner.get("planner_used")),
            "attempts": planner.get("attempts"),
            "provider": _normalize_text(planner.get("provider")),
            "model": _normalize_text(planner.get("model")),
            "model_lane": _normalize_text(planner.get("model_lane")),
            "model_lane_role": _normalize_text(planner.get("model_lane_role")),
            "model_lane_cost_tier": _normalize_text(planner.get("model_lane_cost_tier")),
            "model_lane_selection_reason": _normalize_text(planner.get("model_lane_selection_reason")),
            "model_lane_config_path": _normalize_text(planner.get("model_lane_config_path")),
            "model_lane_source_chain": [
                dict(item) for item in list(planner.get("model_lane_source_chain") or []) if isinstance(item, dict)
            ],
            "error": _normalize_text(planner.get("error")),
            "trace_packet_id": _normalize_text(planner.get("trace_packet_id")),
            "local_trace_ref": _normalize_text(planner.get("local_trace_ref")),
            "contract_fingerprint": _normalize_text(planner.get("contract_fingerprint")),
            "planner_packet_summary": planner_packet_summary,
        },
        "codeintel": {
            "policy_mode": _normalize_text(codeintel.get("policy_mode"))
            or _normalize_text((codeintel.get("policy") or {}).get("mode")),
            "mode": _normalize_text(codeintel.get("mode")),
            "error": _normalize_text(codeintel.get("error")),
            "query_identity": _normalize_codeintel_query_identity(codeintel.get("query_identity")),
            "retrieval_mode": _normalize_text(codeintel.get("retrieval_mode")),
            "confidence_score": _normalize_float(codeintel.get("confidence_score")),
            "latency_ms": _normalize_float(codeintel.get("latency_ms")),
            "retrieval_adapter": _normalize_text(codeintel.get("retrieval_adapter")),
            "adapter_latency_ms": _normalize_float(codeintel.get("adapter_latency_ms")),
            "topk_returned": _safe_int(codeintel.get("topk_returned")),
            "source_refs_count": _safe_int(codeintel.get("source_refs_count")),
            "citations_count": _safe_int(codeintel.get("citations_count")),
            "citations": _normalize_codeintel_citations(codeintel.get("citations")),
            "fallback_reason_code": _normalize_text(codeintel.get("fallback_reason_code"))
            or _normalize_text((codeintel.get("fallback") or {}).get("reason_code")),
            "fallback_reason_summary": _normalize_text(codeintel.get("fallback_reason_summary"))
            or _normalize_text((codeintel.get("fallback") or {}).get("reason_summary")),
        },
        "quality_gate": {
            "passed": bool(quality_gate.get("passed")),
            "code": _normalize_text(quality_gate.get("code")),
            "message": _normalize_text(quality_gate.get("message")),
            "reasons": list(quality_gate.get("reasons") or []),
        },
        "repair_loop": {
            "failure_code": _normalize_text(repair_loop.get("failure_code")),
            "failure_class": _normalize_text(repair_loop.get("failure_class")),
            "policy_action": _normalize_text(repair_loop.get("policy_action")),
            "budget_key": _normalize_text(repair_loop.get("budget_key")),
            "max_attempts": _safe_int(repair_loop.get("max_attempts")),
            "attempts_used": _safe_int(repair_loop.get("attempts_used")),
            "planner_attempts_total": _safe_int(repair_loop.get("planner_attempts_total")),
            "retry_attempts_recorded": _safe_int(repair_loop.get("retry_attempts_recorded")),
            "retryable": bool(repair_loop.get("retryable")),
            "same_turn_repair_active": bool(repair_loop.get("same_turn_repair_active")),
            "recovery_task_suppressed": bool(repair_loop.get("recovery_task_suppressed")),
            "recovery_submission_reason": _normalize_text(repair_loop.get("recovery_submission_reason")),
            "retry_history": [
                {
                    key: value
                    for key, value in {
                        "stage": _normalize_text(item.get("stage")),
                        "reason_code": _normalize_text(item.get("reason_code")),
                        "budget_key": _normalize_text(item.get("budget_key")),
                        "attempt_index": _safe_int(item.get("attempt_index")),
                        "failure_detail": _normalize_text(item.get("failure_detail")),
                        "provider": _normalize_text(item.get("provider")),
                        "model": _normalize_text(item.get("model")),
                        "lane": _normalize_text(item.get("lane")),
                        "trace_ref": _normalize_text(item.get("trace_ref")),
                        "step_count": _safe_int(item.get("step_count")),
                        "mutating_step_present": (
                            bool(item.get("mutating_step_present"))
                            if item.get("mutating_step_present") is not None
                            else None
                        ),
                        "context_source": _normalize_text(item.get("context_source")),
                        "codeintel_confidence_score": _normalize_float(item.get("codeintel_confidence_score")),
                        "codeintel_fallback_reason_code": _normalize_text(item.get("codeintel_fallback_reason_code")),
                    }.items()
                    if value is not None
                }
                for item in list(repair_loop.get("retry_history") or [])
                if isinstance(item, dict)
            ],
        },
        "failure_lineage": failure_lineage,
        "assist_loop": {
            "conversation_state": _normalize_text(assist_loop.get("conversation_state")),
            "awaiting_actor": _normalize_text(assist_loop.get("awaiting_actor")),
            "open_questions": [
                str(item).strip() for item in assist_loop.get("open_questions") or [] if str(item).strip()
            ],
            "guidance_requests": [
                {
                    "requester": _normalize_text(item.get("requester")),
                    "reason_code": _normalize_text(item.get("reason_code")),
                    "question": _normalize_text(item.get("question")),
                    "channel": _normalize_text(item.get("channel")),
                }
                for item in list(assist_loop.get("guidance_requests") or [])
                if isinstance(item, dict)
            ],
            "onlineai_guidance": [
                {
                    "summary": _normalize_text(item.get("summary")),
                    "source": _normalize_text(item.get("source")),
                    "applied": bool(item.get("applied")),
                }
                for item in list(assist_loop.get("onlineai_guidance") or [])
                if isinstance(item, dict)
            ],
            "resume_context": dict(assist_loop.get("resume_context") or {}),
        },
        "escalation_packet": dict(evidence.get("escalation_packet") or {}),
        "ai_operator": ai_operator,
        "failure": failure,
        "diagnostic_outcome": diagnostic_outcome,
        "writeback": {
            "status": _normalize_text(writeback.get("status")),
            "opsbus_written": bool(writeback.get("opsbus_written")),
            "workhub_written": bool(writeback.get("workhub_written")),
            "retry_enqueued": bool(writeback.get("retry_enqueued")),
            "opsbus_error": _normalize_text(writeback.get("opsbus_error")),
            "workhub_error": _normalize_text(writeback.get("workhub_error")),
            "retry_event_ref": _normalize_text(writeback.get("retry_event_ref")),
        },
        "refs": {
            "proposal_diff_ref": (
                _normalize_text((evidence.get("proposal_artifacts") or {}).get("diff_ref"))
                if isinstance(evidence.get("proposal_artifacts"), dict)
                else None
            ),
            "proposal_changed_files_ref": (
                _normalize_text((evidence.get("proposal_artifacts") or {}).get("changed_files_ref"))
                if isinstance(evidence.get("proposal_artifacts"), dict)
                else None
            ),
            "outcome_policy_trace_ref": (
                _normalize_text((payload.get("outcome_policy_trace") or {}).get("trace_ref"))
                or _normalize_text((payload.get("outcome_policy_trace") or {}).get("local_trace_ref"))
            ),
        },
        "attestation_authorization": dict(payload.get("attestation_authorization") or {}),
        "audit_events": list(payload.get("audit_events") or []),
    }
    record["minimal_run_outcome"] = _minimal_run_outcome(
        {
            **record,
            "task_packet": task_packet,
            "validation_contract": validation_contract,
            "feature_slice_plan": list(payload.get("feature_slice_plan") or []),
        }
    )
    record["attestation"] = build_attestation_from_run_ledger_record(record)
    record["transition_record"] = build_transition_record_from_run_ledger_record(record)
    record["control_plane_outcome"] = _control_plane_outcome(record)
    record["execution_digest"] = build_execution_digest(payload)
    return record


def _build_ai_operator_record(
    *,
    task_packet: dict[str, Any],
    validation_contract: dict[str, Any],
    planner: dict[str, Any],
    result: dict[str, Any],
    evidence: dict[str, Any],
    quality_gate: dict[str, Any],
) -> dict[str, Any]:
    transcript = [item for item in list(evidence.get("transcript") or []) if isinstance(item, dict)]
    evidence_refs = [str(item).strip() for item in list(result.get("evidence_refs") or []) if str(item).strip()]
    proposal_artifacts = dict(evidence.get("proposal_artifacts") or {})
    for ref_value in (
        proposal_artifacts.get("diff_ref"),
        proposal_artifacts.get("changed_files_ref"),
        evidence.get("transcript_ref"),
        evidence.get("logs_ref"),
        evidence.get("tests_ref"),
    ):
        ref_text = str(ref_value or "").strip()
        if ref_text and ref_text not in evidence_refs:
            evidence_refs.append(ref_text)
    typed_failures = _typed_failure_entries(transcript=transcript, result=result, quality_gate=quality_gate)
    return {
        "task_packet": {
            "task_id": _normalize_text(task_packet.get("task_id")),
            "task_number": int(task_packet.get("task_number") or 0) or None,
            "title": _normalize_text(task_packet.get("title")),
            "priority": _normalize_text(task_packet.get("priority")),
            "target_files": [
                str(item).strip() for item in list(validation_contract.get("target_files") or []) if str(item).strip()
            ],
        },
        "symbol_targets": [
            {
                "relative_path": _normalize_text(item.get("relative_path")),
                "symbol": _normalize_text(item.get("symbol")),
                "label": _normalize_text(item.get("label")),
            }
            for item in list(validation_contract.get("prompt_symbol_targets") or [])
            if isinstance(item, dict)
        ],
        "anchor_confirmations": [
            {
                "relative_path": _normalize_text((entry.get("result") or {}).get("relative_path")),
                "symbol_name": _normalize_text((entry.get("result") or {}).get("symbol_name")),
                "reason_code": _normalize_text((entry.get("result") or {}).get("reason_code")),
                "matched": bool((entry.get("result") or {}).get("matched")),
            }
            for entry in transcript
            if str(entry.get("tool") or "").strip() == "confirm_anchor"
        ],
        "mutation_attempts": [
            {
                "tool": str(entry.get("tool") or "").strip(),
                "ok": bool(entry.get("ok")),
                "relative_path": _normalize_text((entry.get("result") or {}).get("relative_path")),
                "changed_files": [
                    str(item).strip()
                    for item in list((entry.get("result") or {}).get("changed_files") or [])
                    if str(item).strip()
                ],
                "dry_run": bool((entry.get("result") or {}).get("dry_run")),
                "reason_code": (
                    _normalize_text((entry.get("result") or {}).get("reason_code"))
                    or _normalize_text((entry.get("result") or {}).get("code"))
                    or (
                        _normalize_text(
                            (((entry.get("result") or {}).get("dry_run_failures") or [{}])[0]).get("failure_code")
                        )
                        if isinstance((entry.get("result") or {}).get("dry_run_failures"), list)
                        and (entry.get("result") or {}).get("dry_run_failures")
                        else None
                    )
                ),
                "failure_detail": (
                    _normalize_text((entry.get("result") or {}).get("reason_summary"))
                    or _normalize_text((entry.get("result") or {}).get("message"))
                    or _normalize_text((entry.get("result") or {}).get("error"))
                    or (
                        _normalize_text(
                            (((entry.get("result") or {}).get("dry_run_failures") or [{}])[0]).get("detail")
                        )
                        if isinstance((entry.get("result") or {}).get("dry_run_failures"), list)
                        and (entry.get("result") or {}).get("dry_run_failures")
                        else None
                    )
                ),
                "semantic_precheck_passed": (entry.get("result") or {}).get("semantic_precheck_passed"),
                "post_apply_validation_passed": (entry.get("result") or {}).get("pyright_passed"),
                "post_apply_validation_scope": _normalize_text(
                    (entry.get("result") or {}).get("post_apply_validation_scope")
                ),
            }
            for entry in transcript
            if str(entry.get("tool") or "").strip() in ALL_MUTATION_TOOLS
        ],
        "typed_failures": typed_failures,
        "retry_actions": _retry_actions(planner=planner),
        "validation_contract": {
            "expected_commands": [
                str(item).strip()
                for item in list(validation_contract.get("expected_commands") or [])
                if str(item).strip()
            ],
            "code_change_required": bool(validation_contract.get("code_change_required")),
            "executed_commands": [
                str(item).strip() for item in list(evidence.get("commands") or []) if str(item).strip()
            ],
            "tests_detected": bool((evidence.get("tests") or {}).get("detected")),
        },
        "evidence_refs": evidence_refs,
    }


def _typed_failure_entries(
    *,
    transcript: list[dict[str, Any]],
    result: dict[str, Any],
    quality_gate: dict[str, Any],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []

    def _policy_entry(*, failure_code: str, source: str) -> dict[str, Any]:
        return resolve_recovery_policy(
            code=failure_code,
            workflow_stage=source,
            task_intent=_normalize_text(result.get("task_intent")),
            tenant_id=_normalize_text(result.get("tenant_id")),
            repo_profile_id=_normalize_text((result.get("repo_profile") or {}).get("profile_id")),
            provider=_normalize_text((result.get("planner") or {}).get("provider")),
            model=_normalize_text((result.get("planner") or {}).get("model")),
        )

    def _typed_failure_packet(
        *,
        source: str,
        failure_code: str,
        tool: str | None = None,
        detail: str | None = None,
        suggested_fix: str | None = None,
    ) -> dict[str, Any]:
        policy = _policy_entry(failure_code=failure_code, source=source)
        return {
            "source": source,
            "tool": _normalize_text(tool),
            "failure_code": _normalize_text(failure_code) or "UNKNOWN",
            "detail": _normalize_text(detail),
            "suggested_fix": _normalize_text(suggested_fix),
            "outcome_class": policy["outcome_class"],
            "tool_class": policy["tool_class"],
            "policy_action": policy["policy_action"],
            "failure_family": _normalize_text(policy.get("failure_family")),
            "retry_budget_key": _normalize_text(policy.get("retry_budget_key")),
            "matched_policy": _normalize_text(policy.get("matched_policy")),
            "next_best_actions": [
                _normalize_text(item) for item in list(policy.get("next_best_actions") or []) if _normalize_text(item)
            ],
        }

    for entry in transcript:
        if bool(entry.get("ok", True)):
            continue
        result_payload = dict(entry.get("result") or {})
        dry_run_failures = [
            item for item in list(result_payload.get("dry_run_failures") or []) if isinstance(item, dict)
        ]
        if dry_run_failures:
            for item in dry_run_failures:
                failure_code = (
                    _normalize_text(item.get("failure_code")) or _normalize_text(item.get("failure")) or "UNKNOWN"
                )
                failures.append(
                    _typed_failure_packet(
                        source="tool_transcript",
                        tool=_normalize_text(item.get("tool")) or _normalize_text(entry.get("tool")),
                        failure_code=failure_code,
                        detail=_normalize_text(item.get("detail")),
                        suggested_fix=_normalize_text(item.get("suggested_fix")),
                    )
                )
            continue
        failure_code = _normalize_text(entry.get("error")) or "TOOL_STEP_FAILED"
        failures.append(
            _typed_failure_packet(
                source="tool_transcript",
                tool=_normalize_text(entry.get("tool")),
                failure_code=failure_code,
                detail=_normalize_text(entry.get("error")),
            )
        )
    gate_code = _normalize_text(quality_gate.get("code"))
    if gate_code and not bool(quality_gate.get("passed", True)):
        failures.append(
            _typed_failure_packet(
                source="quality_gate",
                failure_code=gate_code,
                detail=_normalize_text(quality_gate.get("message")),
            )
        )
    error = dict(result.get("error") or {})
    error_code = _normalize_text(error.get("code"))
    if error_code:
        failures.append(
            _typed_failure_packet(
                source="result_error",
                failure_code=error_code,
                detail=_normalize_text(error.get("message")),
            )
        )
    return failures


def _retry_actions(*, planner: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    retry_history = [dict(item) for item in list(planner.get("repair_retry_history") or []) if isinstance(item, dict)]
    if retry_history:
        stage_kind_map = {
            "validation": "planner_retry",
            "execution": "execution_retry",
            "quality_gate": "quality_gate_retry",
        }
        for item in retry_history:
            actions.append(
                {
                    "kind": stage_kind_map.get(_normalize_text(item.get("stage")) or "", "repair_retry"),
                    "reason_code": _normalize_text(item.get("reason_code")),
                    "budget_key": _normalize_text(item.get("budget_key")),
                }
            )
        return actions
    if _normalize_text(planner.get("replanned_after_validation_error")):
        actions.append(
            {
                "kind": "planner_retry",
                "reason_code": _normalize_text(planner.get("replanned_after_validation_error")),
            }
        )
    if _normalize_text(planner.get("replanned_after_execution_failure")):
        actions.append(
            {
                "kind": "execution_retry",
                "reason_code": _normalize_text(planner.get("replanned_after_execution_failure")),
            }
        )
    if _normalize_text(planner.get("replanned_after_quality_gate_failure")):
        actions.append(
            {
                "kind": "quality_gate_retry",
                "reason_code": _normalize_text(planner.get("replanned_after_quality_gate_failure")),
            }
        )
    return actions


def persist_run_ledger_record(payload: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    record = build_run_ledger_record(payload)
    out_root = Path(root) if root is not None else get_run_ledger_root()
    out_root.mkdir(parents=True, exist_ok=True)
    correlation_id = record["correlation_id"]
    file_name = f"{correlation_id}.json"
    out_path = out_root / file_name
    latest_path = out_root / "latest.json"
    record["execution_digest"] = build_execution_digest(
        payload,
        run_ledger_ref=str(out_path),
        run_ledger_latest_ref=str(latest_path),
    )
    _atomic_write_json(out_path, record)
    _atomic_write_json(latest_path, record)
    capsule_outcome = _persist_runtime_memory_capsule(record, root=out_root / "memory_capsules")
    return {
        "record": record,
        "ref": str(out_path),
        "latest_ref": str(latest_path),
        "capsule_ref": (capsule_outcome or {}).get("ref"),
        "capsule_latest_ref": (capsule_outcome or {}).get("latest_ref"),
    }


def build_task_fast_ledger(
    *,
    project_id: str,
    task_number: int,
    records: list[dict[str, Any]],
    invocations: list[dict[str, Any]] | None = None,
    task_trace_bundle: dict[str, Any] | None = None,
    transition_audit: list[dict[str, Any]] | None = None,
    expand: bool = False,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for invocation in invocations or []:
        invocation_id = _normalize_text(invocation.get("invocation_id")) or "unknown"
        accepted_at = _normalize_text(invocation.get("accepted_at_utc"))
        if accepted_at:
            entries.append(
                {
                    "timestamp_utc": accepted_at,
                    "kind": "invocation.accepted",
                    "invocation_id": invocation_id,
                    "status": _normalize_text(invocation.get("status")) or "accepted",
                    "summary": "run-once invocation accepted",
                }
            )
        started_at = _normalize_text(invocation.get("started_at_utc"))
        if started_at:
            entries.append(
                {
                    "timestamp_utc": started_at,
                    "kind": "invocation.started",
                    "invocation_id": invocation_id,
                    "status": "running",
                    "summary": "run-once invocation started",
                }
            )
        for stage_event in list(invocation.get("stage_events") or []):
            if not isinstance(stage_event, dict):
                continue
            stage_name = _normalize_text(stage_event.get("stage"))
            timestamp_utc = _normalize_text(stage_event.get("timestamp_utc"))
            if not stage_name or not timestamp_utc:
                continue
            entries.append(
                {
                    "timestamp_utc": timestamp_utc,
                    "kind": "executor.stage",
                    "invocation_id": invocation_id,
                    "status": _normalize_text(invocation.get("status")) or "running",
                    "stage": stage_name,
                    "summary": _normalize_text(stage_event.get("summary")) or stage_name,
                    "details": dict(stage_event.get("details") or {}),
                }
            )
        finished_at = _normalize_text(invocation.get("finished_at_utc"))
        if finished_at:
            result = dict(invocation.get("result") or {})
            reason_code = (_normalize_text(result.get("reason")) or "").upper() or None
            final_status = _normalize_text(result.get("final_status")) or _RUNNER_NOOP_STATUS_MAP.get(reason_code or "")
            noop_summary = _runner_noop_summary(
                reason_code,
                current_status=result.get("current_status"),
                target_task_number=result.get("target_task_number"),
            )
            invocation_summary = "run-once invocation finished"
            if reason_code and noop_summary:
                invocation_summary = noop_summary
            elif final_status:
                invocation_summary = f"run-once invocation finished with task status {final_status}"
            entries.append(
                {
                    "timestamp_utc": finished_at,
                    "kind": "invocation.finished",
                    "invocation_id": invocation_id,
                    "status": _normalize_text(invocation.get("status")) or "completed",
                    "summary": invocation_summary,
                    "final_status": final_status,
                    "ok": bool(result.get("ok")),
                    "error": _normalize_text(invocation.get("error")) or reason_code,
                }
            )
    bundle = dict(task_trace_bundle or {})
    task_snapshot = dict(bundle.get("task") or {})
    correlation_ids = [
        str(item).strip()
        for item in list((bundle.get("trace") or {}).get("correlation_ids") or [])
        if str(item).strip()
    ]
    if _normalize_text(task_snapshot.get("created_at")):
        entries.append(
            {
                "timestamp_utc": _normalize_text(task_snapshot.get("created_at")),
                "kind": "workhub.task.created",
                "status": _normalize_text(task_snapshot.get("status")) or "queued",
                "summary": "workhub task created",
                "task_id": _normalize_text(task_snapshot.get("id")),
                "correlation_ids": correlation_ids,
            }
        )
    if _normalize_text(task_snapshot.get("updated_at")) and _normalize_text(
        task_snapshot.get("updated_at")
    ) != _normalize_text(task_snapshot.get("created_at")):
        entries.append(
            {
                "timestamp_utc": _normalize_text(task_snapshot.get("updated_at")),
                "kind": "workhub.task.snapshot",
                "status": _normalize_text(task_snapshot.get("status")),
                "summary": f"current workhub status: {_normalize_text(task_snapshot.get('status')) or 'unknown'}",
                "task_id": _normalize_text(task_snapshot.get("id")),
                "correlation_ids": correlation_ids,
            }
        )
    for audit in list(transition_audit or []):
        if not isinstance(audit, dict):
            continue
        metadata = audit.get("metadata_json")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}
        metadata = metadata if isinstance(metadata, dict) else {}
        next_status = _normalize_text(metadata.get("to_status")) or _normalize_text(metadata.get("new_state"))
        prior_status = _normalize_text(metadata.get("from_status")) or _normalize_text(metadata.get("prior_state"))
        summary = "workhub task transitioned"
        if prior_status or next_status:
            summary = f"workhub transition: {prior_status or 'unknown'} -> {next_status or 'unknown'}"
        entries.append(
            {
                "timestamp_utc": _normalize_text(audit.get("created_at")),
                "kind": "workhub.transition",
                "status": next_status,
                "summary": summary,
                "actor_sub": _normalize_text(audit.get("actor_sub")),
                "correlation_id": _normalize_text(audit.get("request_id")),
            }
        )
    for evidence in list(bundle.get("evidence") or []):
        if not isinstance(evidence, dict):
            continue
        payload = evidence.get("evidence_json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        payload = payload if isinstance(payload, dict) else {}
        evidence_type = _normalize_text(evidence.get("evidence_type")) or "generic"
        summary = _normalize_text(payload.get("summary")) or f"evidence attached: {evidence_type}"
        entries.append(
            {
                "timestamp_utc": _normalize_text(evidence.get("created_at")),
                "kind": "workhub.evidence.attached",
                "status": _normalize_text(task_snapshot.get("status")),
                "summary": summary,
                "evidence_type": evidence_type,
                "correlation_id": _normalize_text(evidence.get("correlation_id")),
                "evidence_ref": _normalize_text(payload.get("evidence_ref")),
            }
        )
    verification = dict(bundle.get("verification") or {})
    for comment in list(verification.get("history") or []):
        if not isinstance(comment, dict):
            continue
        metadata = comment.get("metadata_json")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}
        metadata = metadata if isinstance(metadata, dict) else {}
        event_type = _normalize_text(metadata.get("event_type")) or "verification_comment"
        entries.append(
            {
                "timestamp_utc": _normalize_text(comment.get("created_at")),
                "kind": "workhub.verification.comment",
                "status": _normalize_text(task_snapshot.get("status")),
                "summary": _normalize_text(comment.get("body")) or event_type.replace("_", " "),
                "event_type": event_type,
                "author_id": _normalize_text(comment.get("author_id")),
                "correlation_id": _normalize_text(comment.get("correlation_id")),
            }
        )
    for run_bundle in list(bundle.get("runs") or []):
        if not isinstance(run_bundle, dict):
            continue
        run = dict(run_bundle.get("run") or {})
        run_id = _normalize_text(run_bundle.get("run_id")) or _normalize_text(run.get("id"))
        if _normalize_text(run.get("started_at")) or _normalize_text(run.get("created_at")):
            entries.append(
                {
                    "timestamp_utc": _normalize_text(run.get("started_at")) or _normalize_text(run.get("created_at")),
                    "kind": "opsbus.run.started",
                    "status": _normalize_text(run.get("status")) or "running",
                    "summary": "opsbus run started",
                    "run_id": run_id,
                }
            )
        if _normalize_text(run.get("completed_at")) or _normalize_text(run.get("finished_at")):
            entries.append(
                {
                    "timestamp_utc": _normalize_text(run.get("completed_at"))
                    or _normalize_text(run.get("finished_at")),
                    "kind": "opsbus.run.finished",
                    "status": _normalize_text(run.get("status")) or "completed",
                    "summary": "opsbus run finished",
                    "run_id": run_id,
                }
            )
        for step in list(run_bundle.get("steps") or []):
            if not isinstance(step, dict):
                continue
            entries.append(
                {
                    "timestamp_utc": _normalize_text(step.get("created_at"))
                    or _normalize_text(step.get("started_at"))
                    or _normalize_text(step.get("completed_at")),
                    "kind": "opsbus.step",
                    "status": _normalize_text(step.get("status")),
                    "summary": _normalize_text(step.get("step_name"))
                    or _normalize_text(step.get("name"))
                    or "opsbus step recorded",
                    "run_id": run_id,
                    "step_id": _normalize_text(step.get("id")),
                }
            )
    if not records:
        derived_record = _derive_trace_bundle_runner_result_record(
            project_id=project_id,
            task_number=task_number,
            task_trace_bundle=bundle,
        )
        if derived_record:
            records = [derived_record]
    for record in records:
        recorded_at = _normalize_text(record.get("recorded_at_utc"))
        if not recorded_at:
            continue
        failure = dict(record.get("failure") or {})
        refs = dict(record.get("refs") or {})
        temporal = dict(record.get("temporal") or {})
        attestation_authorization = dict(record.get("attestation_authorization") or {})
        status = _normalize_text(record.get("final_status")) or _normalize_text(record.get("status"))
        control_plane = _control_plane_outcome(record)
        reason_code = _normalize_text(control_plane.get("reason_code"))
        reason_summary = _normalize_text(control_plane.get("reason_summary"))
        blocked_reason = _normalize_text(control_plane.get("blocked_reason"))
        readable_summary = _fast_ledger_status_summary(
            status=status,
            failure=failure,
            reason_code=reason_code,
            reason_summary=reason_summary,
            blocked_reason=blocked_reason,
            attestation_authorization=attestation_authorization,
        )
        event = to_compact_run_ledger_record(record)
        item: dict[str, Any] = {
            "timestamp_utc": recorded_at,
            "kind": "runner.result",
            "correlation_id": _normalize_text(record.get("correlation_id")),
            "status": status,
            "summary": readable_summary,
            "minimal_run_outcome": dict(record.get("minimal_run_outcome") or _minimal_run_outcome(record)),
            "event": event,
        }
        if bool(record.get("_derived_from_trace_bundle")):
            item["derived"] = True
            item["derived_source"] = "workhub_trace_bundle"
        if expand:
            item["expanded"] = {
                "failure": {
                    "stage": _normalize_text(failure.get("stage")),
                    "code": _normalize_text(failure.get("code")),
                    "category": _normalize_text(failure.get("category")),
                },
                "control_plane_outcome": {
                    "reason_code": reason_code,
                    "reason_summary": reason_summary,
                    "handoff_status": _normalize_text(control_plane.get("handoff_status")),
                    "failure_category": _normalize_text(control_plane.get("failure_category")),
                    "blocked_reason": blocked_reason,
                    "blocked_at": _normalize_text(control_plane.get("blocked_at")),
                    "remediation": [
                        _normalize_text(item)
                        for item in list(control_plane.get("remediation") or [])
                        if _normalize_text(item)
                    ],
                    "next_best_actions": [
                        _normalize_text(item)
                        for item in list(control_plane.get("next_best_actions") or [])
                        if _normalize_text(item)
                    ],
                },
                "temporal_state": _normalize_text((temporal.get("state_machine") or {}).get("current_state")),
                "trace_ref": _normalize_text(refs.get("outcome_policy_trace_ref")),
                "record": record,
            }
            if bool(record.get("_derived_from_trace_bundle")):
                item["expanded"]["derived"] = {
                    "source": "workhub_trace_bundle",
                    "reason": "canonical run-ledger record unavailable; derived from WorkHub trace bundle",
                }
        entries.append(item)
    entries.sort(
        key=lambda item: (_normalize_text(item.get("timestamp_utc")) or "", _normalize_text(item.get("kind")) or "")
    )
    latest = entries[-1] if entries else None
    return {
        "schema_version": "aix.fast_ledger.v2",
        "record_schema_version": "aix.ledger.core.current",
        "project_id": _normalize_text(project_id),
        "task_number": int(task_number),
        "expanded": bool(expand),
        "entry_count": len(entries),
        "latest": latest,
        "items": entries,
        "trace": {"correlation_ids": correlation_ids},
    }


def _derive_trace_bundle_runner_result_record(
    *,
    project_id: str,
    task_number: int,
    task_trace_bundle: dict[str, Any] | None,
) -> dict[str, Any] | None:
    bundle = dict(task_trace_bundle or {})
    task_snapshot = dict(bundle.get("task") or {})
    task_status = _normalize_text(task_snapshot.get("status"))
    if not task_status:
        return None
    timestamps: list[str] = []
    for value in (task_snapshot.get("updated_at"), task_snapshot.get("created_at")):
        text = _normalize_text(value)
        if text:
            timestamps.append(text)
    for evidence in list(bundle.get("evidence") or []):
        if not isinstance(evidence, dict):
            continue
        text = _normalize_text(evidence.get("created_at"))
        if text:
            timestamps.append(text)
    verification = dict(bundle.get("verification") or {})
    for comment in list(verification.get("history") or []):
        if not isinstance(comment, dict):
            continue
        text = _normalize_text(comment.get("created_at"))
        if text:
            timestamps.append(text)
    for run_bundle in list(bundle.get("runs") or []):
        if not isinstance(run_bundle, dict):
            continue
        run = dict(run_bundle.get("run") or {})
        for value in (
            run.get("completed_at"),
            run.get("finished_at"),
            run.get("started_at"),
            run.get("created_at"),
        ):
            text = _normalize_text(value)
            if text:
                timestamps.append(text)
        for step in list(run_bundle.get("steps") or []):
            if not isinstance(step, dict):
                continue
            for value in (step.get("completed_at"), step.get("started_at"), step.get("created_at")):
                text = _normalize_text(value)
                if text:
                    timestamps.append(text)
    recorded_at = max(timestamps) if timestamps else datetime.now(UTC).isoformat()
    correlation_ids = [
        str(item).strip()
        for item in list((bundle.get("trace") or {}).get("correlation_ids") or [])
        if str(item).strip()
    ]
    latest_correlation_id = correlation_ids[-1] if correlation_ids else None
    evidence_refs: list[str] = []
    for evidence in list(bundle.get("evidence") or []):
        if not isinstance(evidence, dict):
            continue
        payload = evidence.get("evidence_json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        payload = payload if isinstance(payload, dict) else {}
        ref = _normalize_text(payload.get("evidence_ref"))
        if ref and ref not in evidence_refs:
            evidence_refs.append(ref)
        correlation_id = _normalize_text(evidence.get("correlation_id"))
        if correlation_id and correlation_id not in correlation_ids:
            correlation_ids.append(correlation_id)
            latest_correlation_id = correlation_id
    fallback_summary = "canonical runner.result unavailable; derived from WorkHub trace bundle"
    status_ok = task_status in {"done", "needs_review"}
    payload = {
        "ok": status_ok,
        "project_id": _normalize_text(project_id),
        "task_number": int(task_number),
        "task_id": _normalize_text(task_snapshot.get("id")),
        "final_status": task_status,
        "result": {
            "project_id": _normalize_text(project_id),
            "task_id": _normalize_text(task_snapshot.get("id")),
            "task_number": int(task_number),
            "status": "completed" if status_ok else "failed",
            "correlation_id": latest_correlation_id,
            "evidence_refs": evidence_refs,
        },
        "control_plane_outcome": {
            "phase": "historical_trace_fallback",
            "status": task_status,
            "substate": "trace_bundle_fallback",
            "reason_code": None,
            "reason_summary": fallback_summary,
            "handoff_status": task_status,
            "failure_category": None if status_ok else "execution_failed",
            "blocked_reason": fallback_summary if task_status == "blocked" else None,
        },
        "quality_gate": {
            "passed": status_ok,
            "code": "HISTORICAL_TRACE_FALLBACK",
            "message": fallback_summary,
        },
        "diagnostic_outcome": {
            "status": "historical_trace_fallback",
            "reason_code": "HISTORICAL_RUN_LEDGER_MISSING",
            "reason_summary": fallback_summary,
        },
        "writeback": {"status": "completed", "opsbus_written": True, "workhub_written": True},
        "temporal": {
            "enabled": False,
            "state_machine": {
                "state": task_status,
                "events": [],
            },
        },
        "recorded_at_utc": recorded_at,
    }
    record = build_run_ledger_record(payload)
    record["_derived_from_trace_bundle"] = True
    return record


def _fast_ledger_status_summary(
    *,
    status: str | None,
    failure: dict[str, Any],
    reason_code: str | None,
    reason_summary: str | None,
    blocked_reason: str | None,
    attestation_authorization: dict[str, Any] | None = None,
) -> str:
    normalized_status = _normalize_text(status) or "unknown"
    authorization = dict(attestation_authorization or {})
    if authorization and authorization.get("authorized") is False:
        auth_suffix = (
            _normalize_text(authorization.get("reason_summary"))
            or _normalize_text(authorization.get("reason_code"))
            or "attestation authorization failed"
        )
        return f"contract blocked lifecycle: {auth_suffix}"
    normalized_reason = (
        _normalize_text(reason_summary) or _normalize_text(reason_code) or _normalize_text(blocked_reason)
    )
    failure_summary = _normalize_text(failure.get("summary"))
    if normalized_status == "blocked":
        suffix = (
            _normalize_text(reason_code)
            or _normalize_text(blocked_reason)
            or normalized_reason
            or _normalize_text(failure.get("code"))
            or "unknown reason"
        )
        return f"task blocked: {suffix}"
    if normalized_status == "waiting_on_human":
        suffix = normalized_reason or "clarification required"
        return f"waiting on human input: {suffix}"
    if normalized_status == "needs_review":
        suffix = normalized_reason or _normalize_text(failure.get("code")) or "review required"
        return f"awaiting review: {suffix}"
    if normalized_status == "done":
        return "task completed successfully"
    return failure_summary or "runner result recorded"


def list_run_ledger_records(
    *,
    project_id: str | None = None,
    task_number: int | None = None,
    limit: int = 50,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    out_root = Path(root) if root is not None else get_run_ledger_root()
    if not out_root.exists():
        return []
    records: list[dict[str, Any]] = []
    files = [path for path in out_root.glob("*.json") if path.name.lower() != "latest.json"]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if project_id and str(payload.get("project_id") or "").strip() != str(project_id).strip():
            continue
        if task_number is not None and int(payload.get("task_number") or 0) != int(task_number):
            continue
        records.append(payload)
        if len(records) >= max(1, min(int(limit), 500)):
            break
    return records


def build_run_ledger_summary(
    records: list[dict[str, Any]],
    *,
    project_id: str | None = None,
    task_number: int | None = None,
) -> dict[str, Any]:
    category_counts: Counter[str] = Counter()
    code_counts: Counter[str] = Counter()
    stage_counts: Counter[str] = Counter()
    task_numbers: list[int] = []
    latest_record = records[0] if records else None
    latest_failure = None
    success_count = 0
    codeintel_first_total = 0
    codeintel_first_hits = 0
    codeintel_first_fallbacks = 0
    codeintel_fallback_reasons: Counter[str] = Counter()
    codeintel_latency_values: list[float] = []
    codeintel_confidence_buckets: Counter[str] = Counter()

    for record in records:
        failure = dict(record.get("failure") or {})
        category = _normalize_text(failure.get("category")) or "unknown"
        code = _normalize_text(failure.get("code")) or "UNKNOWN"
        stage = _normalize_text(failure.get("stage")) or "unknown"
        category_counts[category] += 1
        code_counts[code] += 1
        stage_counts[stage] += 1
        record_task_number = int(record.get("task_number") or 0)
        if record_task_number > 0:
            task_numbers.append(record_task_number)
        if category == "success":
            success_count += 1
        if latest_failure is None and category != "success":
            latest_failure = {
                "task_number": record_task_number or None,
                "correlation_id": _normalize_text(record.get("correlation_id")),
                "category": category,
                "code": code,
                "summary": _normalize_text(failure.get("summary")),
                "recorded_at_utc": _normalize_text(record.get("recorded_at_utc")),
            }
        codeintel = dict(record.get("codeintel") or {})
        if _normalize_text(codeintel.get("policy_mode")) == "codeintel_first":
            codeintel_first_total += 1
            if _normalize_text(codeintel.get("mode")) == "codeintel_first":
                codeintel_first_hits += 1
            fallback_reason = _normalize_text(codeintel.get("fallback_reason_code"))
            if fallback_reason:
                codeintel_first_fallbacks += 1
                codeintel_fallback_reasons[fallback_reason] += 1
            latency = _normalize_float(codeintel.get("latency_ms"))
            if latency is not None:
                codeintel_latency_values.append(latency)
            confidence = _normalize_float(codeintel.get("confidence_score"))
            if confidence is None:
                codeintel_confidence_buckets["unknown"] += 1
            elif confidence >= 0.85:
                codeintel_confidence_buckets["high"] += 1
            elif confidence >= 0.6:
                codeintel_confidence_buckets["medium"] += 1
            else:
                codeintel_confidence_buckets["low"] += 1

    total_runs = len(records)
    failure_runs = max(total_runs - success_count, 0)
    success_rate = round(success_count / total_runs, 4) if total_runs else 0.0
    codeintel_hit_rate = round(codeintel_first_hits / codeintel_first_total, 4) if codeintel_first_total else 0.0
    codeintel_fallback_rate = (
        round(codeintel_first_fallbacks / codeintel_first_total, 4) if codeintel_first_total else 0.0
    )
    avg_latency = (
        round(sum(codeintel_latency_values) / len(codeintel_latency_values), 2) if codeintel_latency_values else None
    )
    max_latency = round(max(codeintel_latency_values), 2) if codeintel_latency_values else None

    return {
        "schema_version": "aix.run_ledger.summary.current",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "project_id": _normalize_text(project_id) or _normalize_text((latest_record or {}).get("project_id")),
        "task_number": task_number,
        "total_runs": total_runs,
        "success_count": success_count,
        "failure_count": failure_runs,
        "success_rate": success_rate,
        "distinct_task_numbers": sorted(set(task_numbers)),
        "counts": {
            "by_category": dict(category_counts),
            "by_code": dict(code_counts),
            "by_stage": dict(stage_counts),
        },
        "codeintel_first": {
            "runs": codeintel_first_total,
            "hits": codeintel_first_hits,
            "fallbacks": codeintel_first_fallbacks,
            "hit_rate": codeintel_hit_rate,
            "fallback_rate": codeintel_fallback_rate,
            "fallback_reasons": dict(codeintel_fallback_reasons),
            "latency_ms": {
                "avg": avg_latency,
                "max": max_latency,
            },
            "confidence_distribution": dict(codeintel_confidence_buckets),
        },
        "latest_record": (
            {
                "task_number": int((latest_record or {}).get("task_number") or 0) or None,
                "correlation_id": _normalize_text((latest_record or {}).get("correlation_id")),
                "recorded_at_utc": _normalize_text((latest_record or {}).get("recorded_at_utc")),
                "failure": dict((latest_record or {}).get("failure") or {}),
                "planner": dict((latest_record or {}).get("planner") or {}),
            }
            if latest_record
            else None
        ),
        "latest_planner": dict((latest_record or {}).get("planner") or {}) if latest_record else None,
        "latest_failure": latest_failure,
        "records_considered": total_runs,
    }


__all__ = [
    "RUN_LEDGER_ROOT",
    "build_execution_digest",
    "build_task_fast_ledger",
    "build_run_ledger_record",
    "build_run_ledger_summary",
    "classify_run_failure",
    "get_latest_runtime_memory_capsule",
    "list_run_ledger_records",
    "persist_run_ledger_record",
    "_resolve_runlog_root",
]
