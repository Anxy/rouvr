from __future__ import annotations

import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from backend.clients.opsbus_run_logs_client import opsbus_run_logs_client
from backend.clients.workhub_knowledge_client import workhub_knowledge_client
from backend.clients.workhub_task_evidence_client import workhub_task_evidence_client
from backend.services.aix_enveloped_run_service import RunResult
from backend.services.devexecutor_mutation_tool_taxonomy_service import ORCHESTRATION_REMOVED_MUTATION_TOOLS

try:
    from hyeve_core.task_knowledge_contract import build_task_knowledge_metadata
except ModuleNotFoundError:
    _HERE = Path(__file__).resolve()
    _REPO_ROOT = _HERE.parents[5]
    _HYEVE_CORE_ROOT = _REPO_ROOT / "hyeve_core"
    for _candidate in (_REPO_ROOT, _HYEVE_CORE_ROOT):
        _candidate_text = str(_candidate)
        if _candidate_text not in sys.path:
            sys.path.insert(0, _candidate_text)
    import hyeve_core as _hyeve_core

    _nested_hyeve_core = _HYEVE_CORE_ROOT / "hyeve_core"
    _package_path = list(getattr(_hyeve_core, "__path__", []))
    if _HYEVE_CORE_ROOT.exists() and str(_HYEVE_CORE_ROOT) not in _package_path:
        _package_path.append(str(_HYEVE_CORE_ROOT))
    if _nested_hyeve_core.exists() and str(_nested_hyeve_core) not in _package_path:
        _package_path.append(str(_nested_hyeve_core))
    if _package_path:
        _hyeve_core.__path__ = _package_path

    from hyeve_core.task_knowledge_contract import build_task_knowledge_metadata

_AIX_LOGGER = logging.getLogger("hyeve_aix_service.aix_v2_runtime")
_BOUNDARY_LOGGER = structlog.get_logger("hyeve_aix_service.aix_v2_runtime")


def _join_writeback_errors(*errors: str | None) -> str | None:
    parts: list[str] = []
    for error in errors:
        text = str(error or "").strip()
        if text and text not in parts:
            parts.append(text)
    return "; ".join(parts) if parts else None


@dataclass(slots=True)
class WritebackOutcome:
    opsbus_written: bool
    workhub_written: bool
    retry_enqueued: bool
    status: str
    opsbus_error: str | None = None
    workhub_error: str | None = None
    knowledge_written: bool = False
    knowledge_duplicate: bool = False
    knowledge_error: str | None = None
    knowledge_ref: str | None = None
    retry_error: str | None = None
    retry_event_ref: str | None = None
    analytics_event_written: bool = False
    analytics_event_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "opsbus_written": self.opsbus_written,
            "workhub_written": self.workhub_written,
            "retry_enqueued": self.retry_enqueued,
            "status": self.status,
            "opsbus_error": self.opsbus_error,
            "workhub_error": self.workhub_error,
            "knowledge_written": self.knowledge_written,
            "knowledge_duplicate": self.knowledge_duplicate,
            "knowledge_error": self.knowledge_error,
            "knowledge_ref": self.knowledge_ref,
            "retry_error": self.retry_error,
            "retry_event_ref": self.retry_event_ref,
            "analytics_event_written": self.analytics_event_written,
            "analytics_event_error": self.analytics_event_error,
        }


def _emit_writeback_boundary(*, code: str, decision: str, input_summary: dict[str, Any]) -> None:
    import json as _json
    import sys as _sys

    print(
        _json.dumps(
            {
                "event": "aix_boundary",
                "layer": "writeback",
                "code": code,
                "decision": decision,
                "input_summary": input_summary,
            }
        ),
        flush=True,
        file=_sys.stdout,
    )


