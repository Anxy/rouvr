"""Persona / Capability / Policy / Overlay entities for the governed persona runtime prototype.

Each entity is a pure data object. None of them grant anything to a runtime
execution by themselves -- Persona and Capability only *request*; Policy is
the sole entity with the authority to *grant*; Overlay can only narrow a
Policy's grant, never widen it. Resolution into an actual grant happens in
composer.py, not here.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PERSONA_RUNTIME_SCHEMA_VERSION = "persona_runtime.v1"
AUTONOMY_LEVEL = Literal["L0", "L1", "L2", "L3", "L4"]
AUTONOMY_ORDER: dict[AUTONOMY_LEVEL, int] = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}


class PersonaV1(BaseModel):
    """Identity only. Requests capabilities by id; grants nothing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["persona_runtime.v1"] = PERSONA_RUNTIME_SCHEMA_VERSION
    persona_id: str = Field(..., min_length=1)
    persona_name: str = Field(..., min_length=1)
    persona_version: str = Field(default="v1", min_length=1)
    mission: str = Field(..., min_length=1)
    communication_style: tuple[str, ...] = Field(default_factory=tuple)
    reasoning_style: tuple[str, ...] = Field(default_factory=tuple)
    requested_capability_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Capability ids this persona wants composed in. A capability passed to "
        "compose() that is not listed here is ignored, not silently granted.",
    )


class CapabilityV1(BaseModel):
    """A unit of skill: knowledge + verification rules. Requests tools; grants nothing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["persona_runtime.v1"] = PERSONA_RUNTIME_SCHEMA_VERSION
    capability_id: str = Field(..., min_length=1)
    capability_name: str = Field(..., min_length=1)
    capability_version: str = Field(default="v1", min_length=1)
    knowledge: tuple[str, ...] = Field(
        default_factory=tuple, description="Best-practice / domain-knowledge lines this capability contributes."
    )
    verification_rules: tuple[str, ...] = Field(
        default_factory=tuple, description="Rules the renderer should surface as acceptance/verification guidance."
    )
    tool_invocation_protocol: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Exact-format, mechanical tool-invocation instructions for tools this capability "
        "requests -- JSON shapes, sequencing rules, forbidden patterns. Kept distinct from `knowledge` "
        "(best-practice prose) and `verification_rules` (acceptance criteria) because in the real system "
        "this content is authored and reviewed on a different cadence: it changes when a tool's calling "
        "convention changes, not when domain best-practice changes.",
    )
    requested_tools: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Tools this capability wants available. A request, not a grant -- the "
        "composer intersects this against Policy's granted_tools.",
    )


class PolicyV1(BaseModel):
    """The only entity with authority to grant anything."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["persona_runtime.v1"] = PERSONA_RUNTIME_SCHEMA_VERSION
    policy_id: str = Field(..., min_length=1)
    policy_version: str = Field(default="v1", min_length=1)
    autonomy_cap: AUTONOMY_LEVEL
    granted_tools: tuple[str, ...] = Field(
        default_factory=tuple, description="The tool ceiling. Nothing outside this set can ever be granted."
    )
    evidence_requirements: tuple[str, ...] = Field(default_factory=tuple)
    constraints: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Non-negotiable operational rules (e.g. no_recursive_agent_spawn). Added post-Sprint-0: "
        "the gap analysis found these don't fit Persona/Capability cleanly and are enforced today, so they "
        "belong here, not omitted.",
    )


class OverlayV1(BaseModel):
    """A tenant-scoped delta on a Policy. Every field is optional; every field can only narrow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["persona_runtime.v1"] = PERSONA_RUNTIME_SCHEMA_VERSION
    overlay_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    autonomy_cap_override: AUTONOMY_LEVEL | None = Field(
        default=None, description="Must be <= the base Policy's autonomy_cap. Composer rejects otherwise."
    )
    granted_tools_override: tuple[str, ...] | None = Field(
        default=None,
        description="If set, must be a subset of the base Policy's granted_tools. Composer rejects otherwise.",
    )
    evidence_requirements_add: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Purely additive -- overlays may only add stricter evidence requirements, never remove one.",
    )
