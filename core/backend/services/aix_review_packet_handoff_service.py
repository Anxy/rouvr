from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from backend.services.aix_evidence_bundle_v2_service import verify_signed_evidence_bundle_v2


MVP_REVIEW_DECISION_TO_STATE = {
    "approve": "review_accepted",
    "reject": "review_rejected",
    "request_changes": "changes_requested",
}

FORBIDDEN_REVIEW_ACTOR_TYPES = {"executor", "service", "automated", "persona", "model", "tool"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_review_packet_from_evidence_bundle_v2(
    *,
    evidence_bundle_v2: dict[str, Any],
    project_id: str,
    run_id: str,
    review_packet_version: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    signature_result = verify_signed_evidence_bundle_v2(evidence_bundle_v2)
    packet_version = review_packet_version or str(evidence_bundle_v2.get("evidence_bundle_id") or run_id)
    review_packet = {
        "schema_version": "aix.review_packet.workhub_handoff.v1",
        "review_packet_id": f"aix-review-packet://{run_id}",
        "version": packet_version,
        "evidence_bundle_id": str(evidence_bundle_v2.get("evidence_bundle_id") or ""),
        "evidence_signature_status": signature_result["status"],
        "tenant_id": str(evidence_bundle_v2.get("tenant_id") or ""),
        "workspace_id": str(evidence_bundle_v2.get("workspace_id") or ""),
        "repo_ref": str(evidence_bundle_v2.get("repo_ref") or ""),
        "project_id": project_id,
        "task_id": str(evidence_bundle_v2.get("task_id") or ""),
        "run_id": run_id,
        "task_kind": str(evidence_bundle_v2.get("task_kind") or ""),
        "created_at": created_at or _utc_now(),
        "review_state": "review_pending",
        "workhub_render_contract": {
            "renderer": "WorkHub",
            "workhub_lifecycle_owner": True,
            "aix_lifecycle_mutation_allowed": False,
        },
        "summary": {
            "result": "evidence_ready" if signature_result["passed"] else "evidence_blocked",
            "recommendation": "ready_for_human_review" if signature_result["passed"] else "block_until_evidence_valid",
            "accepted": False,
        },
        "evidence_links": {
            "evidence_bundle_id": str(evidence_bundle_v2.get("evidence_bundle_id") or ""),
            "integrity_hash": str(_as_dict(evidence_bundle_v2.get("integrity")).get("deterministic_integrity_hash") or ""),
            "signature": str(_as_dict(evidence_bundle_v2.get("integrity")).get("signature") or ""),
            "diff_hash": str(_as_dict(evidence_bundle_v2.get("diff_proof")).get("diff_hash") or ""),
            "validation_status": str(_as_dict(evidence_bundle_v2.get("validation_proof")).get("status") or ""),
            "replay_status": str(_as_dict(evidence_bundle_v2.get("replay_proof")).get("status") or ""),
        },
        "rollback_reference": {
            "required": True,
            "production_mutation": bool(_as_dict(evidence_bundle_v2.get("rollback_proof")).get("production_mutation")),
            "rollback_path": "proposal_workspace_reset_to_start_commit",
        },
        "audit": {
            "actor": "AIX",
            "action": "review_packet_generated",
            "correlation_id": f"aix-review-packet-{run_id}",
        },
        "authority_limits": {
            "auto_accept": False,
            "executor_can_approve": False,
            "reviewer_persona_can_approve": False,
            "aix_mutates_workhub_lifecycle": False,
        },
    }
    review_packet["packet_hash"] = _hash(review_packet)
    return review_packet


def validate_review_packet_handoff(review_packet: dict[str, Any]) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    authority = _as_dict(review_packet.get("authority_limits"))
    render = _as_dict(review_packet.get("workhub_render_contract"))
    summary = _as_dict(review_packet.get("summary"))
    if summary.get("accepted") is True:
        failures.append({"code": "REVIEW_PACKET_AUTO_ACCEPT_FORBIDDEN"})
    if authority.get("auto_accept") is True or authority.get("executor_can_approve") is True:
        failures.append({"code": "REVIEW_PACKET_AUTHORITY_ESCALATION"})
    if authority.get("aix_mutates_workhub_lifecycle") is True or render.get("aix_lifecycle_mutation_allowed") is True:
        failures.append({"code": "REVIEW_PACKET_WORKHUB_LIFECYCLE_MUTATION_FORBIDDEN"})
    if str(review_packet.get("evidence_signature_status") or "") != "passed":
        failures.append({"code": "REVIEW_PACKET_SIGNED_EVIDENCE_REQUIRED"})
    return {
        "schema": "aix.review_packet.workhub_handoff_validation.v1",
        "status": "passed" if not failures else "blocked",
        "passed": not failures,
        "failures": failures,
    }


def evaluate_human_review_handoff_decision(
    *,
    review_packet: dict[str, Any],
    decision: str,
    human_actor_id: str,
    actor_type: str = "human",
) -> dict[str, Any]:
    validation = validate_review_packet_handoff(review_packet)
    if not validation["passed"]:
        return {
            "schema": "aix.review_packet.human_decision_evaluation.v1",
            "status": "blocked",
            "reason_code": validation["failures"][0]["code"],
            "review_state": "review_pending",
            "workhub_lifecycle_mutation": False,
            "failures": validation["failures"],
        }
    normalized_decision = str(decision or "").strip()
    normalized_actor_type = str(actor_type or "").strip().lower()
    failures: list[dict[str, str]] = []
    if normalized_decision not in MVP_REVIEW_DECISION_TO_STATE:
        failures.append({"code": "REVIEW_DECISION_UNSUPPORTED"})
    if not str(human_actor_id or "").strip() or normalized_actor_type != "human":
        failures.append({"code": "HUMAN_REVIEW_REQUIRED"})
    if normalized_actor_type in FORBIDDEN_REVIEW_ACTOR_TYPES:
        failures.append({"code": "HUMAN_REVIEW_REQUIRED"})
    review_state = MVP_REVIEW_DECISION_TO_STATE.get(normalized_decision, "review_pending")
    return {
        "schema": "aix.review_packet.human_decision_evaluation.v1",
        "status": "passed" if not failures else "blocked",
        "reason_code": "HUMAN_REVIEW_DECISION_RECORDED" if not failures else failures[0]["code"],
        "decision": normalized_decision,
        "review_state": review_state if not failures else "review_pending",
        "human_actor_id": human_actor_id,
        "workhub_lifecycle_mutation": False,
        "evidence_is_acceptance": False,
        "failures": failures,
    }