async def writeback_run_result(
    result: RunResult,
    *,
    tenant_id: str,
    actor_id: str,
    workhub_service_token_override: str | None = None,
    include_opsbus: bool = True,
) -> WritebackOutcome:
    """
    Mandatory writeback adapter for envelope executions.

    Behavior:
    - Always attempt OpsBus writeback when run_id is present.
    - Attempt WorkHub evidence writeback when task_id is present.
    - If WorkHub write fails, force status=needs_review and emit retry event to OpsBus logs.
    """
    opsbus_written = False
    opsbus_error: str | None = None
    if include_opsbus and result.run_id and result.project_id and result.correlation_id:
        opsbus_written, opsbus_error = await opsbus_run_logs_client.log_model_usage(
            tenant_id=tenant_id,
            project_id=result.project_id,
            run_id=result.run_id,
            correlation_id=result.correlation_id,
            actor_id=actor_id,
            usage_block={
                "writeback": {
                    "schema_id": result.schema_id,
                    "status": result.status,
                    "run_id": result.run_id,
                    "task_id": result.task_id,
                    "task_number": result.task_number,
                    "packet_id": result.packet_id,
                    "evidence_refs": result.evidence_refs,
                    "telemetry": result.telemetry,
                    "error": result.error.to_dict() if result.error else None,
                }
            },
        )
    analytics_event_written = False
    analytics_event_error: str | None = None
    if include_opsbus and result.run_id and result.project_id and result.correlation_id:
        analytics_event_written, analytics_event_error = await opsbus_run_logs_client.log_run_event(
            tenant_id=tenant_id,
            project_id=result.project_id,
            run_id=result.run_id,
            correlation_id=result.correlation_id,
            event_name="aix.devexecutor.run.closed",
            event_payload=_build_run_closed_event_payload(result),
        )

    workhub_written = False
    workhub_error: str | None = None
    if result.task_id and result.project_id and result.correlation_id:
        routing_written, routing_error = await workhub_task_evidence_client.add_model_routing_decision_evidence(
            tenant_id=tenant_id,
            project_id=result.project_id,
            task_id=result.task_id,
            correlation_id=result.correlation_id,
            actor_id=actor_id,
            decision_payload={
                "execution_writeback": {
                    "run_id": result.run_id,
                    "status": result.status,
                    "schema_id": result.schema_id,
                    "evidence": result.evidence,
                    "escalation_packet": dict((result.evidence or {}).get("escalation_packet") or {}),
                    "evidence_refs": result.evidence_refs,
                    "telemetry": result.telemetry,
                }
            },
            service_token_override=workhub_service_token_override,
        )
        codeintel_written, codeintel_error = await workhub_task_evidence_client.add_codeintel_provenance_evidence(
            tenant_id=tenant_id,
            project_id=result.project_id,
            task_id=result.task_id,
            correlation_id=result.correlation_id,
            actor_id=actor_id,
            provenance_payload=dict((result.evidence or {}).get("codeintel") or {}),
            service_token_override=workhub_service_token_override,
        )
        workhub_written = bool(routing_written and codeintel_written)
        workhub_error = _join_writeback_errors(routing_error, codeintel_error)

    knowledge_written = False
    knowledge_duplicate = False
    knowledge_error: str | None = None
    knowledge_ref: str | None = None
    if workhub_written and _should_publish_knowledge(result):
        knowledge_written, knowledge_duplicate, knowledge_error, knowledge_ref = await _publish_workhub_knowledge(
            result=result,
            tenant_id=tenant_id,
            actor_id=actor_id,
            workhub_service_token_override=workhub_service_token_override,
        )

    retry_enqueued = False
    retry_error: str | None = None
    retry_event_ref: str | None = None
    final_status = result.status
    if str(result.status or "").strip().lower() not in {"completed", "done"}:
        _emit_writeback_boundary(
            code="RUN_OUTCOME_FAILURE",
            decision="failed",
            input_summary={
                "run_id": result.run_id,
                "task_id": result.task_id,
                "task_number": result.task_number,
                "run_status": result.status,
                "error_code": str(result.error.code or "").strip() if result.error else None,
                "workhub_written": workhub_written,
                "opsbus_written": opsbus_written,
            },
        )
    if result.task_id and not workhub_written:
        _emit_writeback_boundary(
            code="WH_WRITE_FAILED",
            decision="failed",
            input_summary={
                "run_id": result.run_id,
                "task_id": result.task_id,
                "task_number": result.task_number,
                "run_status": result.status,
                "opsbus_written": opsbus_written,
                "workhub_error": workhub_error,
            },
        )
        final_status = "needs_review"
        retry_enqueued, retry_error = await _enqueue_workhub_retry_event(
            result=result,
            tenant_id=tenant_id,
            actor_id=actor_id,
            reason=workhub_error or "workhub_writeback_failed",
        )
        if retry_enqueued and result.run_id:
            retry_event_ref = f"opsbus://run-log/{result.run_id}/workhub.writeback.retry"

    return WritebackOutcome(
        opsbus_written=opsbus_written,
        workhub_written=workhub_written,
        retry_enqueued=retry_enqueued,
        status=final_status,
        opsbus_error=opsbus_error,
        workhub_error=workhub_error,
        knowledge_written=knowledge_written,
        knowledge_duplicate=knowledge_duplicate,
        knowledge_error=knowledge_error,
        knowledge_ref=knowledge_ref,
        retry_error=retry_error,
        retry_event_ref=retry_event_ref,
        analytics_event_written=analytics_event_written,
        analytics_event_error=analytics_event_error,
    )


