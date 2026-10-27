"""AIX-E2E-CYCLE-001M-W01: correlation and structured logs.

`CorrelationContextV1` carries the full task->cycle->step->attempt->model/tool->event->
evidence chain. `build_log_record` fails closed if a log at a given `stage` is missing
the identifiers that stage implies — a "step"-stage log with no `step_id` is a bug in the
caller, not something to log anyway with a blank field.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

LogStage = Literal["task", "cycle", "step", "attempt", "event", "evidence"]

# Each stage requires every identifier at or above it in the chain to be present.
_STAGE_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "task": ("task_id",),
    "cycle": ("task_id", "cycle_id"),
    "step": ("task_id", "cycle_id", "step_id"),
    "attempt": ("task_id", "cycle_id", "step_id", "attempt_id"),
    "event": ("task_id", "cycle_id", "event_id"),
    "evidence": ("task_id", "cycle_id", "evidence_ref"),
}


class CorrelationError(ValueError):
    pass


@dataclass(frozen=True)
class CorrelationContextV1:
    tenant_id: str
    correlation_id: str
    task_id: str | None = None
    objective_id: str | None = None
    cycle_id: str | None = None
    step_id: str | None = None
    attempt_id: str | None = None
    model_ref: str | None = None
    tool_ref: str | None = None
    event_id: str | None = None
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.tenant_id.strip():
            raise CorrelationError("tenant_id is required")
        if not self.correlation_id or not self.correlation_id.strip():
            raise CorrelationError("correlation_id is required")

    def as_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "tenant_id": self.tenant_id,
                "correlation_id": self.correlation_id,
                "task_id": self.task_id,
                "objective_id": self.objective_id,
                "cycle_id": self.cycle_id,
                "step_id": self.step_id,
                "attempt_id": self.attempt_id,
                "model_ref": self.model_ref,
                "tool_ref": self.tool_ref,
                "event_id": self.event_id,
                "evidence_ref": self.evidence_ref,
            }.items()
            if value is not None
        }


def build_log_record(
    *,
    context: CorrelationContextV1,
    stage: LogStage,
    level: Literal["debug", "info", "warning", "error"],
    message: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    required = _STAGE_REQUIRED_FIELDS[stage]
    context_dict = context.as_dict()
    missing = [field for field in required if not context_dict.get(field)]
    if missing:
        raise CorrelationError(f"log stage {stage!r} requires fields {missing} in the correlation context")

    record: dict[str, Any] = {
        "stage": stage,
        "level": level,
        "message": message,
        **context_dict,
    }
    if extra:
        overlap = set(extra) & set(record)
        if overlap:
            raise CorrelationError(f"extra fields collide with correlation context fields: {sorted(overlap)}")
        record.update(extra)
    return record


__all__ = [
    "LogStage",
    "CorrelationError",
    "CorrelationContextV1",
    "build_log_record",
]
