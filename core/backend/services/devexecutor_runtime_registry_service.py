from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from backend.services.aix_run_ledger_service import get_latest_runtime_memory_capsule
from backend.services.devexecutor_runtime_path_service import resolve_runtime_workspace_mapping
from backend.services.devexecutor_tool_loop_service import execute_tool_loop
from backend.services.repo_profile_resolver_service import resolve_effective_repo_profile

TERMINAL_STATES = {"completed", "failed", "cancelled"}
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DevExecutorRunRecord:
    run_id: str
    project_id: str
    task_number: int
    correlation_id: str
    idempotency_key: str | None
    workspace_root: str
    repo_key: str
    profile_id: str | None
    requested_steps: list[dict[str, Any]]
    created_at_utc: str
    token_budget: int | None = None
    resume_run_id: str | None = None
    resume_depth: int = 0
    status: str = "queued"
    started_at_utc: str | None = None
    finished_at_utc: str | None = None
    cancelled_at_utc: str | None = None
    transcript: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    replayed: bool = False


_RUNS: dict[str, DevExecutorRunRecord] = {}
_IDEMPOTENCY: dict[tuple[str, str], str] = {}
_LOCK = Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create_run(
    *,
    project_id: str,
    task_number: int,
    correlation_id: str,
    idempotency_key: str | None,
    workspace_root: str,
    repo_key: str,
    profile_id: str | None,
    steps: list[dict[str, Any]],
    token_budget: int | None = None,
    resume_run_id: str | None = None,
) -> DevExecutorRunRecord:
    idem = (project_id, idempotency_key) if idempotency_key else None
    with _LOCK:
        if idem and idem in _IDEMPOTENCY:
            existing = _RUNS[_IDEMPOTENCY[idem]]
            existing.replayed = True
            return existing

        run_id = f"devrun-{uuid4().hex[:12]}"
        resume_depth = 0
        if resume_run_id is not None:
            parent = _RUNS.get(resume_run_id)
            if parent is None:
                raise KeyError(f"RESUME_RUN_NOT_FOUND: {resume_run_id}")
            resume_depth = int(parent.resume_depth) + 1
            if resume_depth > 3:
                raise RuntimeError("RESUME_CHAIN_LIMIT_EXCEEDED max_depth=3")
        record = DevExecutorRunRecord(
            run_id=run_id,
            project_id=project_id,
            task_number=task_number,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            workspace_root=workspace_root,
            repo_key=repo_key,
            profile_id=profile_id,
            requested_steps=list(steps),
            created_at_utc=_now(),
            token_budget=token_budget,
            resume_run_id=resume_run_id,
            resume_depth=resume_depth,
        )
        _RUNS[run_id] = record
        if idem:
            _IDEMPOTENCY[idem] = run_id
        return record


def get_run(*, run_id: str) -> DevExecutorRunRecord | None:
    with _LOCK:
        return _RUNS.get(run_id)


def get_resumable_steps(*, run_id: str) -> list[dict[str, Any]] | None:
    run = get_run(run_id=run_id)
    if run is None:
        return None
    outcomes = summarize_requested_step_outcomes(run=run)
    remaining: list[dict[str, Any]] = []
    for step, outcome in zip(run.requested_steps, outcomes, strict=False):
        if outcome["step_status"] != "ok":
            remaining.append(dict(step))
    return remaining


def build_resume_context(*, run: DevExecutorRunRecord) -> dict[str, Any]:
    if not run.resume_run_id:
        return {}
    parent = get_run(run_id=run.resume_run_id)
    if parent is None:
        return {
            "resume_run_id": run.resume_run_id,
            "resume_depth": int(run.resume_depth),
            "parent_found": False,
        }
    resumable_steps = get_resumable_steps(run_id=parent.run_id) or []
    inherited_ok_steps = len(parent.requested_steps) - len(resumable_steps)
    return {
        "resume_run_id": parent.run_id,
        "resume_depth": int(run.resume_depth),
        "parent_found": True,
        "parent_task_number": int(parent.task_number),
        "parent_correlation_id": str(parent.correlation_id or "").strip() or None,
        "resume_supported": True,
        "inherited_successful_step_count": max(inherited_ok_steps, 0),
        "remaining_resumable_step_count": len(resumable_steps),
    }


