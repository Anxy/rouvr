from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.models.evidence_pack_models import EvidencePackCurrent

OTHER_LABEL = "__other__"


def build_structured_log_event(
    *,
    project_id: str,
    task_number: int,
    job_id: str,
    correlation_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not project_id:
        raise ValueError("project_id is required")
    if task_number < 1:
        raise ValueError("task_number must be >= 1")
    if not job_id:
        raise ValueError("job_id is required")
    if not correlation_id:
        raise ValueError("correlation_id is required")
    if not event_type:
        raise ValueError("event_type is required")
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "project_id": project_id,
        "task_number": task_number,
        "job_id": job_id,
        "correlation_id": correlation_id,
        "event_type": event_type,
        "payload": dict(payload or {}),
    }


def _classify_tool_call_outcome(item: dict[str, Any]) -> str:
    if bool(item.get("ok", False)):
        return "success"
    error = str(item.get("error") or "").lower()
    if "conflict" in error:
        return "conflict"
    if "escalation" in error:
        return "escalation"
    if "policy_denied" in error or "command_denied" in error:
        return "policy_denied"
    return "error"


def build_tool_call_telemetry_events(
    *,
    project_id: str,
    task_number: int,
    job_id: str,
    correlation_id: str,
    transcript: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in transcript:
        diagnostics = item.get("diagnostics") if isinstance(item.get("diagnostics"), dict) else {}
        payload = {
            "index": int(item.get("index", 0)),
            "tool": str(item.get("tool") or "unknown"),
            "ok": bool(item.get("ok", False)),
            "outcome": _classify_tool_call_outcome(item),
            "latency_ms": int(diagnostics.get("elapsed_ms", 0) or 0),
            "error": str(item.get("error") or ""),
        }
        events.append(
            build_structured_log_event(
                project_id=project_id,
                task_number=task_number,
                job_id=job_id,
                correlation_id=correlation_id,
                event_type="devexecutor.tool_call",
                payload=payload,
            )
        )
    return events


class CardinalityLimiter:
    def __init__(self, max_values_per_label: int = 20):
        if max_values_per_label < 1:
            raise ValueError("max_values_per_label must be >= 1")
        self.max_values_per_label = max_values_per_label
        self._seen: dict[str, set[str]] = {}

    def normalize(self, label_name: str, value: str) -> str:
        normalized = str(value or "").strip() or "unknown"
        seen = self._seen.setdefault(label_name, set())
        if normalized in seen:
            return normalized
        if len(seen) >= self.max_values_per_label:
            return OTHER_LABEL
        seen.add(normalized)
        return normalized

    def seen_values(self, label_name: str) -> set[str]:
        return set(self._seen.get(label_name, set()))


class DevExecutorMetrics:
    def __init__(self, *, limiter: CardinalityLimiter | None = None):
        self.limiter = limiter or CardinalityLimiter()
        self.tool_calls_total: dict[tuple[str, str, str], int] = {}
        self.tool_call_duration_ms: list[dict[str, Any]] = []
        self.policy_denials_total: dict[str, int] = {}
        self.drift_flags_total: dict[str, int] = {}

    def record_tool_call(self, *, tool: str, state: str, result: str, duration_ms: float) -> None:
        tool_n = self.limiter.normalize("tool", tool)
        state_n = self.limiter.normalize("state", state)
        result_n = self.limiter.normalize("result", result)
        key = (tool_n, state_n, result_n)
        self.tool_calls_total[key] = self.tool_calls_total.get(key, 0) + 1
        self.tool_call_duration_ms.append(
            {"tool": tool_n, "state": state_n, "result": result_n, "duration_ms": duration_ms}
        )

    def record_policy_denial(self, *, reason: str) -> None:
        reason_n = self.limiter.normalize("policy_reason", reason)
        self.policy_denials_total[reason_n] = self.policy_denials_total.get(reason_n, 0) + 1

    def record_drift_flag(self, *, flag: str) -> None:
        flag_n = self.limiter.normalize("drift_flag", flag)
        self.drift_flags_total[flag_n] = self.drift_flags_total.get(flag_n, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "tool_calls_total": {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in sorted(self.tool_calls_total.items())},
            "tool_call_duration_ms": list(self.tool_call_duration_ms),
            "policy_denials_total": dict(sorted(self.policy_denials_total.items())),
            "drift_flags_total": dict(sorted(self.drift_flags_total.items())),
            "cardinality_limits": {"max_values_per_label": self.limiter.max_values_per_label},
        }


@dataclass
class SpanRecord:
    span_id: str
    parent_span_id: str | None
    name: str
    correlation_id: str
    attributes: dict[str, Any]
    status: str
    started_at_utc: str
    ended_at_utc: str | None = None
    error: str | None = None


class DevExecutorTracer:
    def __init__(self, *, correlation_id: str):
        self.correlation_id = correlation_id
        self.spans: list[SpanRecord] = []

    def start_span(
        self, *, name: str, parent_span_id: str | None = None, attributes: dict[str, Any] | None = None
    ) -> str:
        span_id = uuid.uuid4().hex
        self.spans.append(
            SpanRecord(
                span_id=span_id,
                parent_span_id=parent_span_id,
                name=name,
                correlation_id=self.correlation_id,
                attributes=dict(attributes or {}),
                status="running",
                started_at_utc=datetime.now(UTC).isoformat(),
            )
        )
        return span_id

    def end_span(self, *, span_id: str, status: str = "ok", error: str | None = None) -> None:
        for span in self.spans:
            if span.span_id == span_id:
                span.status = status
                span.error = error
                span.ended_at_utc = datetime.now(UTC).isoformat()
                return
        raise KeyError(f"SPAN_NOT_FOUND span_id={span_id}")

    def export(self) -> list[dict[str, Any]]:
        return [
            {
                "span_id": s.span_id,
                "parent_span_id": s.parent_span_id,
                "name": s.name,
                "correlation_id": s.correlation_id,
                "attributes": dict(s.attributes),
                "status": s.status,
                "started_at_utc": s.started_at_utc,
                "ended_at_utc": s.ended_at_utc,
                "error": s.error,
            }
            for s in self.spans
        ]


def build_observability_link_refs(
    *,
    correlation_id: str,
    logs_ref: str,
    metrics_ref: str,
    traces_ref: str,
) -> dict[str, Any]:
    return {
        "correlation_id": correlation_id,
        "logs_ref": logs_ref,
        "metrics_ref": metrics_ref,
        "traces_ref": traces_ref,
    }


def attach_observability_links_to_evidence(
    *,
    evidence_pack: EvidencePackCurrent,
    observability_links: dict[str, Any],
) -> EvidencePackCurrent:
    payload = evidence_pack.model_dump(mode="python")
    metadata = dict(payload.get("metadata") or {})
    metadata["observability"] = dict(observability_links)
    payload["metadata"] = metadata
    return EvidencePackCurrent.model_validate(payload)
