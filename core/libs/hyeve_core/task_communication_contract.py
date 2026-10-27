from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

CommunicationChannel = Literal["communication", "coordination"]
CommunicationMessageType = Literal[
    "status_note",
    "question",
    "answer",
    "handoff_note",
    "guidance_request",
    "decision_request",
    "reasoning_trace",
    "tool_intent",
    "tool_observation",
]
CoordinationAction = Literal["request_guidance", "request_decision", "handoff", "informational"]


class TaskCommunicationCurrent(BaseModel):
    schema_version: Literal["task.communication.current"] = "task.communication.current"
    channel: CommunicationChannel = "communication"
    message_type: CommunicationMessageType = "status_note"
    thread_key: str | None = None
    in_reply_to: str | None = None
    summary: str | None = None
    refs: list[str] = Field(default_factory=list)


class TaskCoordinationProjection(BaseModel):
    schema_version: Literal["task.coordination.projection.current"] = "task.coordination.projection.current"
    actionable: bool = False
    coordination_action: CoordinationAction = "informational"
    thread_key: str | None = None
    summary: str | None = None
    message_type: CommunicationMessageType = "status_note"
    refs: list[str] = Field(default_factory=list)


class TaskCommunicationEventEnvelope(BaseModel):
    schema_version: Literal["task.communication.event.current"] = "task.communication.event.current"
    task_id: str
    task_number: int
    author_id: str
    body: str
    communication: TaskCommunicationCurrent
    coordination: TaskCoordinationProjection | None = None


class TaskCommunicationPublication(BaseModel):
    schema_version: Literal["task.communication.publication.current"] = "task.communication.publication.current"
    stream_key: str
    stream_ref: str
    task_number: int
    event: TaskCommunicationEventEnvelope


class TaskCommunicationPublicationResult(BaseModel):
    schema_version: Literal[
        "task.communication.publication_result.current"
    ] = "task.communication.publication_result.current"
    attempted: bool = False
    published: bool = False
    stream_key: str | None = None
    stream_ref: str | None = None
    stream_event_id: str | None = None
    error: str | None = None


def build_task_communication_metadata(
    *,
    channel: str | None,
    message_type: str | None,
    thread_key: str | None = None,
    in_reply_to: str | None = None,
    summary: str | None = None,
    refs: list[str] | None = None,
) -> dict[str, Any] | None:
    normalized_channel = str(channel or "").strip()
    normalized_type = str(message_type or "").strip()
    if not normalized_channel and not normalized_type:
        return None
    payload = TaskCommunicationCurrent(
        channel=(normalized_channel or "communication"),  # type: ignore[arg-type]
        message_type=(normalized_type or "status_note"),  # type: ignore[arg-type]
        thread_key=str(thread_key or "").strip() or None,
        in_reply_to=str(in_reply_to or "").strip() or None,
        summary=str(summary or "").strip() or None,
        refs=[str(item).strip() for item in list(refs or []) if str(item).strip()],
    )
    return payload.model_dump()


def project_task_communication(
    *,
    metadata: dict[str, Any] | None,
    body: str | None = None,
) -> dict[str, Any] | None:
    metadata_dict = dict(metadata or {})
    raw = metadata_dict.get("communication_current")
    if not isinstance(raw, dict):
        return None
    payload = TaskCommunicationCurrent.model_validate(raw)
    projected = payload.model_dump()
    if not projected.get("summary"):
        projected["summary"] = str(body or "").strip() or None
    return projected


def coordination_message_is_actionable(*, channel: str | None, message_type: str | None) -> bool:
    normalized_channel = str(channel or "").strip()
    normalized_type = str(message_type or "").strip()
    if normalized_channel != "coordination":
        return False
    return normalized_type in {"guidance_request", "decision_request", "handoff_note"}