def list_runs(
    *,
    project_id: str | None = None,
    task_number: int | None = None,
) -> list[DevExecutorRunRecord]:
    with _LOCK:
        runs = list(_RUNS.values())
    if project_id is not None:
        runs = [r for r in runs if r.project_id == project_id]
    if task_number is not None:
        runs = [r for r in runs if r.task_number == task_number]
    runs.sort(key=lambda r: r.created_at_utc, reverse=True)
    return runs


def cancel_run(*, run_id: str, reason: str | None = None) -> DevExecutorRunRecord:
    with _LOCK:
        run = _RUNS[run_id]
        if run.status in TERMINAL_STATES:
            return run
        run.status = "cancelled"
        run.cancelled_at_utc = _now()
        run.finished_at_utc = run.cancelled_at_utc
        run.diagnostics["cancel_reason"] = reason or "cancelled_by_operator"
        return run


def execute_run(
    *,
    run_id: str,
    profiles_dir: str | Path,
    max_runtime_seconds: float,
    max_patch_attempts: int,
    command_runtime: dict[str, Any] | None = None,
    mutation_runtime: dict[str, Any] | None = None,
    heartbeat_hook: Any = None,
) -> DevExecutorRunRecord:
    with _LOCK:
        run = _RUNS[run_id]
        if run.status in TERMINAL_STATES:
            return run
        run.status = "running"
        run.started_at_utc = _now()

    profile, profile_diag = resolve_effective_repo_profile(
        repo_key=run.repo_key,
        explicit_profile_id=run.profile_id,
        profiles_dir=profiles_dir,
    )
    dirty_checkout = _detect_dirty_host_checkout(workspace_root=run.workspace_root)
    if dirty_checkout is not None:
        transcript = [
            {
                "index": 0,
                "tool": "workspace_preflight",
                "ok": False,
                "error": dirty_checkout["message"],
                "result": dirty_checkout,
            }
        ]
        with _LOCK:
            run = _RUNS[run_id]
            if run.status != "cancelled":
                run.transcript = transcript
                run.status = "failed"
                run.finished_at_utc = _now()
                run.diagnostics["repo_profile_resolver"] = profile_diag
                run.diagnostics["resolved_profile_id"] = profile.profile_id
                run.diagnostics["workspace_preflight"] = dirty_checkout
            return run
    allow_prefixes = [list(item) for item in profile.command_policy.allow_prefixes]

    if not run.requested_steps:
        transcript = [
            {
                "index": 0,
                "tool": "planner",
                "ok": False,
                "error": "EMPTY_EXECUTION_PLAN",
            }
        ]
        with _LOCK:
            run = _RUNS[run_id]
            if run.status != "cancelled":
                run.transcript = transcript
                run.status = "failed"
                run.finished_at_utc = _now()
                run.diagnostics["repo_profile_resolver"] = profile_diag
                run.diagnostics["resolved_profile_id"] = profile.profile_id
            return run

    command_runtime_payload = _enrich_runtime_payload(
        base=command_runtime,
        run=run,
        runtime_kind="command",
    )
    mutation_runtime_payload = _enrich_runtime_payload(
        base=mutation_runtime,
        run=run,
        runtime_kind="mutation",
    )
    runtime_contract = _build_runtime_contract_diagnostics(
        workspace_root=run.workspace_root,
        command_runtime=command_runtime_payload,
        mutation_runtime=mutation_runtime_payload,
    )

    transcript_prefix = _resume_transcript_prefix(run=run)
    if heartbeat_hook is not None:
        try:
            heartbeat_hook(
                {
                    "stage": "devexecutor_dispatch",
                    "summary": f"dispatching devexecutor run {run.run_id}",
                    "details": {
                        "run_id": run.run_id,
                        "step_count": len(run.requested_steps),
                    },
                    "timestamp_utc": _now(),
                }
            )
        except Exception:
            pass
    transcript = execute_tool_loop(
        workspace_root=run.workspace_root,
        steps=run.requested_steps,
        allow_prefixes=allow_prefixes,
        max_runtime_seconds=max_runtime_seconds,
        max_patch_attempts=max_patch_attempts,
        token_budget=run.token_budget,
        heartbeat_hook=heartbeat_hook,
        command_runtime=command_runtime_payload,
        mutation_runtime=mutation_runtime_payload,
    )
    if transcript_prefix:
        transcript = [*transcript_prefix, *transcript]
    _annotate_requested_step_statuses(transcript=transcript, requested_steps=run.requested_steps)
    terminal = "completed"
    if transcript and transcript[-1].get("ok") is False:
        terminal = "failed"

    with _LOCK:
        run = _RUNS[run_id]
        if run.status != "cancelled":
            run.transcript = transcript
            run.status = terminal
            run.finished_at_utc = _now()
            run.diagnostics["repo_profile_resolver"] = profile_diag
            run.diagnostics["resolved_profile_id"] = profile.profile_id
            run.diagnostics["runtime_contract"] = runtime_contract
        return run