def _build_run_closed_event_payload(result: RunResult) -> dict[str, Any]:
    evidence = _as_dict(result.evidence)
    telemetry = _as_dict(result.telemetry)
    runtime = _as_dict(telemetry.get("runtime"))
    model = _as_dict(telemetry.get("model"))
    quality_gate = _as_dict(evidence.get("quality_gate"))
    transcript = [item for item in list(evidence.get("transcript") or []) if isinstance(item, dict)]
    tool_sequence = [str(item).strip() for item in list(runtime.get("tool_sequence") or []) if str(item).strip()]
    if not tool_sequence and transcript:
        tool_sequence = [
            str(item.get("tool") or "").strip() for item in transcript if str(item.get("tool") or "").strip()
        ]
    legacy_removed_tools_seen = [tool for tool in tool_sequence if tool in set(ORCHESTRATION_REMOVED_MUTATION_TOOLS)]
    terminal_errors: list[str] = []
    if result.error and str(result.error.code or "").strip():
        terminal_errors.append(str(result.error.code).strip())
    for item in transcript:
        result_block = _as_dict(item.get("result"))
        dry_run_failures = [
            entry for entry in list(result_block.get("dry_run_failures") or []) if isinstance(entry, dict)
        ]
        for failure in dry_run_failures:
            code = str(failure.get("failure_code") or "").strip()
            if code and code not in terminal_errors:
                terminal_errors.append(code)
    confirm_anchor_outcomes = []
    for item in transcript:
        if str(item.get("tool") or "").strip() != "confirm_anchor":
            continue
        code = str(_as_dict(item.get("result")).get("reason_code") or "").strip()
        if code:
            confirm_anchor_outcomes.append(code)
    diagnostics = _as_dict(evidence.get("diagnostics"))
    validation_errors_at_intake = []
    for key in ("validation_errors_at_intake", "validation_errors", "intake_validation_errors"):
        for item in list(diagnostics.get(key) or []):
            text = str(item).strip()
            if text and text not in validation_errors_at_intake:
                validation_errors_at_intake.append(text)
    planner_packet = _as_dict(diagnostics.get("planner_packet"))
    validation_error = str(planner_packet.get("validation_error") or "").strip()
    if validation_error and validation_error not in validation_errors_at_intake:
        validation_errors_at_intake.append(validation_error)
    for gap in list(planner_packet.get("clarification_gaps") or []):
        if not isinstance(gap, dict):
            continue
        code = str(gap.get("code") or "").strip()
        if code and code not in validation_errors_at_intake:
            validation_errors_at_intake.append(code)
    steps_executed = runtime.get("step_count")
    if steps_executed in (None, ""):
        steps_executed = evidence.get("transcript_count")
    steps_planned = diagnostics.get("steps_planned")
    if steps_planned in (None, "") and isinstance(evidence.get("planned_steps"), list):
        steps_planned = len(list(evidence.get("planned_steps") or []))
    planner_used = bool(str(model.get("provider") or "").strip() or str(model.get("model") or "").strip())
    planned_steps_value = _coerce_optional_int(steps_planned)
    executed_steps_value = _coerce_optional_int(steps_executed)
    return {
        "event": "aix.devexecutor.run.closed",
        "event_schema_version": "1.0",
        "run_id": result.run_id,
        "task_id": result.task_id,
        "task_number": int(result.task_number or 0) or None,
        "project_id": result.project_id,
        "correlation_id": result.correlation_id,
        "status": result.status,
        "planner_used": planner_used,
        "persona": str(model.get("role") or "").strip() or None,
        "provider": str(model.get("provider") or "").strip() or None,
        "model": str(model.get("model") or "").strip() or None,
        "steps_planned": planned_steps_value,
        "steps_executed": executed_steps_value if executed_steps_value is not None else len(tool_sequence) or None,
        "tool_sequence_classification": "audit",
        "tool_sequence": tool_sequence,
        "legacy_removed_tools_seen": legacy_removed_tools_seen,
        "quality_gate": str(quality_gate.get("code") or "").strip() or None,
        "quality_gate_passed": bool(quality_gate.get("passed")),
        "errors": terminal_errors,
        "confirm_anchor_outcomes": confirm_anchor_outcomes,
        "validation_errors_at_intake": validation_errors_at_intake,
    }


