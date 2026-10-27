from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AixExecutionPacketSenderCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)
    provider: str | None = None
    model: str | None = None


class AixExecutionPacketTargetCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str | None = None
    role: str | None = None

    @model_validator(mode="after")
    def _ensure_target_present(self) -> AixExecutionPacketTargetCurrent:
        if not self.agent_id and not self.role:
            raise ValueError("target.agent_id or target.role is required")
        return self


class AixExecutionPacketStepCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(..., ge=1)
    status: Literal["queued", "running", "completed", "failed", "blocked"]
    summary: str = Field(..., min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class AixExecutionPacketCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_name: Literal["aix_execution_packet_current"] = "aix_execution_packet_current"
    schema_version: Literal["current"] = "current"
    packet_id: str = Field(..., min_length=1, max_length=128)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    causation_id: str | None = None
    tenant_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    idempotency_key: str | None = None
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    prompt_profile: str = Field(default="default", min_length=1, max_length=64)
    sender: AixExecutionPacketSenderCurrent
    target: AixExecutionPacketTargetCurrent
    step: AixExecutionPacketStepCurrent
