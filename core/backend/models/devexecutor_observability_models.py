from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DEVEXECUTOR_OBSERVABILITY_SCHEMA_VERSION_Current = "devexecutor.observability.current"


class DevExecutorObservabilityRefCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["devexecutor.observability.current"]
    correlation_id: str = Field(..., min_length=1)
    logs_ref: str = Field(..., min_length=1)
    metrics_ref: str = Field(..., min_length=1)
    traces_ref: str = Field(..., min_length=1)


def validate_observability_ref(payload: dict) -> DevExecutorObservabilityRefCurrent:
    return DevExecutorObservabilityRefCurrent.model_validate(payload)
