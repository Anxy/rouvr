"""RuntimeContextV1: the resolved, request-scoped output of composing a persona runtime."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.persona_runtime.entities import AUTONOMY_LEVEL, PERSONA_RUNTIME_SCHEMA_VERSION
from typing import Literal


class RuntimeContextV1(BaseModel):
    """Resolved output of compose(persona, capabilities, policy, overlay, objective, budget).

    granted_tools is always (requested tools) INTERSECT (granted tools) -- never the granted
    set alone. denied_requested_tools and missing_capability_ids are carried through for
    transparency/evidence, not enforcement -- enforcement already happened in the composer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["persona_runtime.v1"] = PERSONA_RUNTIME_SCHEMA_VERSION

    objective: str
    budget: dict[str, Any] = Field(default_factory=dict)

    persona_id: str
    mission: str
    communication_style: tuple[str, ...]
    reasoning_style: tuple[str, ...]

    capability_ids: tuple[str, ...]
    knowledge: tuple[str, ...]
    verification_rules: tuple[str, ...]
    tool_invocation_protocol: tuple[str, ...] = Field(default_factory=tuple)
    missing_capability_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Capability ids the persona requested but that were not present in compose()'s capabilities arg.",
    )

    policy_id: str
    overlay_id: str | None
    autonomy_cap: AUTONOMY_LEVEL
    constraints: tuple[str, ...] = Field(default_factory=tuple)

    requested_tools: tuple[str, ...]
    granted_tools: tuple[str, ...]
    denied_requested_tools: tuple[str, ...] = Field(
        default_factory=tuple, description="requested_tools - granted_tools, for transparency."
    )

    evidence_requirements: tuple[str, ...]