def _detect_dirty_host_checkout(*, workspace_root: str) -> dict[str, Any] | None:
    root = Path(str(workspace_root or "").strip())
    if not (root / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )
    except Exception as exc:
        return {
            "code": "WORKSPACE_DIRTY_CHECK_FAILED",
            "reason_code": "WORKSPACE_DIRTY_CHECK_FAILED",
            "message": f"workspace dirty check failed: {exc}",
            "workspace_root": str(root),
        }
    if proc.returncode != 0:
        return {
            "code": "WORKSPACE_DIRTY_CHECK_FAILED",
            "reason_code": "WORKSPACE_DIRTY_CHECK_FAILED",
            "message": (proc.stderr or proc.stdout or "workspace dirty check failed").strip(),
            "workspace_root": str(root),
        }
    dirty_lines = [line.rstrip() for line in (proc.stdout or "").splitlines() if line.strip()]
    if not dirty_lines:
        return None
    return {
        "code": "HOST_CHECKOUT_DIRTY_FOR_EXECUTION",
        "reason_code": "HOST_CHECKOUT_DIRTY_FOR_EXECUTION",
        "message": "host checkout contains leaked mutable state; execution refused until workspace is clean",
        "workspace_root": str(root),
        "dirty_paths": [line[3:].strip() if len(line) > 3 else line for line in dirty_lines][:20],
    }


def summarize_requested_step_outcomes(*, run: DevExecutorRunRecord) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for index, step in enumerate(run.requested_steps):
        outcome = {"step": index, "tool": str(step.get("tool") or "unknown"), "step_status": "pending"}
        matching = [item for item in run.transcript if isinstance(item, dict) and int(item.get("index", -1)) == index]
        if not matching:
            outcomes.append(outcome)
            continue
        if any(
            str((item.get("result") or {}).get("code") or "") == "ALREADY_APPLIED"
            for item in matching
            if isinstance(item.get("result"), dict)
        ):
            outcome["step_status"] = "skipped"
        elif any(bool(item.get("ok")) is False for item in matching):
            outcome["step_status"] = "failed"
        elif any(bool(item.get("ok")) is True for item in matching):
            outcome["step_status"] = "ok"
        outcomes.append(outcome)
    return outcomes


