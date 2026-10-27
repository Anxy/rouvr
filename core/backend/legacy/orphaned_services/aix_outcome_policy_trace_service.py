from __future__ import annotations

import asyncio
import hashlib
import json
from concurrent.futures import Future
from threading import Thread
from typing import Any

from backend.clients.ayrpack_client import ayrpack_client
from backend.hyeve_aix_service.config import settings
from backend.services.ayrpack_trace_packet_service import build_outcome_policy_trace_packet_payload
from backend.services.devexecutor_runlog_resolver_service import get_outcome_policy_trace_root
from backend.legacy.orphaned_services.outcome_policy_registry_service import resolve_recovery_policy

LOCAL_TRACE_ROOT = get_outcome_policy_trace_root()
SHADOW_TRACE_ROLE = "debug_forensic_mirror"
SHADOW_TRACE_AUTHORITY = "non_canonical"
CANONICAL_OPERATOR_SOURCES = ["workhub", "run_ledger", "execution_digest", "opsbus_run_closed"]


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _build_outcome_policy_trace_packet_id(
    *,
    project_id: str,
    correlation_id: str,
    task_number: int,
    signal_code: str,
) -> str:
    fingerprint = hashlib.sha1(
        f"{project_id}|{correlation_id}|{task_number}|{signal_code}".encode(),
        usedforsecurity=False,
    ).hexdigest()[:16]
    return f"pkt-outcome-{fingerprint}"


def _raw_signal(
    *,
    quality_gate: dict[str, Any],
    diagnostic_outcome: dict[str, Any],
    planner_diag: dict[str, Any],
    result: Any,
) -> dict[str, Any]:
    error_obj = getattr(result, "error", None)
    result_error = dict(error_obj.to_dict() if error_obj is not None else {})
    if diagnostic_outcome:
        return {
            "source": "diagnostic_outcome",
            "stage": _normalize_text(diagnostic_outcome.get("stage")) or "planning",
            "code": _normalize_text(diagnostic_outcome.get("reason_code")) or "DIAGNOSTIC_BLOCKED",
            "summary": _normalize_text(diagnostic_outcome.get("reason_summary"))
            or "bounded diagnostic outcome blocked mutation",
            "category": _normalize_text(diagnostic_outcome.get("status")) or "diagnostic_blocked",
        }
    if not bool(quality_gate.get("passed", True)):
        return {
            "source": "quality_gate",
            "stage": _normalize_text(quality_gate.get("stage")) or "quality_gate",
            "code": _normalize_text(quality_gate.get("code")) or "QUALITY_GATE_REJECTED",
            "summary": _normalize_text(quality_gate.get("message")) or "quality gate rejected the run",
            "category": "quality_gate_rejected",
        }
    if _normalize_text(result_error.get("code")):
        return {
            "source": "result_error",
            "stage": "execution",
            "code": _normalize_text(result_error.get("code")),
            "summary": _normalize_text(result_error.get("message")) or "execution failed",
            "category": "execution_failure",
        }
    if _normalize_text(planner_diag.get("error")):
        return {
            "source": "planner",
            "stage": "planning",
            "code": _normalize_text(planner_diag.get("error")),
            "summary": _normalize_text(planner_diag.get("error")) or "planner failed",
            "category": "planner_failure",
        }
    return {
        "source": "success",
        "stage": "completed",
        "code": "OK",
        "summary": "run completed successfully",
        "category": "success",
    }


def _normalized_signal(
    *,
    raw_signal: dict[str, Any],
    verifier_outcome: dict[str, Any],
    repair_loop: dict[str, Any],
) -> dict[str, Any]:
    semantic_drift = dict(verifier_outcome.get("semantic_drift") or {})
    if semantic_drift:
        return {
            "source": "verifier.semantic_drift",
            "stage": _normalize_text(semantic_drift.get("stage")) or _normalize_text(raw_signal.get("stage")),
            "category": _normalize_text(semantic_drift.get("category")) or _normalize_text(raw_signal.get("category")),
            "code": _normalize_text(semantic_drift.get("code")) or _normalize_text(raw_signal.get("code")),
            "summary": _normalize_text(semantic_drift.get("summary")) or _normalize_text(raw_signal.get("summary")),
            "outcome_class": _normalize_text(semantic_drift.get("outcome_class")),
            "tool_class": _normalize_text(semantic_drift.get("tool_class")),
            "failure_family": _normalize_text(semantic_drift.get("failure_family"))
            or _normalize_text(repair_loop.get("failure_class")),
        }
    return {
        "source": "raw_signal",
        "stage": _normalize_text(raw_signal.get("stage")),
        "category": _normalize_text(raw_signal.get("category")),
        "code": _normalize_text(raw_signal.get("code")),
        "summary": _normalize_text(raw_signal.get("summary")),
        "outcome_class": "",
        "tool_class": "",
        "failure_family": _normalize_text(repair_loop.get("failure_class")),
    }


