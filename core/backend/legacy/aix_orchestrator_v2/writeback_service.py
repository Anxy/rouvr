from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.aix_orchestrator_v2.models import WritebackResult
from backend.aix_orchestrator_v2.workhub_client import WorkHubTaskClient
from backend.services.aix_evidence_bundle_v2_service import build_signed_evidence_bundle_v2


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _build_machine_proof_refs(*, run_id: str, execution: dict[str, Any]) -> list[str]:
    refs = [str(item).strip() for item in list(execution.get("diff_refs") or []) if str(item).strip()]
    summary_ref = f"aix://orchestrator-v2/runs/{run_id}"
    if summary_ref not in refs:
        refs.append(summary_ref)
    return refs


def _sha256_ref(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _artifact_ref(*, ref: str, kind: str, payload: Any) -> dict[str, Any]:
    text = str(ref or "").strip() or f"aix://orchestrator-v2/{kind}"
    return {
        "ref": text,
        "kind": kind,
        "sha256": _sha256_ref({"ref": text, "kind": kind, "payload": payload}),
        "size_bytes": max(1, len(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))),
    }


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _active_contract(task_payload: dict[str, Any], read_contract: dict[str, Any] | None = None) -> dict[str, str]:
    contract = _as_dict(task_payload.get("active_execution_contract"))
    read = _as_dict(read_contract)
    contract_id = str(
        read.get("active_contract_id")
        or read.get("contract_id")
        or contract.get("contract_id")
        or contract.get("id")
        or ""
    ).strip()
    contract_hash = str(
        read.get("active_contract_hash") or read.get("contract_hash") or contract.get("contract_hash") or ""
    ).strip()
    return {"contract_id": contract_id, "contract_hash": contract_hash}


def _build_aix_evidence_bundle(
    *,
    tenant_id: str,
    actor_id: str,
    project_id: str,
    task_id: str,
    run_id: str,
    execution: dict[str, Any],
    verification: dict[str, Any],
    read_contract: dict[str, Any],
    evidence_ref: str,
    machine_proof_refs: list[str],
    correlation_id: str,
) -> dict[str, Any]:
    contract = _active_contract({"active_execution_contract": {}}, read_contract)
    return {
        "bundle_id": f"aix-evidence-{run_id}",
        "schema_version": "workhub.evidence_bundle",
        "tenant_id": tenant_id,
        "project_id": project_id,
        "task_id": task_id,
        "contract_id": contract["contract_id"],
        "contract_hash": contract["contract_hash"],
        "run_id": run_id,
        "actor_sub": actor_id,
        "correlation_id": correlation_id,
        "evidence_kind": "implementation_result",
        "created_at": _utc_timestamp(),
        "artifact_refs": [
            _artifact_ref(
                ref=next(iter(machine_proof_refs), evidence_ref), kind="diff", payload=execution.get("diff_refs")
            ),
            _artifact_ref(ref=evidence_ref, kind="transcript", payload={"run_id": run_id, "execution": execution}),
            _artifact_ref(ref=evidence_ref, kind="validation_output", payload=verification.get("checks")),
            _artifact_ref(ref=evidence_ref, kind="verifier_output", payload=verification),
        ],
    }


def _build_aix_verification_record(
    *,
    tenant_id: str,
    actor_id: str,
    project_id: str,
    task_id: str,
    run_id: str,
    verification: dict[str, Any],
    read_contract: dict[str, Any],
    evidence_bundle_id: str,
    correlation_id: str,
) -> dict[str, Any]:
    contract = _active_contract({"active_execution_contract": {}}, read_contract)
    classification = str(verification.get("classification") or "").strip()
    return {
        "verification_id": f"aix-verification-{run_id}",
        "schema_version": "workhub.verification_record",
        "tenant_id": tenant_id,
        "project_id": project_id,
        "task_id": task_id,
        "contract_id": contract["contract_id"],
        "contract_hash": contract["contract_hash"],
        "run_id": run_id,
        "evidence_bundle_id": evidence_bundle_id,
        "verifier_actor_sub": actor_id,
        "verification_result_hash": _sha256_ref(verification),
        "classification": "passed" if classification == "success" else "failed",
        "correlation_id": correlation_id,
        "created_at": _utc_timestamp(),
    }


