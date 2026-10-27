from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import BaseModel, Field

ObserverEventType = Literal["task_snapshot", "task_event", "task_outcome"]
ObserverResultClass = Literal[
    "runnable",
    "packet_repair_needed",
    "completed",
    "blocked",
    "failed",
    "skipped",
    "stale_or_human",
]
ObserverRootCauseLayer = Literal[
    "task_packet",
    "planner",
    "executor",
    "validation",
    "environment",
    "deploy",
    "coordination",
    "unknown",
]


class TaskObserverActionCurrent(BaseModel):
    action: str = Field(min_length=1)
    status: str | None = None
    detail: str | None = None
    refs: list[str] = Field(default_factory=list)


class TaskObserverEntryCurrent(BaseModel):
    schema_version: Literal["task.observer.entry.current"] = "task.observer.entry.current"
    suite: Literal["aix_queue_orchestrator"] = "aix_queue_orchestrator"
    experiment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    generated_at_utc: str = Field(min_length=1)
    task_number: int = Field(ge=1)
    task_title: str | None = None
    task_status: str | None = None
    event_type: ObserverEventType = "task_event"
    event_status: str = Field(min_length=1)
    actor_role: str = Field(min_length=1)
    actor_model: str | None = None
    task_packet_quality: dict[str, Any] = Field(default_factory=dict)
    readiness: dict[str, Any] = Field(default_factory=dict)
    admission: dict[str, Any] = Field(default_factory=dict)
    selection_reason: str | None = None
    packet_repair_applied: bool = False
    packet_repair_types: list[str] = Field(default_factory=list)
    actions: list[TaskObserverActionCurrent] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)
    result_class: ObserverResultClass | None = None
    blocker_class: str | None = None
    root_cause_layer: ObserverRootCauseLayer | None = None
    reason_code: str | None = None
    reason_summary: str | None = None
    retry_count: int = Field(default=0, ge=0)
    required_deploy: bool = False
    required_coordination: bool = False
    refs: list[str] = Field(default_factory=list)
    improvement_notes: list[str] = Field(default_factory=list)


class TaskObserverLatestCurrent(BaseModel):
    schema_version: Literal["task.observer.latest.current"] = "task.observer.latest.current"
    suite: Literal["aix_queue_orchestrator"] = "aix_queue_orchestrator"
    experiment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    generated_at_utc: str = Field(min_length=1)
    ledger_path: str = Field(min_length=1)
    entry_count: int = Field(ge=0)
    task_counts: dict[str, int] = Field(default_factory=dict)
    result_class_counts: dict[str, int] = Field(default_factory=dict)
    blocker_class_counts: dict[str, int] = Field(default_factory=dict)
    latest_task_number: int | None = None
    latest_event_type: ObserverEventType | None = None
    latest_reason_code: str | None = None


def _clean_str_list(items: list[str] | None) -> list[str]:
    return [str(item).strip() for item in list(items or []) if str(item).strip()]