async def _shadow_write_outcome_policy_trace_packet(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    packet_id: str,
    packet_payload: dict[str, Any],
    signal_code: str,
    policy_action: str,
) -> tuple[str | None, str | None]:
    packet_response, packet_error = await ayrpack_client.submit_packet(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        packet_payload=packet_payload,
        idempotency_key=packet_id,
    )
    if packet_error:
        return None, packet_error
    persisted_packet_id = (
        ((packet_response or {}).get("data") or {}).get("packet_id") if isinstance(packet_response, dict) else None
    )
    trace_packet_id = str(persisted_packet_id or packet_id)
    _, audit_error = await ayrpack_client.append_audit(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        packet_id=trace_packet_id,
        audit_payload={
            "event_type": "outcome_policy_trace_shadow_write",
            "event_data_json": {
                "packet_id": trace_packet_id,
                "signal_code": signal_code,
                "policy_action": policy_action,
            },
            "actor_id": "aix-v2-runtime",
            "status": "recorded",
        },
    )
    if audit_error:
        return trace_packet_id, f"AYRPACK_AUDIT_{audit_error}"
    return trace_packet_id, None


def _maybe_shadow_write_outcome_policy_trace(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    packet_id: str,
    packet_payload: dict[str, Any],
    signal_code: str,
    policy_action: str,
) -> tuple[str | None, str | None]:
    if not bool(settings.AYRPACK_SHADOW_WRITE_ENABLED):
        return None, "AYRPACK_SHADOW_WRITE_DISABLED"
    if not _normalize_text(settings.AYRPACK_API_TOKEN):
        return None, "AYRPACK_API_TOKEN_MISSING"
    coroutine = _shadow_write_outcome_policy_trace_packet(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        packet_id=packet_id,
        packet_payload=packet_payload,
        signal_code=signal_code,
        policy_action=policy_action,
    )
    try:
        asyncio.get_running_loop()
    except Exception as exc:
        if isinstance(exc, RuntimeError):
            try:
                return asyncio.run(coroutine)
            except Exception as inner_exc:
                return None, str(inner_exc)
        return None, str(exc)
    result: Future[tuple[str | None, str | None]] = Future()

    def _runner() -> None:
        try:
            result.set_result(asyncio.run(coroutine))
        except Exception as exc:  # pragma: no cover
            result.set_exception(exc)

    thread = Thread(target=_runner, name="ayrpack-outcome-policy-shadow-write", daemon=True)
    thread.start()
    thread.join()
    try:
        return result.result()
    except Exception as exc:
        return None, str(exc)


def _persist_outcome_policy_trace_local(
    *,
    packet_id: str,
    payload: dict[str, Any],
) -> tuple[str | None, str | None]:
    try:
        trace_root = get_outcome_policy_trace_root()
        trace_root.mkdir(parents=True, exist_ok=True)
        out_path = trace_root / f"{packet_id}.json"
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        latest_path = trace_root / "latest.json"
        latest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        return str(out_path), None
    except Exception as exc:
        return None, str(exc)


def _shadow_trace_contract() -> dict[str, Any]:
    return {
        "role": SHADOW_TRACE_ROLE,
        "authority": SHADOW_TRACE_AUTHORITY,
        "required_for_control_plane": False,
        "canonical_operator_sources": list(CANONICAL_OPERATOR_SOURCES),
    }


