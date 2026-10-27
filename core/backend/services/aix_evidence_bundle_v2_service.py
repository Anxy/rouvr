from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any


EVIDENCE_BUNDLE_V2_SCHEMA = "aix.evidence_bundle.v2"
EVIDENCE_BUNDLE_V2_SIGNATURE_PREFIX = "aix-signature-v1:"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _unsigned_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    unsigned = dict(bundle)
    integrity = dict(_as_dict(unsigned.get("integrity")))
    integrity.pop("deterministic_integrity_hash", None)
    integrity.pop("signature", None)
    unsigned["integrity"] = integrity
    return unsigned


def _signature(*, bundle_id: str, tenant_id: str, run_id: str, integrity_hash: str) -> str:
    signature_input = {
        "bundle_id": bundle_id,
        "tenant_id": tenant_id,
        "run_id": run_id,
        "integrity_hash": integrity_hash,
    }
    return EVIDENCE_BUNDLE_V2_SIGNATURE_PREFIX + hashlib.sha256(
        _canonical_json(signature_input).encode("utf-8")
    ).hexdigest()


def _artifact_hashes(evidence_bundle: dict[str, Any], execution: dict[str, Any], verification: dict[str, Any]) -> dict:
    refs = [item for item in _as_list(evidence_bundle.get("artifact_refs")) if isinstance(item, dict)]
    hashes: dict[str, str] = {
        "workhub_evidence_bundle": _sha256(evidence_bundle),
        "execution": _sha256(execution),
        "verification": _sha256(verification),
    }
    for ref in refs:
        kind = str(ref.get("kind") or "artifact").strip() or "artifact"
        sha = str(ref.get("sha256") or "").strip()
        if sha:
            hashes[f"artifact_{kind}"] = sha
    return hashes


def _replay_proof_from_execution(execution: dict[str, Any]) -> dict[str, Any]:
    replay = _as_dict(execution.get("clean_replay")) or _as_dict(_as_dict(execution.get("run_result")).get("clean_replay"))
    if not replay:
        return {
            "required": True,
            "status": "pending_clean_replay",
            "clean_sandbox_verified": False,
            "replay_mismatch_blocks_promotion": True,
            "auto_accept": False,
            "workhub_lifecycle_mutation": False,
        }
    return {
        "required": True,
        "status": str(replay.get("status") or "unknown"),
        "clean_sandbox_verified": bool(replay.get("clean_sandbox_verified")),
        "replay_data_hash": str(replay.get("replay_data_hash") or ""),
        "mismatch_count": int(replay.get("mismatch_count") or 0),
        "replay_mismatch_blocks_promotion": True,
        "auto_accept": False,
        "workhub_lifecycle_mutation": bool(replay.get("workhub_lifecycle_mutation")),
    }


def build_signed_evidence_bundle_v2(
    *,
    tenant_id: str,
    actor_id: str,
    project_id: str,
    task_id: str,
    run_id: str,
    task_payload: dict[str, Any],
    execution: dict[str, Any],
    verification: dict[str, Any],
    read_contract: dict[str, Any],
    evidence_bundle: dict[str, Any],
    created_at: str | None = None,
) -> dict[str, Any]:
    created = created_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    expires = (datetime.fromisoformat(created.replace("Z", "+00:00")) + timedelta(days=30)).isoformat().replace(
        "+00:00", "Z"
    )
    artifact_hashes = _artifact_hashes(evidence_bundle, execution, verification)
    diff_artifact_hash = artifact_hashes.get("artifact_diff")
    validation_hash = artifact_hashes.get("artifact_validation_output") or _sha256(_as_list(verification.get("checks")))
    classification = str(verification.get("classification") or "").strip()
    changed_files = [str(item).strip() for item in _as_list(execution.get("changed_files")) if str(item).strip()]
    bundle_id = f"evidence-bundle-v2://{run_id}"
    bundle: dict[str, Any] = {
        "schema": EVIDENCE_BUNDLE_V2_SCHEMA,
        "evidence_bundle_id": bundle_id,
        "task_id": task_id,
        "tenant_id": tenant_id,
        "workspace_id": str(_as_dict(task_payload.get("workspace_ref")).get("workspace_id") or run_id),
        "repo_ref": str(_as_dict(task_payload.get("workspace_ref")).get("repo_ref") or project_id),
        "task_kind": str(_as_dict(task_payload.get("metadata")).get("task_kind") or "single_file_helper"),
        "risk_level": str(_as_dict(task_payload.get("metadata")).get("risk_level") or "low"),
        "created_at": created,
        "diff_proof": {
            "required": bool(changed_files),
            "status": "captured" if changed_files else "not_required_or_no_diff",
            "changed_files": changed_files,
            "diff_hash": diff_artifact_hash,
        },
        "validation_proof": {
            "status": "passed" if classification == "success" else "failed",
            "classification": classification,
            "checks": _as_list(verification.get("checks")),
            "validation_hash": validation_hash,
        },
        "executor_proof": {
            "actor_sub": actor_id,
            "run_id": run_id,
            "proposal_workspace_only": True,
            "workhub_api_calls": 0,
            "production_mutation": False,
        },
        "provider_proof": {
            "tenant_scoped_route_required": True,
            "direct_provider_call": False,
        },
        "replay_proof": _replay_proof_from_execution(execution),
        "review_proof": {
            "review_pending_is_not_acceptance": True,
            "auto_accept": False,
        },
        "cost_proof": {
            "cost_unknown_not_treated_as_pass": True,
            "provider_cost_usd": _as_dict(_as_dict(execution.get("run_result")).get("cost")).get("provider_cost_usd"),
        },
        "rollback_proof": {
            "production_mutation": False,
            "proposal_workspace_only": True,
            "rollback_required_for_production": False,
        },
        "artifact_hashes": artifact_hashes,
        "contract": {
            "project_id": project_id,
            "contract_id": str(read_contract.get("active_contract_id") or read_contract.get("contract_id") or ""),
            "contract_hash": str(read_contract.get("active_contract_hash") or read_contract.get("contract_hash") or ""),
        },
        "integrity": {
            "algorithm": "sha256",
            "deterministic_integrity_hash": "",
            "signature": "",
        },
        "ttl": {"expires_at": expires, "expired": False},
        "status": "complete" if classification == "success" else "rejected",
    }
    integrity_hash = _sha256(_unsigned_bundle(bundle))
    bundle["integrity"]["deterministic_integrity_hash"] = integrity_hash
    bundle["integrity"]["signature"] = _signature(
        bundle_id=bundle_id,
        tenant_id=tenant_id,
        run_id=run_id,
        integrity_hash=integrity_hash,
    )
    return bundle