def _build_aix_closeout_request(
    *,
    run_id: str,
    verification: dict[str, Any],
    read_contract: dict[str, Any],
    evidence_bundle_id: str,
    verification_id: str,
    verification_result_hash: str,
) -> dict[str, Any]:
    contract = _active_contract({"active_execution_contract": {}}, read_contract)
    return {
        "closeout_id": f"aix-closeout-{run_id}",
        "contract_id": contract["contract_id"],
        "contract_hash": contract["contract_hash"],
        "run_id": run_id,
        "evidence_bundle_id": evidence_bundle_id,
        "verification_id": verification_id,
        "verification_result_hash": verification_result_hash,
        "decision": "completed",
        "reason": str(verification.get("summary") or "AIX Orchestrator v2 successful closeout").strip(),
    }


async def _writeback_v2_adapter_run(
    *,
    client: WorkHubTaskClient,
    tenant_id: str,
    actor_id: str,
    project_id: str,
    task_payload: dict[str, Any],
    run_id: str,
    execution: dict[str, Any],
    verification: dict[str, Any],
    writeback: dict[str, Any],
) -> dict[str, Any]:
    task_id = str(task_payload.get("id") or task_payload.get("task_id") or "").strip()
    if not task_id:
        return WritebackResult(
            writeback=writeback,
            verification_error="workhub_task_id_missing",
            transition_ok=False,
            transition_error="workhub_task_id_missing",
            evidence_ok=False,
            evidence_error="workhub_task_id_missing",
        ).model_dump(mode="python")

    read_ok, read_error, read_contract = await client.get_aix_adapter_read_contract(
        project_id=project_id,
        task_id=task_id,
        tenant_id=tenant_id,
        correlation_id=f"aix-v2-read-contract-{run_id}-{uuid4().hex[:8]}",
    )
    if not read_ok or not read_contract:
        return WritebackResult(
            writeback=writeback,
            verification_error=read_error or "workhub_read_contract_missing",
            transition_ok=False,
            transition_error=read_error or "workhub_read_contract_missing",
            evidence_ok=False,
            evidence_error=read_error or "workhub_read_contract_missing",
        ).model_dump(mode="python")

    evidence_ref = f"aix://orchestrator-v2/runs/{run_id}"
    machine_proof_refs = _build_machine_proof_refs(run_id=run_id, execution=execution)
    evidence_correlation_id = f"aix-v2-evidence-{run_id}-{uuid4().hex[:8]}"
    evidence_bundle = _build_aix_evidence_bundle(
        tenant_id=tenant_id,
        actor_id=actor_id,
        project_id=project_id,
        task_id=task_id,
        run_id=run_id,
        execution=execution,
        verification=verification,
        read_contract=read_contract,
        evidence_ref=evidence_ref,
        machine_proof_refs=machine_proof_refs,
        correlation_id=evidence_correlation_id,
    )
    evidence_bundle["evidence_bundle_v2"] = build_signed_evidence_bundle_v2(
        tenant_id=tenant_id,
        actor_id=actor_id,
        project_id=project_id,
        task_id=task_id,
        run_id=run_id,
        task_payload=task_payload,
        execution=execution,
        verification=verification,
        read_contract=read_contract,
        evidence_bundle=evidence_bundle,
    )
    evidence_ok, evidence_error, evidence_data = await client.attach_aix_adapter_evidence(
        project_id=project_id,
        task_id=task_id,
        evidence_bundle=evidence_bundle,
        idempotency_key=f"aix-evidence-{run_id}",
        tenant_id=tenant_id,
        correlation_id=evidence_correlation_id,
    )
    if not evidence_ok:
        return WritebackResult(
            writeback=writeback,
            verification_error=None,
            transition_ok=False,
            transition_error=evidence_error,
            evidence_ok=False,
            evidence_error=evidence_error,
        ).model_dump(mode="python")

    verification_eval = None
    verification_error = None
    closeout_operation = None
    closeout_error = None
    transition_ok = False
    transition_error = None
    classification = str(verification.get("classification") or "").strip()
    if classification in {"success", "failed"}:
        verification_record = _build_aix_verification_record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            project_id=project_id,
            task_id=task_id,
            run_id=run_id,
            verification=verification,
            read_contract=read_contract,
            evidence_bundle_id=str((evidence_data or evidence_bundle).get("bundle_id") or evidence_bundle["bundle_id"]),
            correlation_id=f"aix-v2-verification-{run_id}-{uuid4().hex[:8]}",
        )
        verification_ok, verification_error, verification_eval = await client.create_aix_adapter_verification(
            project_id=project_id,
            task_id=task_id,
            verification_record=verification_record,
            idempotency_key=f"aix-verification-{run_id}",
            tenant_id=tenant_id,
            correlation_id=str(verification_record["correlation_id"]),
        )
        if not verification_ok:
            transition_error = verification_error
        elif classification == "success":
            closeout_request = _build_aix_closeout_request(
                run_id=run_id,
                verification=verification,
                read_contract=read_contract,
                evidence_bundle_id=str(verification_record["evidence_bundle_id"]),
                verification_id=str(verification_record["verification_id"]),
                verification_result_hash=str(verification_record["verification_result_hash"]),
            )
            closeout_ok, closeout_error, closeout_operation = await client.closeout_aix_adapter_task(
                project_id=project_id,
                task_id=task_id,
                closeout_request=closeout_request,
                idempotency_key=f"aix-closeout-{run_id}",
                tenant_id=tenant_id,
                correlation_id=f"aix-v2-closeout-{run_id}-{uuid4().hex[:8]}",
            )
            transition_ok = bool(closeout_ok)
            transition_error = None if closeout_ok else closeout_error
    if classification != "success":
        terminal_state = "needs_review"
        transition_ok, transition_error, _transition_data = await client.transition_aix_adapter_task(
            project_id=project_id,
            task_id=task_id,
            transition_request={
                "to_state": terminal_state,
                "reason_code": f"aix_{classification or 'unknown'}",
                "reason_summary": f"AIX Orchestrator v2 {classification or 'unknown'}",
            },
            idempotency_key=f"aix-transition-{run_id}",
            tenant_id=tenant_id,
            correlation_id=f"aix-v2-transition-{run_id}-{uuid4().hex[:8]}",
        )
    return WritebackResult(
        writeback=writeback,
        verification_evaluation=verification_eval,
        verification_error=verification_error,
        closeout_operation=closeout_operation,
        closeout_error=closeout_error,
        transition_ok=transition_ok,
        transition_error=transition_error,
        evidence_ok=True,
        evidence_error=None,
    ).model_dump(mode="python")


