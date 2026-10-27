from __future__ import annotations

from copy import deepcopy

from backend.services.aix_evidence_bundle_v2_service import (
    EVIDENCE_BUNDLE_V2_SIGNATURE_PREFIX,
    build_signed_evidence_bundle_v2,
    evaluate_clean_replay,
    verify_signed_evidence_bundle_v2,
)


def _bundle() -> dict:
    return build_signed_evidence_bundle_v2(
        tenant_id="tenant-a",
        actor_id="aix-orchestrator-v2",
        project_id="project-a",
        task_id="task-1",
        run_id="run-1",
        task_payload={
            "workspace_ref": {"workspace_id": "workspace-1", "repo_ref": "repo://project-a/main"},
            "metadata": {"task_kind": "single_file_helper", "risk_level": "low"},
        },
        execution={
            "status": "completed",
            "changed_files": ["backend/example.py"],
            "run_result": {"status": "completed"},
            "clean_replay": {
                "status": "passed",
                "clean_sandbox_verified": True,
                "replay_data_hash": "sha256:replay",
                "mismatch_count": 0,
                "workhub_lifecycle_mutation": False,
            },
        },
        verification={
            "classification": "success",
            "summary": "passed",
            "checks": [{"name": "declared_validation", "passed": True}],
        },
        read_contract={"active_contract_id": "contract-1", "active_contract_hash": "sha256:contract"},
        evidence_bundle={
            "bundle_id": "aix-evidence-run-1",
            "artifact_refs": [
                {"kind": "diff", "sha256": "sha256:diff"},
                {"kind": "validation_output", "sha256": "sha256:validation"},
            ],
        },
        created_at="2026-07-23T00:00:00Z",
    )


def test_signed_evidence_bundle_v2_has_deterministic_signature() -> None:
    bundle = _bundle()

    assert bundle["schema"] == "aix.evidence_bundle.v2"
    assert bundle["integrity"]["deterministic_integrity_hash"].startswith("sha256:")
    assert bundle["integrity"]["signature"].startswith(EVIDENCE_BUNDLE_V2_SIGNATURE_PREFIX)
    assert verify_signed_evidence_bundle_v2(bundle)["passed"] is True


def test_signed_evidence_bundle_v2_verification_rejects_tampering() -> None:
    bundle = _bundle()
    tampered = deepcopy(bundle)
    tampered["diff_proof"]["changed_files"].append("backend/other.py")

    result = verify_signed_evidence_bundle_v2(tampered)

    assert result["status"] == "blocked"
    codes = {item["code"] for item in result["failures"]}
    assert "EVIDENCE_BUNDLE_V2_INTEGRITY_HASH_MISMATCH" in codes
    assert "EVIDENCE_BUNDLE_V2_SIGNATURE_INVALID" in codes


def test_clean_replay_passes_for_matching_clean_sandbox_material() -> None:
    bundle = _bundle()
    result = evaluate_clean_replay(
        evidence_bundle_v2=bundle,
        tenant_id="tenant-a",
        workspace_id="workspace-1",
        replay_material={
            "clean_sandbox": True,
            "workhub_lifecycle_mutation": False,
            "auto_accept": False,
            "diff_hash": "sha256:diff",
            "validation_hash": "sha256:validation",
        },
    )

    assert result["status"] == "passed"
    assert result["reason_code"] == "CLEAN_REPLAY_VERIFIED"


def test_clean_replay_blocks_mismatch_and_boundary_violations() -> None:
    bundle = _bundle()
    result = evaluate_clean_replay(
        evidence_bundle_v2=bundle,
        tenant_id="tenant-b",
        workspace_id="workspace-2",
        replay_material={
            "clean_sandbox": False,
            "workhub_lifecycle_mutation": True,
            "auto_accept": True,
            "diff_hash": "sha256:other-diff",
            "validation_hash": "sha256:other-validation",
        },
    )

    assert result["status"] == "blocked"
    codes = [item["code"] for item in result["failures"]]
    assert "REPLAY_TENANT_MISMATCH" in codes
    assert "REPLAY_WORKSPACE_MISMATCH" in codes
    assert "REPLAY_CLEAN_SANDBOX_REQUIRED" in codes
    assert "REPLAY_WORKHUB_LIFECYCLE_MUTATION_FORBIDDEN" in codes
    assert "REPLAY_AUTO_ACCEPT_FORBIDDEN" in codes
    assert "REPLAY_DIFF_HASH_MISMATCH" in codes
    assert "REPLAY_VALIDATION_HASH_MISMATCH" in codes
