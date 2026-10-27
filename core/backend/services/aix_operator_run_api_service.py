from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.services.aix_run_ledger_service import to_compact_run_ledger_record
from backend.services.devexecutor_runlog_resolver_service import get_run_ledger_root


class OperatorRunApiError(Exception):
    def __init__(self, *, failure_class: str, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.failure_class = failure_class
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class OperatorRunReadResult:
    status: str
    payload: dict[str, Any]


def list_operator_runs(*, tenant_id: str, limit: int = 100, root: Path | None = None) -> OperatorRunReadResult:
    tenant = _require_tenant(tenant_id)
    records = _read_records(root=root)
    visible = [_to_operator_run_list_item(record) for record in records if _record_tenant(record) == tenant]
    visible.sort(key=lambda item: str(item.get("updated_at") or item.get("started_at") or ""), reverse=True)
    bounded_limit = max(1, min(int(limit or 100), 500))
    return OperatorRunReadResult(
        status="ok",
        payload={
            "success": True,
            "data": {
                "schema_version": "aix.operator_runs.list.v1",
                "authority": _authority(),
                "items": visible[:bounded_limit],
                "total": len(visible),
                "limit": bounded_limit,
                "has_more": len(visible) > bounded_limit,
            },
        },
    )


def get_operator_run_detail(*, tenant_id: str, run_id: str, root: Path | None = None) -> OperatorRunReadResult:
    tenant = _require_tenant(tenant_id)
    wanted_run_id = str(run_id or "").strip()
    if not wanted_run_id:
        raise OperatorRunApiError(
            failure_class="CONTRACT_VIOLATION",
            reason="RUN_ID_REQUIRED",
            detail="run_id is required.",
        )
    records = _read_records(root=root)
    for record in records:
        if _record_run_id(record) != wanted_run_id:
            continue
        if _record_tenant(record) != tenant:
            return OperatorRunReadResult(status="not_found", payload=_not_found_payload(wanted_run_id))
        return OperatorRunReadResult(
            status="ok",
            payload={
                "success": True,
                "data": _to_operator_run_detail(record),
            },
        )
    return OperatorRunReadResult(status="not_found", payload=_not_found_payload(wanted_run_id))


def get_operator_run_evidence_bundle(*, tenant_id: str, run_id: str, root: Path | None = None) -> OperatorRunReadResult:
    result = _get_visible_record(tenant_id=tenant_id, run_id=run_id, root=root)
    if result.status != "ok":
        return result
    record = result.payload["record"]
    return OperatorRunReadResult(
        status="ok",
        payload={
            "success": True,
            "data": {
                "schema_version": "aix.operator_run.evidence_bundle.v1",
                "authority": _authority(),
                "run_id": _record_run_id(record),
                "tenant_id": _record_tenant(record),
                "evidence_bundle": _evidence_bundle_view(record),
            },
        },
    )


def get_operator_run_replay_comparison(
    *, tenant_id: str, run_id: str, root: Path | None = None
) -> OperatorRunReadResult:
    result = _get_visible_record(tenant_id=tenant_id, run_id=run_id, root=root)
    if result.status != "ok":
        return result
    replay_record = result.payload["record"]
    records = _read_records(root=root)
    original_run_id = _text(replay_record.get("replay_of_run_id")) or _text(
        (replay_record.get("replay") or {}).get("original_run_id")
    )
    original_record = None
    if original_run_id:
        for record in records:
            if _record_run_id(record) == original_run_id and _record_tenant(record) == _record_tenant(replay_record):
                original_record = record
                break
    comparison = _replay_comparison_view(original_record=original_record, replay_record=replay_record)
    return OperatorRunReadResult(
        status="ok",
        payload={
            "success": True,
            "data": {
                "schema_version": "aix.operator_run.replay_comparison.v1",
                "authority": _authority(),
                **comparison,
            },
        },
    )


def get_operator_run_replay_by_evidence_id(
    *, tenant_id: str, evidence_id: str, root: Path | None = None
) -> OperatorRunReadResult:
    tenant = _require_tenant(tenant_id)
    wanted_evidence_id = _text(evidence_id)
    if not wanted_evidence_id:
        raise OperatorRunApiError(
            failure_class="CONTRACT_VIOLATION",
            reason="EVIDENCE_ID_REQUIRED",
            detail="evidence_id is required.",
        )
    records = _read_records(root=root)
    tenant_matches = [
        record
        for record in records
        if _record_tenant(record) == tenant and wanted_evidence_id in _record_evidence_ids(record)
    ]
    if not tenant_matches:
        return OperatorRunReadResult(status="not_found", payload=_evidence_not_found_payload(wanted_evidence_id))
    unique_matches = {_record_run_id(record): record for record in tenant_matches}
    if len(unique_matches) > 1:
        raise OperatorRunApiError(
            failure_class="CONTRACT_VIOLATION",
            reason="EVIDENCE_ID_AMBIGUOUS",
            detail="evidence_id resolves to more than one run for the requested tenant.",
        )
    replay_record = next(iter(unique_matches.values()))
    original_run_id = _text(replay_record.get("replay_of_run_id")) or _text(
        (replay_record.get("replay") or {}).get("original_run_id")
    )
    if not original_run_id:
        raise OperatorRunApiError(
            failure_class="CONTRACT_VIOLATION",
            reason="EVIDENCE_NOT_REPLAYABLE",
            detail="evidence_id does not reference a replay run record.",
        )
    original_record = None
    for record in records:
        if _record_tenant(record) == tenant and _record_run_id(record) == original_run_id:
            original_record = record
            break
    comparison = _replay_comparison_view(original_record=original_record, replay_record=replay_record)
    return OperatorRunReadResult(
        status="ok",
        payload={
            "success": True,
            "data": {
                "schema_version": "aix.operator_run.evidence_replay.v1",
                "authority": _authority(),
                "evidence_id": wanted_evidence_id,
                "replay_mode": "read_only_comparison",
                "executed": False,
                **comparison,
            },
        },
    )


def _get_visible_record(*, tenant_id: str, run_id: str, root: Path | None) -> OperatorRunReadResult:
    tenant = _require_tenant(tenant_id)
    wanted_run_id = str(run_id or "").strip()
    if not wanted_run_id:
        raise OperatorRunApiError(
            failure_class="CONTRACT_VIOLATION",
            reason="RUN_ID_REQUIRED",
            detail="run_id is required.",
        )
    records = _read_records(root=root)
    for record in records:
        if _record_run_id(record) != wanted_run_id:
            continue
        if _record_tenant(record) != tenant:
            return OperatorRunReadResult(status="not_found", payload=_not_found_payload(wanted_run_id))
        return OperatorRunReadResult(status="ok", payload={"record": record})
    return OperatorRunReadResult(status="not_found", payload=_not_found_payload(wanted_run_id))


def _read_records(*, root: Path | None = None) -> list[dict[str, Any]]:
    ledger_root = Path(root) if root is not None else get_run_ledger_root()
    if not ledger_root.exists():
        return []
    if not ledger_root.is_dir():
        raise OperatorRunApiError(
            failure_class="RUN_API_BACKEND_UNAVAILABLE",
            reason="LEDGER_ROOT_NOT_DIRECTORY",
            detail=f"run ledger root is not a directory: {ledger_root}",
        )
    records: list[dict[str, Any]] = []
    try:
        files = [path for path in ledger_root.glob("*.json") if path.name.lower() != "latest.json"]
    except OSError as exc:
        raise OperatorRunApiError(
            failure_class="RUN_API_BACKEND_UNAVAILABLE",
            reason="LEDGER_ROOT_UNAVAILABLE",
            detail=f"run ledger root unavailable: {exc}",
        ) from exc
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise OperatorRunApiError(
                failure_class="RUN_API_INVALID_PAYLOAD",
                reason="LEDGER_RECORD_INVALID_JSON",
                detail=f"invalid run ledger payload: {path.name}",
            ) from exc
        if not isinstance(payload, dict):
            raise OperatorRunApiError(
                failure_class="RUN_API_INVALID_PAYLOAD",
                reason="LEDGER_RECORD_NOT_OBJECT",
                detail=f"run ledger payload must be an object: {path.name}",
            )
        _validate_record(payload, path.name)
        records.append(payload)
    return records


def _validate_record(record: dict[str, Any], source_name: str) -> None:
    if not _record_run_id(record):
        raise OperatorRunApiError(
            failure_class="RUN_API_INVALID_PAYLOAD",
            reason="RUN_ID_MISSING",
            detail=f"run ledger payload missing run identity: {source_name}",
        )
    if not _record_tenant(record):
        raise OperatorRunApiError(
            failure_class="RUN_API_INVALID_PAYLOAD",
            reason="TENANT_ID_MISSING",
            detail=f"run ledger payload missing tenant identity: {source_name}",
        )


def _to_operator_run_list_item(record: dict[str, Any]) -> dict[str, Any]:
    compact = to_compact_run_ledger_record(record)
    identity = dict(compact.get("identity") or {})
    outcome = dict(compact.get("outcome") or {})
    execution_state = dict(outcome.get("execution_state") or {})
    failure = dict(record.get("failure") or {})
    task_packet = dict((record.get("ai_operator") or {}).get("task_packet") or record.get("task_packet") or {})
    timestamps = _timestamps(record)
    evidence_status = _proof_evidence_status(record, compact)
    return {
        "run_id": _record_run_id(record),
        "tenant_id": _record_tenant(record),
        "status": _status(record, outcome),
        "failure_class": _failure_class(record),
        "project_id": identity.get("project_id") or record.get("project_id"),
        "repo_id": _text(task_packet.get("repo_key")) or _text((record.get("repo_profile") or {}).get("profile_id")),
        "task_id": _text(task_packet.get("task_id")) or _text(record.get("task_id")),
        "task_number": identity.get("task_number") or record.get("task_number"),
        "actor": (compact.get("workflow") or {}).get("actor"),
        "approval_state": _approval_state(record),
        "evidence_status": evidence_status,
        "started_at": timestamps.get("started_at"),
        "updated_at": timestamps.get("updated_at"),
        "completed_at": timestamps.get("completed_at"),
        "authority": _authority(),
        "workflow_id": _text((record.get("temporal") or {}).get("workflow_id")),
        "workflow_version": _text((record.get("workflow") or {}).get("version")),
        "failure_summary": _text(failure.get("summary")) or _text(outcome.get("reason_summary")),
        "execution_state": execution_state.get("state"),
    }


def _to_operator_run_detail(record: dict[str, Any]) -> dict[str, Any]:
    item = _to_operator_run_list_item(record)
    compact = to_compact_run_ledger_record(record)
    outcome = dict(compact.get("outcome") or {})
    failure = dict(record.get("failure") or {})
    return {
        "schema_version": "aix.operator_run.detail.v1",
        "authority": _authority(),
        "header": {
            "run_id": item["run_id"],
            "tenant_id": item["tenant_id"],
            "status": item["status"],
            "failure_class": item["failure_class"],
            "project_id": item["project_id"],
            "repo_id": item["repo_id"],
            "task_id": item["task_id"],
            "task_number": item["task_number"],
            "actor": item["actor"],
            "timestamps": {
                "started_at": item["started_at"],
                "updated_at": item["updated_at"],
                "completed_at": item["completed_at"],
            },
        },
        "decision": {
            "allowed_actions": [],
            "disabled_reasons": {
                "approve": "S203 contract only; action wiring is S210 scope.",
                "reject": "S203 contract only; action wiring is S210 scope.",
                "replay": "S203 contract only; replay wiring is S210 scope.",
                "stop": "S203 contract only; stop wiring is S210 scope.",
            },
            "next_action_suggestion": _next_action(item),
            "approval_state": item["approval_state"],
        },
        "execution_summary": {
            "what_happened": _text(outcome.get("reason_summary")) or item["failure_summary"] or item["status"],
            "result_summary": item["status"],
            "key_outputs": {
                "changed_file_count": outcome.get("changed_file_count"),
                "evidence_status": item["evidence_status"],
                "verifier_verdict": _verifier_verdict(record),
                "execution_state": item["execution_state"],
            },
            "workflow": {
                "workflow_id": item["workflow_id"],
                "workflow_version": item["workflow_version"],
            },
        },
        "evidence_bundle": _evidence_bundle_view(record),
        "failure": _failure_view(record, item, failure),
        "replay": _replay_summary_view(record),
        "audit": {
            "latest_status": _latest_audit_status(record),
            "events": list(record.get("audit_events") or []),
            "approvals": [],
        },
    }


def _evidence_bundle_view(record: dict[str, Any]) -> dict[str, Any]:
    compact = to_compact_run_ledger_record(record)
    execution_digest = dict(compact.get("execution_digest") or {})
    evidence_refs = list(compact.get("evidence_refs") or [])
    diff = _diff_view(record, execution_digest)
    test_output = _test_output_view(record, execution_digest)
    command_output = _command_output_view(record, execution_digest)
    verifier_result = _verifier_view(record)
    missing_fields: list[str] = []
    malformed_fields: list[str] = []

    if not diff["changed_files"] and not diff["diff_refs"] and not diff["no_mutation_reason"]:
        missing_fields.append("diff")
    if not test_output["commands"]:
        missing_fields.append("test_output.commands")
    for index, command in enumerate(test_output["commands"]):
        if command.get("exit_code") is None:
            missing_fields.append(f"test_output.commands[{index}].exit_code")
        if command.get("passed") is None:
            missing_fields.append(f"test_output.commands[{index}].passed")
    if not command_output:
        missing_fields.append("command_output")
    if verifier_result["verdict"] == "UNKNOWN":
        missing_fields.append("verifier_result")
    if evidence_refs and not all(isinstance(item, str) and item.strip() for item in evidence_refs):
        malformed_fields.append("evidence_refs")
    raw_transcript = ((record.get("result") or {}).get("evidence") or {}).get("transcript")
    if raw_transcript is not None and not isinstance(raw_transcript, list):
        malformed_fields.append("command_output")

    complete = not missing_fields and not malformed_fields
    return {
        "status": "complete" if complete else "incomplete",
        "complete": complete,
        "completeness": {
            "complete": complete,
            "missing_fields": missing_fields,
            "malformed_fields": malformed_fields,
        },
        "evidence_refs": evidence_refs,
        "diff": diff,
        "test_output": test_output,
        "command_output": command_output,
        "verifier_result": verifier_result,
    }


def _proof_evidence_status(record: dict[str, Any], compact: dict[str, Any]) -> str:
    bundle = _evidence_bundle_view(record)
    if bundle["complete"]:
        return "complete"
    if compact.get("evidence_refs") or compact.get("execution_digest"):
        return "incomplete"
    return "missing"


def _diff_view(record: dict[str, Any], execution_digest: dict[str, Any]) -> dict[str, Any]:
    refs = dict(record.get("refs") or {})
    result_evidence = dict((record.get("result") or {}).get("evidence") or {})
    proposal_artifacts = dict(result_evidence.get("proposal_artifacts") or {})
    mutation = dict(execution_digest.get("mutation") or {})
    changed_files = list(
        mutation.get("touched_files") or (execution_digest.get("execution") or {}).get("changed_files") or []
    )
    diff_refs = [
        ref
        for ref in [
            _text(refs.get("proposal_diff_ref")),
            _text(refs.get("proposal_changed_files_ref")),
            _text(proposal_artifacts.get("diff_ref")),
            _text(proposal_artifacts.get("changed_files_ref")),
        ]
        if ref
    ]
    return {
        "changed_files": [str(item) for item in changed_files],
        "diff_refs": diff_refs,
        "no_mutation_reason": None
        if changed_files or diff_refs
        else "no changed files or diff reference recorded in run ledger",
    }


def _test_output_view(record: dict[str, Any], execution_digest: dict[str, Any]) -> dict[str, Any]:
    validation_contract = dict((record.get("ai_operator") or {}).get("validation_contract") or {})
    if not validation_contract:
        validation_contract = dict(record.get("validation_contract") or {})
    postcheck = dict(execution_digest.get("postcheck") or {})
    result_evidence = dict((record.get("result") or {}).get("evidence") or {})
    raw_commands = list(
        validation_contract.get("expected_commands")
        or validation_contract.get("validation_commands")
        or result_evidence.get("commands")
        or []
    )
    if not raw_commands:
        tests = result_evidence.get("tests") or {}
        raw_commands = list((tests if isinstance(tests, dict) else {}).get("commands") or [])
    commands: list[dict[str, Any]] = []
    passed = postcheck.get("passed")
    if passed is None and dict(record.get("quality_gate") or {}).get("passed") is not None:
        passed = dict(record.get("quality_gate") or {}).get("passed")
    exit_code = postcheck.get("exit_code")
    if exit_code is None and passed is not None:
        exit_code = 0 if passed is True else 1
    for command in raw_commands:
        if isinstance(command, dict):
            commands.append(
                {
                    "command": _text(command.get("command")) or _text(command.get("cmd")),
                    "exit_code": command.get("exit_code"),
                    "passed": command.get("passed"),
                    "output": _text(command.get("output"))
                    or _text(command.get("stdout"))
                    or _text(command.get("stderr")),
                }
            )
        else:
            commands.append(
                {
                    "command": _text(command),
                    "exit_code": exit_code,
                    "passed": passed,
                    "output": _text(postcheck.get("output")),
                }
            )
    return {
        "commands": commands,
        "passed": passed,
        "reason_code": postcheck.get("reason_code"),
        "output": _text(postcheck.get("output")),
    }


def _command_output_view(record: dict[str, Any], execution_digest: dict[str, Any]) -> list[dict[str, Any]]:
    transcript = list(((record.get("result") or {}).get("evidence") or {}).get("transcript") or [])
    outputs: list[dict[str, Any]] = []
    for item in transcript:
        if not isinstance(item, dict):
            continue
        outputs.append(
            {
                "tool": _text(item.get("tool")),
                "ok": item.get("ok"),
                "reason_code": _text((item.get("result") or {}).get("reason_code"))
                if isinstance(item.get("result"), dict)
                else None,
            }
        )
    if outputs:
        return outputs
    mutation = dict(execution_digest.get("mutation") or {})
    tool = _text(mutation.get("primary_mutation_tool"))
    if tool:
        return [{"tool": tool, "ok": None, "reason_code": None}]
    return [
        {"tool": _text(item), "ok": None, "reason_code": None}
        for item in list(mutation.get("tool_sequence") or [])
        if _text(item)
    ]


def _verifier_view(record: dict[str, Any]) -> dict[str, Any]:
    quality_gate = dict(record.get("quality_gate") or {})
    return {
        "verdict": _verifier_verdict(record),
        "reason_code": _text(quality_gate.get("code")),
        "summary": _text(quality_gate.get("message")),
    }


def _replay_summary_view(record: dict[str, Any]) -> dict[str, Any]:
    original_run_id = _text(record.get("replay_of_run_id")) or _text(
        (record.get("replay") or {}).get("original_run_id")
    )
    return {
        "eligible": bool(original_run_id),
        "lineage": {
            "original_run_id": original_run_id,
            "replay_run_id": _record_run_id(record) if original_run_id else None,
        },
        "drift_status": "available_via_replay_comparison" if original_run_id else "not_replay",
        "compared_fields": ["diff", "tests", "verifier", "failure_class", "workflow_policy_version"],
        "duplicate_suppression": "read_only_comparison_no_execution",
    }


def _replay_comparison_view(*, original_record: dict[str, Any] | None, replay_record: dict[str, Any]) -> dict[str, Any]:
    original_run_id = _text(replay_record.get("replay_of_run_id")) or _text(
        (replay_record.get("replay") or {}).get("original_run_id")
    )
    replay_run_id = _record_run_id(replay_record)
    fields: list[dict[str, Any]] = []
    drifted = False
    for field_name, extractor in (
        ("diff", _comparison_diff),
        ("tests", _comparison_tests),
        ("verifier", _comparison_verifier),
        ("failure_class", _comparison_failure_class),
        ("workflow_policy_version", _comparison_workflow_policy_version),
    ):
        original_value = extractor(original_record) if original_record is not None else None
        replay_value = extractor(replay_record)
        status_value = "unavailable" if original_record is None or not original_run_id else "match"
        if status_value == "match" and _stable_payload(original_value) != _stable_payload(replay_value):
            status_value = "drift"
            drifted = True
        fields.append(
            {
                "field": field_name,
                "status": status_value,
                "original": original_value,
                "replay": replay_value,
            }
        )
    if not original_run_id:
        drift_status = "not_replay"
    elif original_record is None:
        drift_status = "original_missing"
    else:
        drift_status = "drift" if drifted else "match"
    deterministic_checksum = hashlib.sha256(
        _stable_payload(
            {
                "original_run_id": original_run_id,
                "replay_run_id": replay_run_id,
                "drift_status": drift_status,
                "compared_fields": fields,
            }
        ).encode("utf-8")
    ).hexdigest()
    return {
        "original_run_id": original_run_id,
        "replay_run_id": replay_run_id,
        "drift_status": drift_status,
        "deterministic_checksum": deterministic_checksum,
        "compared_fields": fields,
        "duplicate_suppression": {
            "mode": "read_only",
            "executed": False,
            "duplicate_execution": False,
        },
    }


def _comparison_diff(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    compact = to_compact_run_ledger_record(record)
    return _diff_view(record, dict(compact.get("execution_digest") or {}))


def _comparison_tests(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    compact = to_compact_run_ledger_record(record)
    return _test_output_view(record, dict(compact.get("execution_digest") or {}))


def _comparison_verifier(record: dict[str, Any] | None) -> dict[str, Any] | None:
    return _verifier_view(record) if record is not None else None


def _comparison_failure_class(record: dict[str, Any] | None) -> str | None:
    return _failure_class(record) if record is not None else None


def _comparison_workflow_policy_version(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "workflow_version": _text((record.get("workflow") or {}).get("version")),
        "policy_version": _text((record.get("policy") or {}).get("version"))
        or _text((record.get("policy_decision") or {}).get("version")),
    }


def _stable_payload(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _failure_view(record: dict[str, Any], item: dict[str, Any], failure: dict[str, Any]) -> dict[str, Any] | None:
    if not item["failure_class"] and item["status"] not in {"failed", "blocked", "incomplete"}:
        return None
    return {
        "failure_class": item["failure_class"] or "RUN_FAILURE_CLASS_MISSING",
        "failed_stage": _text(failure.get("stage")) or "unknown",
        "summary": item["failure_summary"] or "run failed",
        "playbook": {
            "action": _text(failure.get("policy_action")) or "inspect evidence and classify failure",
            "reason_code": _text(failure.get("code")),
        },
        "evidence_refs": list(to_compact_run_ledger_record(record).get("evidence_refs") or []),
    }


def _record_run_id(record: dict[str, Any]) -> str:
    return (
        _text(record.get("run_id"))
        or _text((record.get("result") or {}).get("run_id"))
        or _text(record.get("correlation_id"))
        or ""
    )


def _record_tenant(record: dict[str, Any]) -> str:
    return _text(record.get("tenant_id")) or _text((record.get("identity") or {}).get("tenant_id")) or ""


def _record_evidence_ids(record: dict[str, Any]) -> set[str]:
    result = dict(record.get("result") or {})
    result_evidence = dict(result.get("evidence") or {})
    compact = to_compact_run_ledger_record(record)
    candidates: list[Any] = [
        record.get("evidence_id"),
        result.get("evidence_id"),
        result_evidence.get("evidence_id"),
        (record.get("evidence") or {}).get("evidence_id") if isinstance(record.get("evidence"), dict) else None,
        *list(record.get("evidence_refs") or []),
        *list(result.get("evidence_refs") or []),
        *list(compact.get("evidence_refs") or []),
    ]
    return {text for item in candidates if (text := _text(item))}


def _status(record: dict[str, Any], outcome: dict[str, Any]) -> str:
    return _text(outcome.get("status")) or _text(record.get("final_status")) or _text(record.get("status")) or "unknown"


def _failure_class(record: dict[str, Any]) -> str | None:
    minimal = dict(record.get("minimal_run_outcome") or {})
    failure = dict(record.get("failure") or {})
    return _text(minimal.get("failure_class")) or _text(failure.get("failure_family")) or _text(failure.get("category"))


def _evidence_status(compact: dict[str, Any]) -> str:
    if compact.get("attestation") and compact.get("evidence_refs") and compact.get("execution_digest"):
        return "complete"
    if compact.get("evidence_refs") or compact.get("execution_digest"):
        return "incomplete"
    return "missing"


def _approval_state(record: dict[str, Any]) -> str:
    approval = record.get("approval_state")
    return _text(approval) or "none"


def _timestamps(record: dict[str, Any]) -> dict[str, str | None]:
    recorded = _text(record.get("recorded_at_utc"))
    return {
        "started_at": _text(record.get("started_at_utc")) or recorded,
        "updated_at": _text(record.get("updated_at_utc")) or recorded,
        "completed_at": _text(record.get("completed_at_utc")) or recorded,
    }


def _verifier_verdict(record: dict[str, Any]) -> str:
    quality_gate = dict(record.get("quality_gate") or {})
    if quality_gate.get("passed") is True:
        return "PASS"
    if quality_gate.get("passed") is False:
        return "FAIL"
    return "UNKNOWN"


def _latest_audit_status(record: dict[str, Any]) -> dict[str, Any] | None:
    events = [event for event in list(record.get("audit_events") or []) if isinstance(event, dict)]
    if not events:
        return None
    return events[-1]


def _next_action(item: dict[str, Any]) -> str:
    if item["status"] in {"failed", "blocked", "incomplete"}:
        return "inspect failure panel and evidence bundle"
    if item["approval_state"] == "pending":
        return "review pending approval in Run Detail"
    return "inspect evidence bundle"


def _not_found_payload(run_id: str) -> dict[str, Any]:
    return {
        "success": False,
        "failure_class": "RUN_NOT_FOUND",
        "message": "Run not found.",
        "data": {"run_id": run_id},
    }


def _evidence_not_found_payload(evidence_id: str) -> dict[str, Any]:
    return {
        "success": False,
        "failure_class": "EVIDENCE_NOT_FOUND",
        "message": "Evidence not found.",
        "data": {"evidence_id": evidence_id},
    }


def _authority() -> dict[str, Any]:
    return {
        "source": "aix_run_ledger",
        "source_classification": "authoritative_aix_run_record",
        "workhub_task_derived": False,
        "opsbus_event_text_derived": False,
    }


def _require_tenant(tenant_id: str) -> str:
    tenant = _text(tenant_id)
    if not tenant:
        raise OperatorRunApiError(
            failure_class="CONTRACT_VIOLATION",
            reason="TENANT_ID_REQUIRED",
            detail="X-Tenant-ID header is required.",
        )
    return tenant


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


__all__ = [
    "OperatorRunApiError",
    "OperatorRunReadResult",
    "get_operator_run_detail",
    "get_operator_run_evidence_bundle",
    "get_operator_run_replay_by_evidence_id",
    "get_operator_run_replay_comparison",
    "list_operator_runs",
]
