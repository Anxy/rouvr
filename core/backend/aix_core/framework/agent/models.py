"""AIX Framework Agent Models & Canonical Types.

Sprint: F7 Agent Framework (F7-P1)
Grounded in: agent_roles.py, GovernedProviderRouter, AgentSpec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    PLANNER = "planner"
    EXECUTOR_CODER = "executor_coder"
    VERIFIER = "verifier"
    RECOVERY_PLANNER = "recovery_planner"
    REVIEW_PACKET_CURATOR = "review_packet_curator"


class PermissionEffect(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


@dataclass(frozen=True)
class Permission:
    """Explicit permission grant or denial scoped to capabilities/operations."""

    permission_id: str
    scope: str
    effect: PermissionEffect = PermissionEffect.ALLOW
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "permission_id": self.permission_id,
            "scope": self.scope,
            "effect": self.effect.value,
            "description": self.description,
        }


@dataclass(frozen=True)
class Skill:
    """Reusable skill profile specifying required capabilities and prohibited effects."""

    skill_id: str
    name: str
    description: str
    required_capabilities: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()
    prohibited_effects: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "required_capabilities": list(self.required_capabilities),
            "required_permissions": list(self.required_permissions),
            "prohibited_effects": list(self.prohibited_effects),
        }


@dataclass(frozen=True)
class Persona:
    """Agent persona embodying system instructions and autonomy bounds."""

    persona_id: str
    name: str
    role: Role
    system_prompt: str
    description: str = ""
    autonomy_cap: str = "L1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona_id": self.persona_id,
            "name": self.name,
            "role": self.role.value,
            "description": self.description,
            "autonomy_cap": self.autonomy_cap,
        }


@dataclass(frozen=True)
class Agent:
    """Principal agent instance combining role, persona, skills, and permissions."""

    agent_id: str
    role: Role
    persona: Persona
    skills: tuple[Skill, ...] = ()
    permissions: tuple[Permission, ...] = ()
    autonomy_cap: str = "L1"
    can_approve: bool = False
    can_escalate: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.role.value,
            "persona": self.persona.to_dict(),
            "skills": [s.to_dict() for s in self.skills],
            "permissions": [p.to_dict() for p in self.permissions],
            "autonomy_cap": self.autonomy_cap,
            "can_approve": self.can_approve,
            "can_escalate": self.can_escalate,
        }