def _coerce_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _enqueue_workhub_retry_event(
    *,
    result: RunResult,
    tenant_id: str,
    actor_id: str,
    reason: str,
) -> tuple[bool, str | None]:
    if not result.run_id or not result.project_id or not result.correlation_id:
        return False, "MISSING_RUN_PROJECT_OR_CORRELATION"
    return await opsbus_run_logs_client.log_model_usage(
        tenant_id=tenant_id,
        project_id=result.project_id,
        run_id=result.run_id,
        correlation_id=result.correlation_id,
        actor_id=actor_id,
        usage_block={
            "event": "workhub.writeback.retry",
            "reason": reason,
            "task_id": result.task_id,
            "task_number": result.task_number,
            "run_id": result.run_id,
            "packet_id": result.packet_id,
            "retryable": True,
            "retry_payload": result.to_dict(),
        },
    )


def _should_publish_knowledge(result: RunResult) -> bool:
    if str(result.status or "").strip() not in {"completed", "done"}:
        return False
    if not result.project_id or not result.correlation_id or not result.run_id:
        return False
    if int(result.task_number or 0) <= 0:
        return False
    quality_gate = _as_dict((result.evidence or {}).get("quality_gate"))
    return bool(quality_gate.get("passed")) and str(quality_gate.get("code") or "").strip() == "QUALITY_OK"


async def _publish_workhub_knowledge(
    *,
    result: RunResult,
    tenant_id: str,
    actor_id: str,
    workhub_service_token_override: str | None,
) -> tuple[bool, bool, str | None, str | None]:
    project_id = str(result.project_id or "").strip()
    correlation_id = str(result.correlation_id or "").strip()
    query = _knowledge_query(result)
    candidate = _build_knowledge_candidate(result)
    pattern_hash = str(candidate["metadata_json"].get("pattern_hash") or "").strip()

    existing, search_error = await workhub_knowledge_client.search_knowledge(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        actor_id=actor_id,
        query=query,
        limit=20,
        service_token_override=workhub_service_token_override,
    )
    if search_error:
        return False, False, search_error, None
    for item in existing:
        metadata = _as_dict(item.get("metadata_json"))
        if str(metadata.get("pattern_hash") or "").strip() == pattern_hash:
            knowledge_id = str(item.get("id") or "").strip() or None
            return False, True, None, _knowledge_ref(project_id=project_id, knowledge_id=knowledge_id)

    knowledge_id, create_error = await workhub_knowledge_client.create_knowledge(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        actor_id=actor_id,
        title=str(candidate["title"]),
        content=str(candidate["content"]),
        kind=str(candidate["kind"]),
        tags=list(candidate["tags"]),
        uri=str(candidate["uri"]) if candidate["uri"] is not None else None,
        metadata_json=dict(candidate["metadata_json"]),
        service_token_override=workhub_service_token_override,
    )
    if not create_error:
        return True, False, None, _knowledge_ref(project_id=project_id, knowledge_id=knowledge_id)

    comment_written, comment_error = await workhub_knowledge_client.add_task_comment(
        tenant_id=tenant_id,
        project_id=project_id,
        task_number=int(result.task_number or 0),
        correlation_id=correlation_id,
        actor_id=actor_id,
        body=_knowledge_comment_body(candidate),
        service_token_override=workhub_service_token_override,
    )
    if comment_written:
        return True, False, None, f"workhub://projects/{project_id}/tasks/{int(result.task_number or 0)}/comments"
    return False, False, f"{create_error}; comment_fallback={comment_error or 'failed'}", None


