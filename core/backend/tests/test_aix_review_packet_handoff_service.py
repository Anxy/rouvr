from __future__ import annotations

from copy import deepcopy

from backend.services.aix_evidence_bundle_v2_service import build_signed_evidence_bundle_v2
from backend.services.aix_review_packet_handoff_service import (
    build_review_packet_from_evidence_bundle_v2,
    evaluate_human_review_handoff_decision,
    validate_review_packet_handoff,
)


def _signed_bundle() -> dict:
    return build_signed_evidence_bundle_v2(
        tenant_id="tenant-a",
        actor_id="aix-orchestrator-v2",
        project_id="project-a",
        task_id="task-1",
        run_id="run-1",
        task_payload={
            "workspace_ref": {"workspace_id": "workspace-a", "repo_ref": "repo-a"},
            "metadata": {"task_kind": "docs_only_change", "risk_level": "low"},
        },
        execution={"status": "completed", "changed_files": ["docs/current/example.md"], "run_result": {}},
        verification={"classification": "success", "checks": [{"name": "validation", "passed": True}]},
        read_contract={"active_contract_id": "contract-1", "active_contract_hash": "sha256:contract"},
        evidence_bundle={
            "bundle_id": "aix-evidence-run-1",
            "artifact_refs": [{"kind": "diff", "sha256": "sha256:diff"}],
        },
        created_at="2026-07-23T00:00:00Z",
    )


def _review_packet() -> dict:
    return build_review_packet_from_evidence_bundle_v2(
        evidence_bundle_v2=_signed_bundle(),
        project_id="project-a",
        run_id="run-1",
        review_packet_version="packet-v1",
        created_at="2026-07-23T00:00:00Z",
    )


def test_review_packet_handoff_renders_from_signed_evidence_bundle_v2() -> None:
    packet = _review_packet()

    assert packet["schema_version"] == "aix.review_packet.workhub_handoff.v1"
    assert packet["version"] == "packet-v1"
    assert packet["review_state"] == "review_pending"
    assert packet["summary"]["accepted"] is False
    assert packet["workhub_render_contract"]["workhub_lifecycle_owner"] is True
    assert packet["workhub_render_contract"]["aix_lifecycle_mutation_allowed"] is False
    assert packet["authority_limits"]["executor_can_approve"] is False
    assert validate_review_packet_handoff(packet)["passed"] is True


def test_review_packet_handoff_validation_rejects_auto_accept_or_lifecycle_mutation() -> None:
    packet = _review_packet()
    packet["summary"]["accepted"] = True
    packet["authority_limits"]["executor_can_approve"] = True
    packet["workhub_render_contract"]["aix_lifecycle_mutation_allowed"] = True

    result = validate_review_packet_handoff(packet)

    assert result["status"] == "blocked"
    codes = {item["code"] for item in result["failures"]}
    assert "REVIEW_PACKET_AUTO_ACCEPT_FORBIDDEN" in codes
    assert "REVIEW_PACKET_AUTHORITY_ESCALATION" in codes
    assert "REVIEW_PACKET_WORKHUB_LIFECYCLE_MUTATION_FORBIDDEN" in codes


def test_human_review_handoff_decision_maps_all_allowed_decisions_without_workhub_mutation() -> None:
    packet = _review_packet()

    decisions = {
        decision: evaluate_human_review_handoff_decision(
            review_packet=packet,
            decision=decision,
            human_actor_id="human-1",
            actor_type="human",
        )
        for decision in ("approve", "reject", "request_changes")
    }

    assert decisions["approve"]["review_state"] == "review_accepted"
    assert decisions["reject"]["review_state"] == "review_rejected"
    assert decisions["request_changes"]["review_state"] == "changes_requested"
    assert all(item["workhub_lifecycle_mutation"] is False for item in decisions.values())
    assert all(item["evidence_is_acceptance"] is False for item in decisions.values())


def test_human_review_handoff_decision_rejects_executor_actor() -> None:
    result = evaluate_human_review_handoff_decision(
        review_packet=_review_packet(),
        decision="approve",
        human_actor_id="executor-1",
        actor_type="executor",
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "HUMAN_REVIEW_REQUIRED"
    assert result["review_state"] == "review_pending"


def test_review_packet_blocks_tampered_evidence_signature() -> None:
    bundle = _signed_bundle()
    tampered = deepcopy(bundle)
    tampered["task_id"] = "task-tampered"

    packet = build_review_packet_from_evidence_bundle_v2(
        evidence_bundle_v2=tampered,
        project_id="project-a",
        run_id="run-1",
        review_packet_version="packet-v1",
    )
    result = validate_review_packet_handoff(packet)

    assert packet["evidence_signature_status"] == "blocked"
    assert result["status"] == "blocked"
    assert result["failures"][0]["code"] == "REVIEW_PACKET_SIGNED_EVIDENCE_REQUIRED"
