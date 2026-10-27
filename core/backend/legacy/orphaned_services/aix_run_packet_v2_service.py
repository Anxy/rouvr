from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.legacy.orphaned_services.aix_command_identity_service import coerce_command_list

RunSummaryStateV2 = Literal[
    "succeeded",
    "failed",
    "awaiting_review",
    "blocked_on_human",
    "blocked_on_system",
]
RunSummaryCodeV2 = Literal[
    "OK",
    "CLI_EXIT_NONZERO",
    "HARNESS_VALIDATION_MISMATCH",
    "EVIDENCE_REQUIRED",
]

LEGACY_SUMMARY_KEYS = {
    "final_status",
    "handoff_status",
    "quality_gate",
    "attestation",
    "minimal_run_outcome",
    "execution_digest",
    "promotion_projection",
}


def _text(value: Any) -> str | None:
    if hasattr(value, "value"):
        value = value.value
    text = str(value or "").strip()
    return text or None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    return [str(item).strip().replace("\\", "/") for item in list(value or []) if str(item).strip()]


def _without_empty(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {str(key): _without_empty(item) for key, item in value.items()}
        return {key: item for key, item in cleaned.items() if item not in (None, {}, [])}
    if isinstance(value, list):
        return [item for item in (_without_empty(item) for item in value) if item not in (None, {}, [])]
    return value


def _payload_result(payload: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(payload.get("result"))


def _payload_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    result = _payload_result(payload)
    return _as_dict(result.get("evidence"))


def _control_plane(payload: dict[str, Any]) -> dict[str, Any]:
    evidence = _payload_evidence(payload)
    return _as_dict(payload.get("control_plane_outcome")) or _as_dict(evidence.get("control_plane_outcome"))


def _changed_files(payload: dict[str, Any]) -> list[str]:
    evidence = _payload_evidence(payload)
    proposal = _as_dict(evidence.get("proposal_artifacts"))
    for candidate in (proposal.get("changed_files"), evidence.get("changed_files"), payload.get("changed_files")):
        files = _string_list(candidate)
        if files:
            return list(dict.fromkeys(files))
    return []


def _tests_run(payload: dict[str, Any]) -> bool:
    evidence = _payload_evidence(payload)
    tests = _as_dict(evidence.get("tests"))
    if bool(tests.get("detected")) or _string_list(tests.get("commands")):
        return True
    for command in list(evidence.get("commands") or []):
        if not isinstance(command, dict):
            continue
        command_text = _text(command.get("command_text")) or " ".join(_string_list(command.get("command")))
        if "pytest" in command_text or " py.test" in command_text:
            return True
    return False


def _outcome_code(raw_reason: str | None, *, state: str) -> RunSummaryCodeV2:
    reason = str(raw_reason or "").strip().upper()
    if state == "succeeded":
        return "OK"
    if reason in {"HARNESS_VALIDATION_MISMATCH", "CANDIDATE_VALIDATION_MISSING"}:
        return "HARNESS_VALIDATION_MISMATCH"
    if reason in {
        "VERIFIER_OR_EVIDENCE_REQUIRED",
        "EVIDENCE_REQUIRED",
        "NO_TESTS_RUN",
        "WORKHUB_EVIDENCE_WRITEBACK_FAILED",
    }:
        return "EVIDENCE_REQUIRED"
    if "POLICY" in reason or "DENY" in reason or "ADMISSION" in reason:
        return "EVIDENCE_REQUIRED"
    return "CLI_EXIT_NONZERO"


def _outcome(payload: dict[str, Any]) -> dict[str, Any]:
    control_plane = _control_plane(payload)
    result = _payload_result(payload)
    raw_status = (
        _text(control_plane.get("status"))
        or _text(payload.get("final_status"))
        or _text(payload.get("status"))
        or _text(result.get("status"))
        or "failed"
    ).lower()
    raw_reason = _text(control_plane.get("reason_code")) or _text(payload.get("reason_code"))
    auto_promoted = bool(control_plane.get("auto_promoted") or payload.get("auto_promoted"))
    if raw_status in {"done", "succeeded", "success"}:
        state: RunSummaryStateV2 = "succeeded"
    elif raw_status in {"needs_review", "submitted_for_review"}:
        state = "awaiting_review"
    elif raw_status in {"waiting_on_human", "human_required"}:
        state = "blocked_on_human"
    elif raw_status == "blocked":
        category = _text(control_plane.get("failure_category")) or ""
        state = "blocked_on_system" if "platform" in category or "system" in category else "blocked_on_human"
    else:
        state = "failed"
    code = _outcome_code(raw_reason, state=state)
    return {
        "state": state,
        "code": code,
        "final": True,
        "auto_promoted": auto_promoted,
    }


def _missing_commands(payload: dict[str, Any]) -> list[dict[str, Any]]:
    quality_gate = _as_dict(payload.get("quality_gate")) or _as_dict(_payload_evidence(payload).get("quality_gate"))
    details = _as_dict(quality_gate.get("details"))
    validation = _as_dict(details.get("validation"))
    return coerce_command_list(validation.get("missing_commands"))


def _refs(payload: dict[str, Any], *, invocation_id: str) -> dict[str, Any]:
    refs = _as_dict(payload.get("refs"))
    evidence = _payload_evidence(payload)
    proposal = _as_dict(evidence.get("proposal_artifacts"))
    return _without_empty(
        {
            "detail": refs.get("detail") or f"aix://run/detail/{invocation_id}",
            "diff": refs.get("diff") or refs.get("proposal_diff_ref") or proposal.get("diff_ref"),
            "ledger": refs.get("ledger") or refs.get("run_ledger_ref") or payload.get("run_ledger_ref"),
            "evidence": refs.get("evidence") or refs.get("evidence_ref") or evidence.get("review_bundle_ref"),
        }
    )


def _invocation_id(payload: dict[str, Any]) -> str:
    result = _payload_result(payload)
    return (
        _text(payload.get("invocation_id"))
        or _text(payload.get("run_id"))
        or _text(result.get("run_id"))
        or _text(payload.get("correlation_id"))
        or "run-unknown"
    )


def _task_number(payload: dict[str, Any]) -> int | None:
    value = payload.get("task_number") or _as_dict(payload.get("task_packet")).get("task_number")
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number or None


def _duration_ms(payload: dict[str, Any]) -> int | None:
    for value in (
        payload.get("duration_ms"),
        _payload_result(payload).get("duration_ms"),
        _as_dict(payload.get("timing")).get("duration_ms"),
    ):
        if value is None:
            continue
        try:
            duration = int(value)
        except (TypeError, ValueError):
            continue
        if duration >= 0:
            return duration
    return None


def _queued_at(payload: dict[str, Any]) -> str:
    return (
        _text(payload.get("queued_at"))
        or _text(payload.get("started_at_utc"))
        or _text(payload.get("recorded_at_utc"))
        or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class RunSummaryOutcomeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: RunSummaryStateV2
    code: RunSummaryCodeV2
    final: bool
    auto_promoted: bool

    @model_validator(mode="after")
    def _validate_pair(self) -> RunSummaryOutcomeV2:
        if self.state == "succeeded" and self.code != "OK":
            raise ValueError("succeeded requires OK")
        if self.state != "succeeded" and self.code == "OK":
            raise ValueError("OK requires succeeded")
        return self


class RunSummaryChangesV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[str] = Field(default_factory=list)
    tests_run: bool


class RunSummaryValidationV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    missing_commands: list[dict[str, Any]] = Field(default_factory=list)


class RunSummaryTimingV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queued_at: str = Field(..., min_length=1)
    duration_ms: int | None = Field(default=None, ge=0)


class RunSummaryV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal["aix.run.summary.v3"] = "aix.run.summary.v3"
    task_id: str | None = None
    task_number: int | None = None
    project_id: str = Field(..., min_length=1)
    invocation_id: str = Field(..., min_length=1)
    outcome: RunSummaryOutcomeV2
    changes: RunSummaryChangesV2
    validation: RunSummaryValidationV2 | None = None
    timing: RunSummaryTimingV2
    refs: dict[str, str] = Field(default_factory=dict)
    fingerprint: str = Field(..., min_length=8)

    @model_validator(mode="after")
    def _validate_summary(self) -> RunSummaryV2:
        if self.outcome.state == "succeeded" and not self.outcome.auto_promoted:
            raise ValueError("succeeded requires auto_promoted")
        if self.outcome.state == "succeeded" and not self.changes.files:
            raise ValueError("succeeded requires changed files")
        for forbidden in LEGACY_SUMMARY_KEYS:
            if forbidden in self.model_dump(mode="json"):
                raise ValueError(f"legacy summary key forbidden: {forbidden}")
        return self


class RunDetailV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal["aix.run.detail.v2"] = "aix.run.detail.v2"
    invocation_id: str = Field(..., min_length=1)
    extends: str = Field(..., min_length=1)
    execution: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
    attestation: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_detail(self) -> RunDetailV2:
        if "changed_files" in json.dumps(self.model_dump(mode="json"), sort_keys=True):
            raise ValueError("detail must not repeat summary-owned changed_files")
        return self


def build_run_summary_v2(payload: dict[str, Any]) -> dict[str, Any]:
    invocation_id = _invocation_id(payload)
    summary = _without_empty(
        {
            "schema": "aix.run.summary.v3",
            "task_id": _text(payload.get("task_id")) or _text(_payload_result(payload).get("task_id")),
            "task_number": _task_number(payload),
            "project_id": _text(payload.get("project_id")) or "unknown",
            "invocation_id": invocation_id,
            "outcome": _outcome(payload),
            "changes": {
                "files": _changed_files(payload),
                "tests_run": _tests_run(payload),
            },
            "validation": {"missing_commands": _missing_commands(payload)},
            "timing": {
                "queued_at": _queued_at(payload),
                "duration_ms": _duration_ms(payload),
            },
            "refs": _refs(payload, invocation_id=invocation_id),
        }
    )
    summary["fingerprint"] = _fingerprint(summary)
    return validate_run_summary_v2(summary)


def build_run_detail_v2(payload: dict[str, Any], *, summary_ref: str | None = None) -> dict[str, Any]:
    invocation_id = _invocation_id(payload)
    evidence = _payload_evidence(payload)
    quality_gate = _as_dict(evidence.get("quality_gate"))
    planner = _as_dict(payload.get("planner")) or _as_dict(evidence.get("planner"))
    runtime = _as_dict(evidence.get("runtime"))
    validation_commands = _string_list(payload.get("validation_commands")) or _string_list(
        _as_dict(payload.get("validation_contract")).get("expected_commands")
    )
    detail = _without_empty(
        {
            "schema": "aix.run.detail.v2",
            "invocation_id": invocation_id,
            "extends": summary_ref or f"aix://run/summary/{invocation_id}",
            "execution": {
                "phases": _string_list(payload.get("phases")) or _string_list(runtime.get("phases")),
                "failed_phase": _text(payload.get("failed_phase")) or _text(runtime.get("failed_phase")),
                "executor": _text(payload.get("executor")) or _text(runtime.get("executor")),
                "model": _text(planner.get("model")) or _text(payload.get("model")),
            },
            "validation": {
                "commands": validation_commands,
                "completeness_warnings": _string_list(quality_gate.get("reasons")),
            },
            "policy": {
                "task_family": _text(payload.get("task_family")),
                "admission": _as_dict(evidence.get("admission")),
                "promotion_blocked": _outcome(payload)["state"] in {"awaiting_review", "blocked_on_human"},
                "promotion_reason": _control_plane(payload).get("reason_code"),
            },
            "attestation": {
                "id": _text(_as_dict(payload.get("attestation")).get("attestation_id"))
                or _text(_as_dict(payload.get("attestation")).get("id")),
                "checks_ref": _text(_as_dict(payload.get("attestation")).get("checks_ref")),
            },
            "provenance": {
                "runner_url": _text(payload.get("runner_url")),
                "workspace": _text(_as_dict(evidence.get("proposal_artifacts")).get("workspace_root")),
                "transport": _as_dict(payload.get("transport")),
            },
        }
    )
    return validate_run_detail_v2(detail)


def build_run_packets_v2(payload: dict[str, Any], *, include_detail: bool | None = None) -> dict[str, Any]:
    summary = build_run_summary_v2(payload)
    should_include_detail = bool(include_detail) or summary["outcome"]["state"] != "succeeded"
    packets: dict[str, Any] = {"summary": summary}
    if should_include_detail:
        packets["detail"] = build_run_detail_v2(payload, summary_ref=summary["refs"].get("detail"))
    return packets


def validate_run_summary_v2(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = _without_empty(payload)
    return RunSummaryV2.model_validate(cleaned).model_dump(mode="json", exclude_none=True)


def validate_run_detail_v2(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = _without_empty(payload)
    return RunDetailV2.model_validate(cleaned).model_dump(mode="json", exclude_none=True)


__all__ = [
    "LEGACY_SUMMARY_KEYS",
    "RunDetailV2",
    "RunSummaryV2",
    "build_run_detail_v2",
    "build_run_packets_v2",
    "build_run_summary_v2",
    "validate_run_detail_v2",
    "validate_run_summary_v2",
]
