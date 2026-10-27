from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DEVJOB_STATE_MACHINE_VERSION_Current = "devjob_state.current"
DevJobState = Literal[
    "queued",
    "running",
    "editing",
    "testing",
    "ready_for_human",
    "verifying",
    "verified",
    "needs_fix",
    "failed",
    "needs_input",
]


class DevJobTransitionEventCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["devjob_state.current"]
    sequence: int = Field(..., ge=1)
    from_state: DevJobState
    to_state: DevJobState
    reason_code: str = Field(..., min_length=1)
    correlation_id: str = Field(..., min_length=1)
    occurred_at_utc: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def validate_transition_event(payload: dict) -> DevJobTransitionEventCurrent:
    return DevJobTransitionEventCurrent.model_validate(payload)
