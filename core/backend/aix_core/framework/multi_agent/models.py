"""AIX Framework Multi-Agent Models.

Sprint: F10 Work Compiler + Multi-Agent Runtime (F10-P4)
Grounded in: F10-MULTI-AGENT-PROTOCOL.md, StepContextCapsuleV1, ActionObservationV1.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.framework.agent.models import Role


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _uuid_str() -> str:
    return str(uuid.uuid4())


class StepContextCapsuleV1(BaseModel):
    """Isolated task context capsule provided to a delegated agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capsule_id: str = Field(default_factory=_uuid_str)
    unit_id: str = Field(min_length=1)
    work_id: str = Field(min_length=1)
    assigned_role: Role
    purpose: str = Field(min_length=1)
    target_scope: tuple[str, ...] = Field(min_length=1)
    acceptance_criteria: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    context_data: dict[str, Any] = Field(default_factory=dict)
    capsule_hash: str = ""
    created_at: str = Field(default_factory=_utc_now_iso)

    def compute_hash(self) -> str:
        data = self.model_dump(mode="json", exclude={"capsule_hash"})
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ActionObservationV1(BaseModel):
    """Structured observation and evidence returned from agent execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_id: str = Field(default_factory=_uuid_str)
    capsule_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    status: Literal["SUCCESS", "FAILURE", "BLOCKED"]
    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    touched_files: tuple[str, ...] = ()
    error_message: str | None = None
    observation_hash: str = ""
    created_at: str = Field(default_factory=_utc_now_iso)

    def compute_hash(self) -> str:
        data = self.model_dump(mode="json", exclude={"observation_hash"})
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MultiAgentHandoffRequest(BaseModel):
    """Governed handoff request transferring responsibility across roles."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    handoff_id: str = Field(default_factory=_uuid_str)
    from_role: Role
    to_role: Role
    unit_id: str = Field(min_length=1)
    work_id: str = Field(min_length=1)
    context_capsule: StepContextCapsuleV1
    sealed_evidence_refs: tuple[str, ...] = ()
    created_at: str = Field(default_factory=_utc_now_iso)


class MultiAgentHandoffResult(BaseModel):
    """Governed result of a multi-agent handoff."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    handoff_id: str = Field(min_length=1)
    from_role: Role
    to_role: Role
    status: Literal["ACCEPTED", "REJECTED", "COMPLETED", "FAILED"]
    observation: ActionObservationV1 | None = None
    detail: str = ""
    completed_at: str = Field(default_factory=_utc_now_iso)