def _build_knowledge_candidate(result: RunResult) -> dict[str, Any]:
    evidence = _as_dict(result.evidence)
    telemetry = _as_dict(result.telemetry)
    runtime = _as_dict(telemetry.get("runtime"))
    quality_gate = _as_dict(evidence.get("quality_gate"))
    changed_files = [str(item).strip() for item in list(evidence.get("changed_files") or []) if str(item).strip()]
    grounded_symbols = [
        str(_as_dict(item).get("qualified_name") or "").strip()
        for item in list(evidence.get("grounded_symbols") or [])
        if str(_as_dict(item).get("qualified_name") or "").strip()
    ]
    tool_sequence = [str(item).strip() for item in list(runtime.get("tool_sequence") or []) if str(item).strip()]
    commands = [str(_as_dict(item).get("command_text") or "").strip() for item in list(evidence.get("commands") or [])]
    commands = [item for item in commands if item]
    task_category = _task_category(result=result, changed_files=changed_files)
    primary_path = changed_files[0] if changed_files else "workspace"
    title = f"{task_category}: {primary_path}"
    summary_lines = [
        f"Task #{int(result.task_number or 0)} completed with QUALITY_OK.",
        f"Pattern category: {task_category}.",
    ]
    if changed_files:
        summary_lines.append("Confirmed file paths: " + ", ".join(changed_files[:5]))
    if grounded_symbols:
        summary_lines.append("Grounded symbols: " + ", ".join(grounded_symbols[:5]))
    if tool_sequence:
        summary_lines.append("Tool sequence: " + " -> ".join(tool_sequence[:8]))
    if commands:
        summary_lines.append("Validation/commands: " + " | ".join(commands[:3]))
    if quality_gate.get("message"):
        summary_lines.append(f"Quality gate: {str(quality_gate.get('message')).strip()}")
    tags = sorted(
        {
            "aix",
            "institutional-memory",
            "code-pattern",
            task_category,
            *(_path_tags(changed_files)),
            *(f"tool:{tool}" for tool in tool_sequence[:6]),
        }
    )
    metadata_json = {
        "knowledge_type": "code_pattern",
        "authoritative": False,
        "review_status": "pending_human_confirmation",
        "requires_human_confirmation": True,
        "source_run": result.run_id,
        "source_task": int(result.task_number or 0),
        "source_task_id": result.task_id,
        "quality_gate": {
            "code": str(quality_gate.get("code") or "").strip() or None,
            "message": str(quality_gate.get("message") or "").strip() or None,
        },
        "task_category": task_category,
        "confirmed_file_paths": changed_files,
        "grounded_symbols": grounded_symbols,
        "tool_sequence": tool_sequence,
        "command_texts": commands[:5],
    }
    metadata_json["knowledge_current"] = build_task_knowledge_metadata(
        knowledge_type="code_pattern",
        authority="derived",
        review_status="pending_human_confirmation",
        summary=summary_lines[0],
        rationale=(
            "Derived automatically from a QUALITY_OK AIX run. Review before treating as authoritative engineering guidance."
        ),
        source_task=int(result.task_number or 0) or None,
        source_task_id=result.task_id,
        source_run=result.run_id,
        source_refs=[
            f"workhub://tasks/{int(result.task_number or 0)}",
            f"aix://runs/{str(result.run_id or '').strip()}",
        ],
        tags=tags,
    )
    metadata_json["pattern_hash"] = _pattern_hash(metadata_json)
    return {
        "title": title,
        "content": "\n".join(summary_lines),
        "kind": "code_pattern",
        "tags": tags,
        "uri": f"aix://runs/{result.run_id}",
        "metadata_json": metadata_json,
    }


def _knowledge_comment_body(candidate: dict[str, Any]) -> str:
    return "\n".join(
        [
            "[aix-knowledge-draft]",
            str(candidate.get("title") or "").strip(),
            str(candidate.get("content") or "").strip(),
            json.dumps(dict(candidate.get("metadata_json") or {}), sort_keys=True),
        ]
    )


def _knowledge_query(result: RunResult) -> str:
    evidence = _as_dict(result.evidence)
    changed_files = [str(item).strip() for item in list(evidence.get("changed_files") or []) if str(item).strip()]
    runtime = _as_dict(_as_dict(result.telemetry).get("runtime"))
    tool_sequence = [str(item).strip() for item in list(runtime.get("tool_sequence") or []) if str(item).strip()]
    task_category = _task_category(result=result, changed_files=changed_files)
    tokens = [task_category]
    tokens.extend(path.split("/")[-1] for path in changed_files[:3])
    tokens.extend(tool_sequence[:2])
    return " ".join(item for item in tokens if item)


def _task_category(*, result: RunResult, changed_files: list[str]) -> str:
    if any("/routers/" in path or path.startswith("backend/routers/") for path in changed_files):
        return "router_change"
    if any("/tests/" in path or path.startswith("backend/tests/") for path in changed_files):
        return "test_change"
    if any("/services/" in path or path.startswith("backend/services/") for path in changed_files):
        return "service_change"
    quality_gate = _as_dict((result.evidence or {}).get("quality_gate"))
    declared = str(_as_dict(quality_gate.get("details")).get("task_intent") or "").strip()
    return declared or "code_change"


def _path_tags(paths: list[str]) -> list[str]:
    tags: list[str] = []
    for path in paths[:5]:
        normalized = path.replace("\\", "/").strip("/")
        if not normalized:
            continue
        tags.append(f"path:{normalized}")
        first = normalized.split("/", 1)[0]
        if first:
            tags.append(f"area:{first}")
    return tags


def _pattern_hash(payload: dict[str, Any]) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _knowledge_ref(*, project_id: str, knowledge_id: str | None) -> str | None:
    if not knowledge_id:
        return None
    return f"workhub://projects/{project_id}/knowledge/{knowledge_id}"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


__all__ = ["WritebackOutcome", "writeback_run_result"]