def project_task_coordination(
    *,
    metadata: dict[str, Any] | None,
    body: str | None = None,
) -> dict[str, Any] | None:
    communication = project_task_communication(metadata=metadata, body=body)
    if not isinstance(communication, dict):
        return None
    if str(communication.get("channel") or "").strip() != "coordination":
        return None
    message_type = str(communication.get("message_type") or "status_note").strip() or "status_note"
    coordination_action: CoordinationAction = "informational"
    if message_type == "guidance_request":
        coordination_action = "request_guidance"
    elif message_type == "decision_request":
        coordination_action = "request_decision"
    elif message_type == "handoff_note":
        coordination_action = "handoff"
    payload = TaskCoordinationProjection(
        actionable=coordination_message_is_actionable(
            channel=communication.get("channel"),
            message_type=message_type,
        ),
        coordination_action=coordination_action,
        thread_key=str(communication.get("thread_key") or "").strip() or None,
        summary=str(communication.get("summary") or "").strip() or None,
        message_type=message_type,  # type: ignore[arg-type]
        refs=[str(item).strip() for item in list(communication.get("refs") or []) if str(item).strip()],
    )
    return payload.model_dump()


def build_task_communication_event_envelope(
    *,
    task_id: str,
    task_number: int,
    author_id: str,
    body: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    communication = project_task_communication(metadata=metadata, body=body)
    if not isinstance(communication, dict):
        return None
    coordination = project_task_coordination(metadata=metadata, body=body)
    payload = TaskCommunicationEventEnvelope(
        task_id=str(task_id or "").strip(),
        task_number=int(task_number),
        author_id=str(author_id or "").strip(),
        body=str(body or "").strip(),
        communication=TaskCommunicationCurrent.model_validate(communication),
        coordination=(
            TaskCoordinationProjection.model_validate(coordination) if isinstance(coordination, dict) else None
        ),
    )
    return payload.model_dump()


def build_task_communication_publication(
    *,
    project_id: str,
    task_id: str,
    task_number: int,
    author_id: str,
    body: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    event = build_task_communication_event_envelope(
        task_id=task_id,
        task_number=task_number,
        author_id=author_id,
        body=body,
        metadata=metadata,
    )
    if not isinstance(event, dict):
        return None
    normalized_project_id = str(project_id or "").strip()
    payload = TaskCommunicationPublication(
        stream_key=f"task:{normalized_project_id}:{int(task_number)}:comm",
        stream_ref=f"workhub://projects/{normalized_project_id}/tasks/{int(task_number)}/communication/stream",
        task_number=int(task_number),
        event=TaskCommunicationEventEnvelope.model_validate(event),
    )
    return payload.model_dump()


def build_task_communication_publication_result_metadata(
    *,
    publication_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(publication_result, dict) or not publication_result:
        return None
    payload = TaskCommunicationPublicationResult(
        attempted=bool(publication_result.get("attempted")),
        published=bool(publication_result.get("published")),
        stream_key=str(publication_result.get("stream_key") or "").strip() or None,
        stream_ref=str(publication_result.get("stream_ref") or "").strip() or None,
        stream_event_id=str(publication_result.get("stream_event_id") or "").strip() or None,
        error=str(publication_result.get("error") or "").strip() or None,
    )
    return payload.model_dump()


def project_task_communication_publication(
    *,
    metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    metadata_dict = dict(metadata or {})
    raw = metadata_dict.get("communication_publication_current")
    if not isinstance(raw, dict):
        return None
    payload = TaskCommunicationPublicationResult.model_validate(raw)
    return payload.model_dump()


__all__ = [
    "CommunicationChannel",
    "CommunicationMessageType",
    "CoordinationAction",
    "TaskCommunicationCurrent",
    "TaskCoordinationProjection",
    "TaskCommunicationEventEnvelope",
    "TaskCommunicationPublication",
    "TaskCommunicationPublicationResult",
    "build_task_communication_metadata",
    "build_task_communication_event_envelope",
    "build_task_communication_publication",
    "build_task_communication_publication_result_metadata",
    "coordination_message_is_actionable",
    "project_task_communication",
    "project_task_coordination",
    "project_task_communication_publication",
]
