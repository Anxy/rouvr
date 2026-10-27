"""AIX Framework Compiler - Stage 1: Intake & Normalization.

Sprint: F10 Work Compiler (F10-P1)
Grounded in: F10-COMPILER-STAGE-CONTRACTS.md, ObjectiveEnvelopeV1.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.runtime.objective.contracts import ObjectiveEnvelopeV1


class CompilerIntakeSnapshot(BaseModel):
    """Normalized and sealed snapshot of admitted objective intake."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intake_id: str
    objective_id: str
    tenant_id: str
    project_id: str
    raw_intent_text: str
    normalized_task_class: str
    definition_of_done: tuple[str, ...]
    allowed_files: tuple[str, ...]
    environment_scope: str = Field(default="workspace")
    allowed_effects: tuple[str, ...] = Field(default_factory=tuple)
    forbidden_effects: tuple[str, ...] = Field(default_factory=tuple)
    budget_ceiling: dict[str, Any] = Field(default_factory=dict)
    intake_hash: str


class IntakeStage:
    """Stage 1: Validates and normalizes admitted objective envelope."""

    @classmethod
    def run(cls, envelope: ObjectiveEnvelopeV1) -> CompilerIntakeSnapshot:
        raw_intent = envelope.definition_of_done
        tenant_id = envelope.tenant_id
        project_id = envelope.project_id
        objective_id = envelope.id
        dods = (envelope.definition_of_done,)
        allowed_files = tuple(envelope.repository_scope)

        # Compute deterministic intake hash
        hasher = hashlib.sha256()
        hasher.update(objective_id.encode("utf-8"))
        hasher.update(raw_intent.encode("utf-8"))
        intake_hash = hasher.hexdigest()

        return CompilerIntakeSnapshot(
            intake_id=f"intake_{intake_hash[:16]}",
            objective_id=objective_id,
            tenant_id=tenant_id,
            project_id=project_id,
            raw_intent_text=raw_intent,
            normalized_task_class=envelope.task_class,
            definition_of_done=dods,
            allowed_files=allowed_files,
            allowed_effects=tuple(envelope.allowed_effects),
            forbidden_effects=tuple(envelope.forbidden_effects),
            intake_hash=intake_hash,
        )