def _annotate_requested_step_statuses(
    *, transcript: list[dict[str, Any]], requested_steps: list[dict[str, Any]]
) -> None:
    for index, step in enumerate(requested_steps):
        matching = [item for item in transcript if isinstance(item, dict) and int(item.get("index", -1)) == index]
        if not matching:
            continue
        step_status = "pending"
        if any(
            str((item.get("result") or {}).get("code") or "") == "ALREADY_APPLIED"
            for item in matching
            if isinstance(item.get("result"), dict)
        ):
            step_status = "skipped"
        elif any(bool(item.get("ok")) is False for item in matching):
            step_status = "failed"
        elif any(bool(item.get("ok")) is True for item in matching):
            step_status = "ok"
        for item in matching:
            item["step_status"] = step_status
            item.setdefault("requested_tool", str(step.get("tool") or "unknown"))


def _resume_transcript_prefix(*, run: DevExecutorRunRecord) -> list[dict[str, Any]]:
    if not run.resume_run_id:
        return []
    parent = get_run(run_id=run.resume_run_id)
    if parent is None:
        return []
    prefix: list[dict[str, Any]] = []
    for item in parent.transcript:
        if not isinstance(item, dict):
            continue
        if bool(item.get("ok")) is False:
            break
        prefix.append(dict(item))
    return prefix


def _enrich_runtime_payload(
    *,
    base: dict[str, Any] | None,
    run: DevExecutorRunRecord,
    runtime_kind: str,
) -> dict[str, Any] | None:
    logger.info("PLANNER_SPAWN_ENTERED task=%s slice=%s", run.task_number, runtime_kind)
    if base is None:
        return None
    payload = dict(base)
    runtime_capsule = get_latest_runtime_memory_capsule(project_id=run.project_id, task_number=run.task_number)
    if isinstance(runtime_capsule, dict) and runtime_capsule:
        payload["persona_instruction_pack"] = [
            "<capsule>\n" + json.dumps(runtime_capsule, indent=2, ensure_ascii=True) + "\n</capsule>"
        ]
        capsule_slice_id = (
            str((runtime_capsule.get("run_ledger_facts") or {}).get("slice_id") or "").strip() or "unknown"
        )
        logger.info("Capsule injected for task %s (slice %s)", run.task_number, capsule_slice_id)
        logger.info(
            "CAPSULE_INJECTED task=%s capsule_id=%s first_200_chars=%s",
            run.task_number,
            runtime_capsule.get("capsule_id"),
            str(runtime_capsule)[:200],
        )
    labels = dict(payload.get("labels") or {})
    labels.setdefault("hyeve.runtime.kind", runtime_kind)
    labels.setdefault("hyeve.run.id", run.run_id)
    labels.setdefault("hyeve.project.id", run.project_id)
    labels.setdefault("hyeve.task.number", str(run.task_number))
    labels.setdefault("hyeve.repo.key", run.repo_key)
    payload["labels"] = labels
    return payload


def _build_runtime_contract_diagnostics(
    *,
    workspace_root: str,
    command_runtime: dict[str, Any] | None,
    mutation_runtime: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "workspace_root": str(workspace_root),
        "command_runtime": _summarize_runtime_payload(workspace_root=workspace_root, runtime=command_runtime),
        "mutation_runtime": _summarize_runtime_payload(workspace_root=workspace_root, runtime=mutation_runtime),
    }


def _summarize_runtime_payload(*, workspace_root: str, runtime: dict[str, Any] | None) -> dict[str, Any] | None:
    if runtime is None:
        return None
    payload = dict(runtime)
    summary: dict[str, Any] = {
        "mode": str(payload.get("mode") or "").strip().lower() or "host",
        "container_required": bool(payload.get("container_required")),
        "image": str(payload.get("image") or "").strip() or None,
        "network": str(payload.get("network") or "").strip() or None,
        "read_only_rootfs": bool(payload.get("read_only_rootfs")),
        "cpus": str(payload.get("cpus") or "").strip() or None,
        "memory": str(payload.get("memory") or "").strip() or None,
        "pids_limit": payload.get("pids_limit"),
    }
    if summary["mode"] == "docker":
        mapping = resolve_runtime_workspace_mapping(workspace_root=workspace_root, runtime=payload)
        summary["workspace_mapping"] = mapping
    return summary
