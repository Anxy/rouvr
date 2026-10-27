from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from backend.models.evidence_pack_models import validate_evidence_pack
from backend.services.aix_run_ledger_service import get_latest_runtime_memory_capsule

logger = logging.getLogger(__name__)


class VerifierHandoffError(ValueError):
    pass


def _summarize_transcript(transcript: list[dict[str, Any]]) -> dict[str, Any]:
    failures = [item for item in transcript if not bool(item.get("ok", False))]
    tools_used = sorted({str(item.get("tool") or "unknown") for item in transcript})
    return {
        "steps_total": len(transcript),
        "failed_steps": len(failures),
        "tools_used": tools_used,
    }


def build_verifier_handoff_payload(
    *,
    project_id: str,
    source_task_number: int | None,
    coder_correlation_id: str,
    coder_result: dict[str, Any],
    verifier_persona_id: str = "verifier.current",
) -> dict[str, Any]:
    logger.info("PLANNER_SPAWN_ENTERED task=%s slice=%s", source_task_number or "unknown", "verifier")
    final_state = str(coder_result.get("final_state") or "")
    if final_state != "ready_for_human":
        raise VerifierHandoffError(
            f"VERIFIER_HANDOFF_PRECONDITION_FAILED expected_final_state=ready_for_human actual={final_state!r}"
        )
    evidence_payload = coder_result.get("evidence_pack")
    if not isinstance(evidence_payload, dict):
        raise VerifierHandoffError("VERIFIER_HANDOFF_PRECONDITION_FAILED missing evidence_pack payload")
    evidence_pack = validate_evidence_pack(evidence_payload)
    completion = dict(coder_result.get("completion") or {})
    transcript = list(coder_result.get("transcript") or [])
    transitions = list(coder_result.get("transitions") or [])
    runtime_capsule = get_latest_runtime_memory_capsule(project_id=project_id, task_number=source_task_number)

    handoff_id = uuid.uuid4().hex
    verifier_correlation_id = f"{coder_correlation_id}:verifier:{handoff_id[:8]}"
    payload = {
        "handoff_id": handoff_id,
        "project_id": project_id,
        "source": {
            "task_number": source_task_number,
            "correlation_id": coder_correlation_id,
            "final_state": final_state,
            "completion": completion,
        },
        "verifier_job": {
            "persona_id": verifier_persona_id,
            "correlation_id": verifier_correlation_id,
            "status": "queued",
            "input_contract": "coder_handoff.current",
        },
        "attachments": {
            "evidence_pack": evidence_pack.model_dump(mode="python"),
            "transitions": transitions,
            "transcript_summary": _summarize_transcript(transcript),
        },
    }
    if isinstance(runtime_capsule, dict) and runtime_capsule:
        payload["verifier_job"]["instruction_pack"] = [
            "<capsule>\n" + json.dumps(runtime_capsule, indent=2, ensure_ascii=True) + "\n</capsule>"
        ]
        capsule_slice_id = (
            str((runtime_capsule.get("run_ledger_facts") or {}).get("slice_id") or "").strip() or "unknown"
        )
        logger.info("Capsule injected for task %s (slice %s)", source_task_number or "unknown", capsule_slice_id)
        logger.info(
            "CAPSULE_INJECTED task=%s capsule_id=%s first_200_chars=%s",
            source_task_number or "unknown",
            runtime_capsule.get("capsule_id"),
            str(runtime_capsule)[:200],
        )
    return payload


def build_verifier_handoff_packet_current(
    *,
    project_id: str,
    tenant_id: str,
    source_task_number: int | None,
    coder_correlation_id: str,
    coder_result: dict[str, Any],
    sender_agent_id: str = "slavx-coder",
    sender_provider: str = "slavx",
    sender_model: str = "llama",
) -> dict[str, Any]:
    """Emit canonical AIX execution packet for coder -> verifier handoff."""
    payload = build_verifier_handoff_payload(
        project_id=project_id,
        source_task_number=source_task_number,
        coder_correlation_id=coder_correlation_id,
        coder_result=coder_result,
    )
    summary = payload["attachments"]["transcript_summary"]
    completion = payload["source"]["completion"]
    evidence_pack = payload["attachments"]["evidence_pack"]
    evidence_refs: list[str] = []
    for item in evidence_pack.get("artifacts", []):
        path_value = item.get("path")
        if isinstance(path_value, str) and path_value.strip():
            evidence_refs.append(path_value)
    if not evidence_refs:
        evidence_refs = [f"codex://handoff/{payload['handoff_id']}/evidence_pack.json"]

    return {
        "schema_name": "aix_execution_packet_current",
        "schema_version": "current",
        "packet_id": f"pkt-{payload['handoff_id']}",
        "correlation_id": coder_correlation_id,
        "causation_id": payload["source"].get("correlation_id"),
        "tenant_id": tenant_id,
        "project_id": project_id,
        "idempotency_key": f"handoff:{payload['handoff_id']}",
        "priority": "high",
        "sender": {
            "agent_id": sender_agent_id,
            "role": "coder",
            "provider": sender_provider,
            "model": sender_model,
        },
        "target": {"role": "verifier"},
        "step": {
            "index": max(int(summary.get("steps_total") or 1), 1),
            "status": "completed",
            "summary": (
                f"coder handoff prepared; final_state={payload['source']['final_state']}; "
                f"failed_steps={summary.get('failed_steps', 0)}; outcome={completion.get('outcome', 'unknown')}"
            ),
            "evidence_refs": evidence_refs,
        },
    }
