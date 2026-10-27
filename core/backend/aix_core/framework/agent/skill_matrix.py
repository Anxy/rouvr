"""AIX Framework Skill Matrix & Capability Permissions.

Sprint: F7 Agent Framework (F7-P3)
Grounded in: agent_roles.py UNIVERSAL_PROHIBITIONS, FIVE_ROLE_DECISIONS, AgentCapabilityGrant.
"""

from __future__ import annotations

from backend.aix_core.framework.agent.models import (
    Permission,
    PermissionEffect,
    Role,
    Skill,
)
from backend.aix_core.runtime.governance.experimental.agent_roles import (
    FIVE_ROLE_DECISIONS,
    UNIVERSAL_PROHIBITIONS,
    AgentRoleId,
    get_role_decision,
)


class SkillMatrix:
    """Canonical matrix governing skills, capabilities, and prohibitions for each agent role."""

    @staticmethod
    def universal_prohibitions() -> tuple[str, ...]:
        return UNIVERSAL_PROHIBITIONS

    @classmethod
    def skills_for_role(cls, role: Role | str) -> tuple[Skill, ...]:
        role_str = role.value if isinstance(role, Role) else str(role)
        decision = get_role_decision(role_str)
        if decision is None:
            return ()

        # Map role actions to canonical skills
        skills: list[Skill] = []
        for action in decision.may:
            skills.append(
                Skill(
                    skill_id=f"skill_{role_str}_{action}",
                    name=action.replace("_", " ").title(),
                    description=f"Authorized action for {role_str}",
                    required_capabilities=("workspace", "evidence"),
                    required_permissions=(f"perm_{action}",),
                    prohibited_effects=decision.must_not,
                )
            )
        return tuple(skills)

    @classmethod
    def permissions_for_role(cls, role: Role | str) -> tuple[Permission, ...]:
        role_str = role.value if isinstance(role, Role) else str(role)
        decision = get_role_decision(role_str)
        if decision is None:
            return ()

        perms: list[Permission] = []
        for action in decision.may:
            perms.append(
                Permission(
                    permission_id=f"perm_{action}",
                    scope=f"action.{action}",
                    effect=PermissionEffect.ALLOW,
                    description=f"Allows {action}",
                )
            )
        for prohibited in decision.must_not:
            perms.append(
                Permission(
                    permission_id=f"deny_{prohibited}",
                    scope=f"prohibition.{prohibited}",
                    effect=PermissionEffect.DENY,
                    description=f"Explicit prohibition of {prohibited}",
                )
            )
        return tuple(perms)

    @classmethod
    def is_effect_prohibited(cls, role: Role | str, effect: str) -> bool:
        role_str = role.value if isinstance(role, Role) else str(role)
        if effect in UNIVERSAL_PROHIBITIONS:
            return True
        decision = get_role_decision(role_str)
        if decision is None:
            return True
        return effect in decision.must_not

    @classmethod
    def is_operation_allowed(cls, role: Role | str, operation: str) -> bool:
        role_str = role.value if isinstance(role, Role) else str(role)
        decision = get_role_decision(role_str)
        if decision is None:
            return False
        if cls.is_effect_prohibited(role, operation):
            return False
        return operation in decision.may
