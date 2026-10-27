from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.clients.opsbus_run_logs_client import opsbus_run_logs_client
from backend.clients.workhub_knowledge_client import workhub_knowledge_client
from backend.services.aix_run_ledger_service import list_run_ledger_records
from backend.services.devexecutor_mutation_tool_taxonomy_service import ORCHESTRATION_REMOVED_MUTATION_TOOLS


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalize_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _coerce_optional_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_utc(value: Any) -> datetime | None:
    text = _normalize_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _report_uri(project_id: str) -> str:
    return f"wh://aix/weekly-failure-patterns/{project_id}"


def _report_title(window_days: int) -> str:
    return f"AIX Weekly Failure Patterns ({int(window_days)}d rolling)"


def _event_ref(project_id: str, run_id: str, task_number: int | None) -> dict[str, Any]:
    ref: dict[str, Any] = {
        "run_id": run_id,
        "run_ref": f"opsbus://runs/{run_id}/logs#aix.devexecutor.run.closed",
    }
    normalized_task_number = _coerce_optional_int(task_number)
    if normalized_task_number and normalized_task_number > 0:
        ref["task_number"] = normalized_task_number
        ref["task_ref"] = f"workhub://projects/{project_id}/tasks/{normalized_task_number}"
    return ref


def _extract_run_closed_event(log_items: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in log_items:
        if str(item.get("message") or "").strip() != "aix.devexecutor.run.closed":
            continue
        metadata = _as_dict(item.get("metadata_json"))
        payload = _as_dict(metadata.get("event_payload"))
        if payload:
            return payload
    return None


def build_weekly_failure_pattern_report(
    *,
    project_id: str,
    events: list[dict[str, Any]],
    window_days: int = 7,
    generated_at: datetime | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(UTC)
    failure_counts: Counter[str] = Counter()
    failure_refs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sequence_counts: Counter[tuple[str, ...]] = Counter()
    sequence_refs: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    status_counts: Counter[str] = Counter()

    for event in events:
        run_id = _normalize_text(event.get("run_id")) or "unknown"
        task_number = _coerce_optional_int(event.get("task_number"))
        ref = _event_ref(project_id, run_id, task_number)
        status = _normalize_text(event.get("status")) or "unknown"
        status_counts[status] += 1

        errors = []
        for item in _as_list(event.get("errors")):
            code = _normalize_text(item)
            if code:
                errors.append(code)
        if not errors:
            quality_gate = _normalize_text(event.get("quality_gate"))
            quality_gate_passed = event.get("quality_gate_passed")
            if quality_gate and quality_gate_passed is False:
                errors.append(f"QUALITY_GATE:{quality_gate}")
            elif status not in {"completed", "done"}:
                errors.append(f"STATUS:{status}")

        for code in errors:
            failure_counts[code] += 1
            if ref not in failure_refs[code]:
                failure_refs[code].append(ref)

        sequence = tuple(str(item).strip() for item in _as_list(event.get("tool_sequence")) if str(item).strip())
        if sequence:
            sequence_counts[sequence] += 1
            if ref not in sequence_refs[sequence]:
                sequence_refs[sequence].append(ref)

    top_failure_codes = [
        {
            "code": code,
            "count": count,
            "refs": failure_refs[code][:3],
        }
        for code, count in sorted(failure_counts.items(), key=lambda item: (-item[1], item[0]))[: max(1, top_n)]
    ]
    top_tool_sequences = [
        {
            "sequence": list(sequence),
            "signature": " -> ".join(sequence),
            "sequence_classification": "audit",
            "legacy_removed_tools_seen": [
                tool for tool in sequence if tool in set(ORCHESTRATION_REMOVED_MUTATION_TOOLS)
            ],
            "count": count,
            "refs": sequence_refs[sequence][:3],
        }
        for sequence, count in sorted(sequence_counts.items(), key=lambda item: (-item[1], list(item[0])))[
            : max(1, top_n)
        ]
    ]
    return {
        "schema_version": "aix.weekly_failure_patterns.current",
        "project_id": project_id,
        "window_days": int(window_days),
        "generated_at_utc": generated.astimezone(UTC).isoformat(),
        "status": "draft",
        "events_considered": len(events),
        "top_failure_codes": top_failure_codes,
        "top_tool_sequences": top_tool_sequences,
        "status_counts": dict(status_counts),
    }


def build_weekly_failure_pattern_knowledge_content(report: dict[str, Any]) -> str:
    lines = [
        "# AIX Weekly Failure Patterns",
        "",
        f"- Window: last {int(report.get('window_days') or 7)} days",
        f"- Generated at UTC: {report.get('generated_at_utc')}",
        f"- Events considered: {int(report.get('events_considered') or 0)}",
        "- Status: draft / non-authoritative",
        "",
        "## Top Failure Codes",
    ]
    failures = _as_list(report.get("top_failure_codes"))
    if not failures:
        lines.extend(["- No recurring failure codes detected in the current window.", ""])
    else:
        for item in failures:
            refs = []
            for ref in _as_list(_as_dict(item).get("refs")):
                run_id = _normalize_text(_as_dict(ref).get("run_id"))
                task_number = _coerce_optional_int(_as_dict(ref).get("task_number"))
                label = f"run {run_id}" if run_id else "run"
                if task_number and task_number > 0:
                    label += f" / task #{task_number}"
                refs.append(label)
            lines.append(
                f"- `{_as_dict(item).get('code')}`: {int(_as_dict(item).get('count') or 0)}"
                + (f" [{'; '.join(refs)}]" if refs else "")
            )
        lines.append("")
    lines.append("## Top Tool Sequences")
    sequences = _as_list(report.get("top_tool_sequences"))
    if not sequences:
        lines.extend(["- No tool-sequence patterns detected in the current window.", ""])
    else:
        for item in sequences:
            refs = []
            for ref in _as_list(_as_dict(item).get("refs")):
                run_id = _normalize_text(_as_dict(ref).get("run_id"))
                task_number = _coerce_optional_int(_as_dict(ref).get("task_number"))
                label = f"run {run_id}" if run_id else "run"
                if task_number and task_number > 0:
                    label += f" / task #{task_number}"
                refs.append(label)
            removed_tools = [
                str(tool).strip()
                for tool in _as_list(_as_dict(item).get("legacy_removed_tools_seen"))
                if str(tool).strip()
            ]
            lines.append(
                f"- `{_as_dict(item).get('signature')}`: {int(_as_dict(item).get('count') or 0)}"
                + (f" [legacy removed tools: {', '.join(removed_tools)}]" if removed_tools else "")
                + (f" [{'; '.join(refs)}]" if refs else "")
            )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


async def collect_recent_run_closed_events(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    window_days: int = 7,
    ledger_limit: int = 200,
    run_ledger_records: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    _ = tenant_id
    since = datetime.now(UTC) - timedelta(days=max(1, int(window_days)))
    records = (
        run_ledger_records
        if run_ledger_records is not None
        else list_run_ledger_records(
            project_id=project_id,
            limit=max(1, ledger_limit),
        )
    )
    recent_records: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()
    for record in records:
        recorded_at = _parse_iso_utc(record.get("recorded_at_utc"))
        run_id = _normalize_text(record.get("run_id"))
        if not run_id or run_id in seen_run_ids:
            continue
        if recorded_at and recorded_at < since:
            continue
        seen_run_ids.add(run_id)
        recent_records.append(record)

    events: list[dict[str, Any]] = []
    for record in recent_records:
        run_id = _normalize_text(record.get("run_id"))
        if not run_id:
            continue
        payload, error = await opsbus_run_logs_client.list_run_logs(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
            correlation_id=correlation_id,
            limit=200,
            offset=0,
        )
        if error:
            return [], error
        event_payload = _extract_run_closed_event(
            [item for item in _as_list(_as_dict(payload).get("items")) if isinstance(item, dict)]
        )
        if event_payload:
            events.append(event_payload)
    return events, None


@dataclass(slots=True)
class WeeklyFailurePatternPublishOutcome:
    success: bool
    report: dict[str, Any]
    knowledge_id: str | None = None
    knowledge_ref: str | None = None
    created: bool = False
    updated: bool = False
    comment_fallback: bool = False
    error: str | None = None


async def publish_weekly_failure_pattern_report(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    actor_id: str,
    task_number: int,
    window_days: int = 7,
    ledger_limit: int = 200,
    top_n: int = 5,
    run_ledger_records: list[dict[str, Any]] | None = None,
    service_token_override: str | None = None,
) -> WeeklyFailurePatternPublishOutcome:
    events, error = await collect_recent_run_closed_events(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        window_days=window_days,
        ledger_limit=ledger_limit,
        run_ledger_records=run_ledger_records,
    )
    if error:
        return WeeklyFailurePatternPublishOutcome(success=False, report={}, error=error)

    report = build_weekly_failure_pattern_report(
        project_id=project_id,
        events=events,
        window_days=window_days,
        top_n=top_n,
    )
    title = _report_title(window_days)
    uri = _report_uri(project_id)
    content = build_weekly_failure_pattern_knowledge_content(report)
    metadata_json = {
        "knowledge": {"status": "draft"},
        "aix_weekly_failure_patterns": report,
        "uri": uri,
    }
    existing, search_error = await workhub_knowledge_client.search_knowledge(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        actor_id=actor_id,
        query=title,
        limit=10,
        service_token_override=service_token_override,
    )
    if search_error:
        return WeeklyFailurePatternPublishOutcome(success=False, report=report, error=search_error)

    knowledge_id: str | None = None
    for item in existing:
        item_dict = _as_dict(item)
        item_meta = _as_dict(item_dict.get("metadata_json"))
        if _normalize_text(item_dict.get("uri")) == uri or _normalize_text(item_meta.get("uri")) == uri:
            knowledge_id = _normalize_text(item_dict.get("id"))
            break
        report_meta = _as_dict(item_meta.get("aix_weekly_failure_patterns"))
        if _normalize_text(report_meta.get("project_id")) == project_id:
            knowledge_id = _normalize_text(item_dict.get("id"))
            break

    if knowledge_id:
        ok, update_error = await workhub_knowledge_client.update_knowledge(
            tenant_id=tenant_id,
            project_id=project_id,
            knowledge_id=knowledge_id,
            correlation_id=correlation_id,
            actor_id=actor_id,
            title=title,
            content=content,
            kind="runbook",
            tags=["aix", "analytics", "failure-patterns", "draft"],
            uri=uri,
            metadata_json=metadata_json,
            service_token_override=service_token_override,
        )
        if ok:
            return WeeklyFailurePatternPublishOutcome(
                success=True,
                report=report,
                knowledge_id=knowledge_id,
                knowledge_ref=f"workhub://projects/{project_id}/knowledge/{knowledge_id}",
                updated=True,
            )
        return WeeklyFailurePatternPublishOutcome(success=False, report=report, error=update_error)

    knowledge_id, create_error = await workhub_knowledge_client.create_knowledge(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        actor_id=actor_id,
        title=title,
        content=content,
        kind="runbook",
        tags=["aix", "analytics", "failure-patterns", "draft"],
        uri=uri,
        metadata_json=metadata_json,
        service_token_override=service_token_override,
    )
    if knowledge_id:
        return WeeklyFailurePatternPublishOutcome(
            success=True,
            report=report,
            knowledge_id=knowledge_id,
            knowledge_ref=f"workhub://projects/{project_id}/knowledge/{knowledge_id}",
            created=True,
        )

    fallback_body = (
        f"{title}\n\n" "Knowledge route unavailable; weekly draft failure-pattern summary follows.\n\n" f"{content}"
    )
    ok, comment_error = await workhub_knowledge_client.add_task_comment(
        tenant_id=tenant_id,
        project_id=project_id,
        task_number=task_number,
        correlation_id=correlation_id,
        actor_id=actor_id,
        body=fallback_body,
        service_token_override=service_token_override,
    )
    if ok:
        return WeeklyFailurePatternPublishOutcome(
            success=True,
            report=report,
            comment_fallback=True,
            error=create_error,
        )
    return WeeklyFailurePatternPublishOutcome(success=False, report=report, error=comment_error or create_error)
