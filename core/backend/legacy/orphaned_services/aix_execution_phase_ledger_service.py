from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.services.devexecutor_runlog_resolver_service import get_execution_phase_ledger_root
from hyeve_core.task_execution_phase_contract import (
    ExecutionPhaseRecord,
    can_resume_from_phase,
    next_phase_name,
)

PHASE_LEDGER_ROOT = get_execution_phase_ledger_root()


def _normalize_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _safe_task_number(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _planner_packet_phase_fields(output_payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(output_payload or {})
    status = _normalize_text(payload.get("planner_packet_status"))
    mode = _normalize_text(payload.get("planner_packet_mode"))
    clarification_gaps = payload.get("planner_packet_clarification_gaps")
    if isinstance(clarification_gaps, list):
        normalized_gaps = [str(item).strip() for item in clarification_gaps if str(item).strip()]
    else:
        normalized_gaps = []

    present = payload.get("planner_packet_present")
    if isinstance(present, bool):
        planner_packet_present = present
    elif present is None:
        planner_packet_present = None
    else:
        planner_packet_present = bool(present)

    ready = payload.get("planner_packet_ready")
    if isinstance(ready, bool):
        planner_packet_ready = ready
    elif ready is None:
        planner_packet_ready = None
    else:
        planner_packet_ready = bool(ready)

    return {
        "planner_packet_status": status,
        "planner_packet_clarification_gaps": normalized_gaps if normalized_gaps else None,
        "planner_packet_mode": mode,
        "planner_packet_present": planner_packet_present,
        "planner_packet_ready": planner_packet_ready,
    }


def build_execution_phase_packet_fingerprint(*, admission: dict[str, Any] | None, fallback: str = "") -> str:
    verdict = dict((admission or {}).get("verdict") or {})
    return str(verdict.get("packet_fingerprint") or fallback).strip() or fallback


def build_execution_phase_workspace_fingerprint(
    *,
    workspace_root: Any = None,
    proposal_state: dict[str, Any] | None = None,
) -> str:
    payload = {
        "workspace_root": str(workspace_root or ""),
        "base_commit": str((proposal_state or {}).get("base_commit") or ""),
    }
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()


def build_execution_phase_runtime_fingerprint(
    *,
    repo_key: Any = None,
    task_intent: Any = None,
    explicit_profile_id: Any = None,
    workspace_root: Any = None,
) -> str:
    payload = {
        "repo_key": str(repo_key or ""),
        "task_intent": str(task_intent or ""),
        "explicit_profile_id": str(explicit_profile_id or ""),
        "workspace_root": str(workspace_root or ""),
    }
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    temp_path.replace(path)


def _project_dir(root: Path, project_id: str) -> Path:
    safe_project = project_id.replace("/", "-").replace("\\", "-")
    return root / safe_project


def _task_dir(root: Path, project_id: str, task_number: int) -> Path:
    return _project_dir(root, project_id) / f"task-{int(task_number)}"


def _invocation_dir(root: Path, project_id: str, task_number: int, invocation_id: str) -> Path:
    safe_invocation = invocation_id.replace("/", "-").replace("\\", "-")
    return _task_dir(root, project_id, task_number) / f"invocation-{safe_invocation}"


def build_execution_phase_ledger_record(
    *,
    project_id: str,
    task_number: int,
    invocation_id: str,
    correlation_id: str | None,
    phase_record: ExecutionPhaseRecord,
    task_id: str | None = None,
) -> dict[str, Any]:
    record = {
        "schema_version": "aix.execution_phase_ledger.current",
        "project_id": project_id,
        "task_number": int(task_number),
        "task_id": _normalize_text(task_id),
        "invocation_id": invocation_id,
        "correlation_id": _normalize_text(correlation_id),
        "phase_name": phase_record.phase_name,
        "attempt": int(phase_record.attempt),
        "status": phase_record.status,
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "phase": phase_record.model_dump(mode="json"),
    }
    record.update(_planner_packet_phase_fields(phase_record.output_payload))
    return record


def persist_execution_phase_record(
    *,
    project_id: str,
    task_number: int,
    invocation_id: str,
    correlation_id: str | None,
    phase_record: ExecutionPhaseRecord,
    task_id: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    out_root = Path(root) if root is not None else get_execution_phase_ledger_root()
    record = build_execution_phase_ledger_record(
        project_id=project_id,
        task_number=task_number,
        invocation_id=invocation_id,
        correlation_id=correlation_id,
        phase_record=phase_record,
        task_id=task_id,
    )
    invocation_dir = _invocation_dir(out_root, project_id, task_number, invocation_id)
    file_name = f"{phase_record.phase_name}.attempt-{int(phase_record.attempt)}.json"
    out_path = invocation_dir / file_name
    invocation_latest_path = invocation_dir / "latest.json"
    task_latest_path = _task_dir(out_root, project_id, task_number) / "latest.json"
    _atomic_write_json(out_path, record)
    _atomic_write_json(invocation_latest_path, record)
    _atomic_write_json(task_latest_path, record)
    return {
        "record": record,
        "ref": str(out_path),
        "invocation_latest_ref": str(invocation_latest_path),
        "task_latest_ref": str(task_latest_path),
    }


def list_execution_phase_records(
    *,
    project_id: str,
    task_number: int,
    invocation_id: str | None = None,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    out_root = Path(root) if root is not None else get_execution_phase_ledger_root()
    base_dir = (
        _invocation_dir(out_root, project_id, task_number, invocation_id)
        if _normalize_text(invocation_id)
        else _task_dir(out_root, project_id, task_number)
    )
    if not base_dir.exists():
        return []
    pattern = "*.json" if _normalize_text(invocation_id) else "**/*.json"
    records: list[dict[str, Any]] = []
    for path in sorted(base_dir.glob(pattern)):
        if path.name == "latest.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return sorted(
        records,
        key=lambda item: (
            _normalize_text(item.get("recorded_at_utc")) or "",
            _normalize_text(item.get("phase_name")) or "",
            int(item.get("attempt") or 0),
        ),
    )


def read_latest_execution_phase_record(
    *,
    project_id: str,
    task_number: int,
    invocation_id: str | None = None,
    root: Path | None = None,
) -> dict[str, Any] | None:
    out_root = Path(root) if root is not None else get_execution_phase_ledger_root()
    latest_path = (
        _invocation_dir(out_root, project_id, task_number, invocation_id) / "latest.json"
        if _normalize_text(invocation_id)
        else _task_dir(out_root, project_id, task_number) / "latest.json"
    )
    if not latest_path.exists():
        return None
    try:
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def evaluate_phase_resume_eligibility(
    *,
    project_id: str,
    task_number: int,
    packet_fingerprint: str,
    workspace_fingerprint: str,
    runtime_fingerprint: str,
    invocation_id: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    latest = read_latest_execution_phase_record(
        project_id=project_id,
        task_number=task_number,
        invocation_id=invocation_id,
        root=root,
    )
    if not isinstance(latest, dict):
        return {
            "resumable": False,
            "resume_from_phase": None,
            "next_phase": None,
            "reason_code": "PHASE_RESUME_MISSING_RECORD",
            "summary": "no durable phase record is available to resume from",
        }

    phase_payload = latest.get("phase")
    if not isinstance(phase_payload, dict):
        return {
            "resumable": False,
            "resume_from_phase": None,
            "next_phase": None,
            "reason_code": "PHASE_RESUME_INVALID_RECORD",
            "summary": "latest durable phase record is malformed",
        }

    try:
        phase_record = ExecutionPhaseRecord.model_validate(phase_payload)
    except Exception:
        return {
            "resumable": False,
            "resume_from_phase": None,
            "next_phase": None,
            "reason_code": "PHASE_RESUME_INVALID_RECORD",
            "summary": "latest durable phase record is malformed",
        }

    decision = can_resume_from_phase(
        phase_record,
        packet_fingerprint=packet_fingerprint,
        workspace_fingerprint=workspace_fingerprint,
        runtime_fingerprint=runtime_fingerprint,
    )
    next_phase = next_phase_name(phase_record.phase_name) if decision.allowed else None
    terminal_reason = "PHASE_RESUME_TERMINAL" if decision.allowed and next_phase is None else None
    return {
        "resumable": bool(decision.allowed and next_phase is not None),
        "resume_from_phase": phase_record.phase_name,
        "next_phase": next_phase,
        "reason_code": decision.reason_code if not decision.allowed else terminal_reason,
        "summary": (
            decision.summary
            if decision.summary
            else (
                f"phase {phase_record.phase_name} is terminal; no further durable phase remains"
                if next_phase is None
                else None
            )
        ),
        "latest_status": phase_record.status,
        "latest_attempt": phase_record.attempt,
    }


__all__ = [
    "PHASE_LEDGER_ROOT",
    "build_execution_phase_ledger_record",
    "build_execution_phase_packet_fingerprint",
    "build_execution_phase_runtime_fingerprint",
    "build_execution_phase_workspace_fingerprint",
    "evaluate_phase_resume_eligibility",
    "list_execution_phase_records",
    "persist_execution_phase_record",
    "read_latest_execution_phase_record",
]