def _execution_not_attempted(*, execution_status: str, run_result_payload: dict[str, Any]) -> bool:
    if execution_status == "skipped":
        return True
    if not run_result_payload:
        return True
    if run_result_payload.get("executable") is False:
        return True
    return False


def _skipped_writeback_payload(*, reason: str = "execution_not_attempted") -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": reason,
        "workhub_written": False,
        "opsbus_written": False,
        "retry_enqueued": False,
        "retry_event_ref": None,
        "knowledge_written": False,
        "knowledge_duplicate": False,
        "knowledge_ref": None,
        "knowledge_error": None,
        "workhub_error": None,
        "opsbus_error": None,
        "retry_error": None,
        "analytics_event_written": False,
        "analytics_event_error": None,
    }


async def writeback_v2_run(
    *,
    client: WorkHubTaskClient,
    tenant_id: str,
    actor_id: str,
    project_id: str,
    task_payload: dict[str, Any],
    run_id: str,
    execution: dict[str, Any],
    verification: dict[str, Any],
) -> dict[str, Any]:
    from backend.shared.monorepo_imports import ensure_hyeve_core_import_path

    ensure_hyeve_core_import_path()
    from backend.services.aix_enveloped_run_service import RunResult
    from backend.legacy.orphaned_services.aix_writeback_service import writeback_run_result

    run_result_payload = _as_dict(execution.get("run_result"))
    execution_status = str(execution.get("status") or "").strip().lower()
    if _execution_not_attempted(execution_status=execution_status, run_result_payload=run_result_payload):
        writeback = _skipped_writeback_payload()
    else:
        result = RunResult.model_validate(run_result_payload)
        writeback = (
            await writeback_run_result(
                result,
                tenant_id=tenant_id,
                actor_id=actor_id,
                include_opsbus=True,
            )
        ).to_dict()
    return await _writeback_v2_adapter_run(
        client=client,
        tenant_id=tenant_id,
        actor_id=actor_id,
        project_id=project_id,
        task_payload=task_payload,
        run_id=run_id,
        execution=execution,
        verification=verification,
        writeback=writeback,
    )