def verify_signed_evidence_bundle_v2(bundle: dict[str, Any]) -> dict[str, Any]:
    integrity = _as_dict(bundle.get("integrity"))
    expected_hash = _sha256(_unsigned_bundle(bundle))
    actual_hash = str(integrity.get("deterministic_integrity_hash") or "")
    expected_signature = _signature(
        bundle_id=str(bundle.get("evidence_bundle_id") or ""),
        tenant_id=str(bundle.get("tenant_id") or ""),
        run_id=str(_as_dict(bundle.get("executor_proof")).get("run_id") or ""),
        integrity_hash=expected_hash,
    )
    actual_signature = str(integrity.get("signature") or "")
    failures: list[dict[str, str]] = []
    if actual_hash != expected_hash:
        failures.append({"code": "EVIDENCE_BUNDLE_V2_INTEGRITY_HASH_MISMATCH"})
    if actual_signature != expected_signature:
        failures.append({"code": "EVIDENCE_BUNDLE_V2_SIGNATURE_INVALID"})
    return {
        "schema": "aix.evidence_bundle_v2.signature_verification.v1",
        "status": "passed" if not failures else "blocked",
        "passed": not failures,
        "failures": failures,
        "expected_integrity_hash": expected_hash,
    }


def evaluate_clean_replay(
    *,
    evidence_bundle_v2: dict[str, Any],
    replay_material: dict[str, Any],
    tenant_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    signature_result = verify_signed_evidence_bundle_v2(evidence_bundle_v2)
    if not bool(signature_result.get("passed")):
        failures.extend(signature_result["failures"])
    if str(evidence_bundle_v2.get("tenant_id") or "") != tenant_id:
        failures.append({"code": "REPLAY_TENANT_MISMATCH"})
    if str(evidence_bundle_v2.get("workspace_id") or "") != workspace_id:
        failures.append({"code": "REPLAY_WORKSPACE_MISMATCH"})
    if not bool(replay_material.get("clean_sandbox")):
        failures.append({"code": "REPLAY_CLEAN_SANDBOX_REQUIRED"})
    if bool(replay_material.get("workhub_lifecycle_mutation")):
        failures.append({"code": "REPLAY_WORKHUB_LIFECYCLE_MUTATION_FORBIDDEN"})
    if bool(replay_material.get("auto_accept")):
        failures.append({"code": "REPLAY_AUTO_ACCEPT_FORBIDDEN"})
    expected_diff_hash = str(_as_dict(evidence_bundle_v2.get("diff_proof")).get("diff_hash") or "")
    actual_diff_hash = str(replay_material.get("diff_hash") or "")
    if expected_diff_hash and actual_diff_hash and expected_diff_hash != actual_diff_hash:
        failures.append({"code": "REPLAY_DIFF_HASH_MISMATCH"})
    expected_validation_hash = str(_as_dict(evidence_bundle_v2.get("validation_proof")).get("validation_hash") or "")
    actual_validation_hash = str(replay_material.get("validation_hash") or "")
    if expected_validation_hash and actual_validation_hash and expected_validation_hash != actual_validation_hash:
        failures.append({"code": "REPLAY_VALIDATION_HASH_MISMATCH"})
    return {
        "schema": "aix.evidence_bundle_v2.clean_replay_evaluation.v1",
        "status": "passed" if not failures else "blocked",
        "passed": not failures,
        "reason_code": "CLEAN_REPLAY_VERIFIED" if not failures else failures[0]["code"],
        "failures": failures,
        "replay_data_hash": _sha256(replay_material),
    }