def build_task_observer_entry(
    *,
    experiment_id: str,
    session_id: str,
    generated_at_utc: str,
    task_number: int,
    actor_role: str,
    event_status: str,
    task_title: str | None = None,
    task_status: str | None = None,
    event_type: str | None = None,
    actor_model: str | None = None,
    task_packet_quality: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
    admission: dict[str, Any] | None = None,
    selection_reason: str | None = None,
    packet_repair_applied: bool = False,
    packet_repair_types: list[str] | None = None,
    actions: list[dict[str, Any]] | None = None,
    validation_commands: list[str] | None = None,
    result_class: str | None = None,
    blocker_class: str | None = None,
    root_cause_layer: str | None = None,
    reason_code: str | None = None,
    reason_summary: str | None = None,
    retry_count: int = 0,
    required_deploy: bool = False,
    required_coordination: bool = False,
    refs: list[str] | None = None,
    improvement_notes: list[str] | None = None,
) -> dict[str, Any]:
    payload = TaskObserverEntryCurrent(
        experiment_id=str(experiment_id or "").strip(),
        session_id=str(session_id or "").strip(),
        generated_at_utc=str(generated_at_utc or "").strip(),
        task_number=int(task_number),
        task_title=str(task_title or "").strip() or None,
        task_status=str(task_status or "").strip() or None,
        event_type=str(event_type or "task_event").strip() or "task_event",  # type: ignore[arg-type]
        event_status=str(event_status or "").strip(),
        actor_role=str(actor_role or "").strip(),
        actor_model=str(actor_model or "").strip() or None,
        task_packet_quality=dict(task_packet_quality or {}),
        readiness=dict(readiness or {}),
        admission=dict(admission or {}),
        selection_reason=str(selection_reason or "").strip() or None,
        packet_repair_applied=bool(packet_repair_applied),
        packet_repair_types=_clean_str_list(packet_repair_types),
        actions=[
            TaskObserverActionCurrent.model_validate(dict(item)).model_dump(mode="json") for item in list(actions or [])
        ],
        validation_commands=_clean_str_list(validation_commands),
        result_class=(str(result_class or "").strip() or None),  # type: ignore[arg-type]
        blocker_class=str(blocker_class or "").strip() or None,
        root_cause_layer=(str(root_cause_layer or "").strip() or None),  # type: ignore[arg-type]
        reason_code=str(reason_code or "").strip() or None,
        reason_summary=str(reason_summary or "").strip() or None,
        retry_count=int(retry_count or 0),
        required_deploy=bool(required_deploy),
        required_coordination=bool(required_coordination),
        refs=_clean_str_list(refs),
        improvement_notes=_clean_str_list(improvement_notes),
    )
    return payload.model_dump(mode="json")


def build_task_observer_latest(
    *,
    experiment_id: str,
    session_id: str,
    generated_at_utc: str,
    ledger_path: str,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    task_counts: dict[str, int] = {}
    result_class_counts: dict[str, int] = {}
    blocker_class_counts: dict[str, int] = {}
    latest_task_number: int | None = None
    latest_event_type: ObserverEventType | None = None
    latest_reason_code: str | None = None

    for item in list(entries):
        task_status = str(item.get("task_status") or "").strip() or "unknown"
        task_counts[task_status] = task_counts.get(task_status, 0) + 1

        result_class = str(item.get("result_class") or "").strip()
        if result_class:
            result_class_counts[result_class] = result_class_counts.get(result_class, 0) + 1

        blocker_class = str(item.get("blocker_class") or "").strip()
        if blocker_class:
            blocker_class_counts[blocker_class] = blocker_class_counts.get(blocker_class, 0) + 1

        latest_task_number = int(item.get("task_number") or 0) or latest_task_number
        event_type_value = str(item.get("event_type") or "").strip()
        if event_type_value in {"task_snapshot", "task_event", "task_outcome"}:
            latest_event_type = cast(ObserverEventType, event_type_value)
        latest_reason_code = str(item.get("reason_code") or "").strip() or latest_reason_code

    payload = TaskObserverLatestCurrent(
        experiment_id=str(experiment_id or "").strip(),
        session_id=str(session_id or "").strip(),
        generated_at_utc=str(generated_at_utc or "").strip(),
        ledger_path=str(ledger_path or "").strip(),
        entry_count=len(list(entries)),
        task_counts=task_counts,
        result_class_counts=result_class_counts,
        blocker_class_counts=blocker_class_counts,
        latest_task_number=latest_task_number,
        latest_event_type=latest_event_type,
        latest_reason_code=latest_reason_code,
    )
    return payload.model_dump(mode="json")


__all__ = [
    "ObserverEventType",
    "ObserverResultClass",
    "ObserverRootCauseLayer",
    "TaskObserverActionCurrent",
    "TaskObserverEntryCurrent",
    "TaskObserverLatestCurrent",
    "build_task_observer_entry",
    "build_task_observer_latest",
]