def persist_outcome_policy_trace(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    task_number: int,
    workspace_root: str,
    task_intent: str,
    final_status: str,
    quality_gate: dict[str, Any],
    diagnostic_outcome: dict[str, Any],
    planner_diag: dict[str, Any],
    verifier_outcome: dict[str, Any],
    recovery_submission: dict[str, Any],
    repair_loop: dict[str, Any],
    result: Any,
    actor_id: str = "aix-v2-runtime",
) -> dict[str, Any]:
    raw = _raw_signal(
        quality_gate=quality_gate,
        diagnostic_outcome=diagnostic_outcome,
        planner_diag=planner_diag,
        result=result,
    )
    normalized = _normalized_signal(
        raw_signal=raw,
        verifier_outcome=verifier_outcome,
        repair_loop=repair_loop,
    )
    policy = resolve_recovery_policy(
        code=_normalize_text(normalized.get("code")) or "UNKNOWN",
        workflow_stage=_normalize_text(normalized.get("stage")) or "completed",
        task_intent=task_intent,
        tenant_id=tenant_id,
        repo_profile_id=_normalize_text((getattr(result, "evidence", {}) or {}).get("repo_profile_id")),
        provider=_normalize_text(planner_diag.get("provider")),
        model=_normalize_text(planner_diag.get("model")),
        category=_normalize_text(normalized.get("category")),
        outcome_class=_normalize_text(normalized.get("outcome_class")),
        tool_class=_normalize_text(normalized.get("tool_class")),
        failure_family=_normalize_text(normalized.get("failure_family")),
        quality_message=_normalize_text(normalized.get("summary")),
    )
    decision_payload = {
        "final_status": final_status,
        "result_status": _normalize_text(getattr(result, "status", "")),
        "repair_loop": {
            "failure_code": _normalize_text(repair_loop.get("failure_code")),
            "failure_class": _normalize_text(repair_loop.get("failure_class")),
            "policy_action": _normalize_text(repair_loop.get("policy_action")),
            "same_turn_repair_active": bool(repair_loop.get("same_turn_repair_active")),
            "retryable": bool(repair_loop.get("retryable")),
            "budget_key": _normalize_text(repair_loop.get("budget_key")),
            "attempts_used": int(repair_loop.get("attempts_used") or 0),
            "max_attempts": int(repair_loop.get("max_attempts") or 0),
        },
        "recovery_submission": {
            "submitted": bool(recovery_submission.get("submitted")),
            "reason": _normalize_text(recovery_submission.get("reason")),
            "task_number": int(recovery_submission.get("task_number") or 0) or None,
            "matched_policy": _normalize_text(recovery_submission.get("matched_policy")),
        },
        "assist_loop": dict((getattr(result, "evidence", {}) or {}).get("assist_loop") or {}),
    }
    packet_id = _build_outcome_policy_trace_packet_id(
        project_id=project_id,
        correlation_id=correlation_id,
        task_number=task_number,
        signal_code=_normalize_text(normalized.get("code")) or "UNKNOWN",
    )
    packet_payload = build_outcome_policy_trace_packet_payload(
        packet_id=packet_id,
        project_id=project_id,
        conversation_id=str(task_number),
        correlation_id=correlation_id,
        actor_id=actor_id,
        workspace_root=workspace_root,
        task_number=task_number,
        task_intent=task_intent,
        final_status=final_status,
        raw_signal=raw,
        normalized_signal=normalized,
        classified_policy=policy,
        decision_payload=decision_payload,
    )
    trace_packet_id, trace_error = _maybe_shadow_write_outcome_policy_trace(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        packet_id=packet_id,
        packet_payload=packet_payload,
        signal_code=_normalize_text(normalized.get("code")) or "UNKNOWN",
        policy_action=_normalize_text(policy.get("policy_action")),
    )
    local_trace_ref, local_trace_error = _persist_outcome_policy_trace_local(
        packet_id=packet_id,
        payload=packet_payload,
    )
    return {
        "packet_id": trace_packet_id or packet_id,
        "trace_ref": f"ayrpack://packet/{trace_packet_id}" if trace_packet_id else None,
        "trace_shadow_write_error": trace_error,
        "trace_shadow_role": SHADOW_TRACE_ROLE,
        "trace_shadow_authority": SHADOW_TRACE_AUTHORITY,
        "trace_shadow_required_for_control_plane": False,
        "canonical_operator_sources": list(CANONICAL_OPERATOR_SOURCES),
        "local_trace_ref": local_trace_ref,
        "local_trace_error": local_trace_error,
        "shadow_trace_contract": _shadow_trace_contract(),
        "raw_signal": raw,
        "normalized_signal": normalized,
        "classified_policy": policy,
        "decision": decision_payload,
    }


__all__ = ["LOCAL_TRACE_ROOT", "persist_outcome_policy_trace"]
