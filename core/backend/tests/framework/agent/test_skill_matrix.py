"""Tests for F7-P3 Skill Matrix & Capability Permissions."""

from __future__ import annotations

from backend.aix_core.framework.agent.models import PermissionEffect, Role
from backend.aix_core.framework.agent.skill_matrix import SkillMatrix


def test_universal_prohibitions():
    prohibitions = SkillMatrix.universal_prohibitions()
    assert "auto_accept" in prohibitions
    assert "direct_provider_route" in prohibitions
    assert "workhub_lifecycle_mutation" in prohibitions


def test_skills_and_permissions_for_planner():
    skills = SkillMatrix.skills_for_role(Role.PLANNER)
    perms = SkillMatrix.permissions_for_role(Role.PLANNER)

    assert len(skills) > 0
    assert any("propose_step_contracts" in s.skill_id for s in skills)

    # Universal prohibitions in must_not
    for s in skills:
        assert "auto_accept" in s.prohibited_effects

    # Explicit allow and deny permissions
    allow_perms = [p for p in perms if p.effect == PermissionEffect.ALLOW]
    deny_perms = [p for p in perms if p.effect == PermissionEffect.DENY]
    assert len(allow_perms) > 0
    assert len(deny_perms) > 0


def test_skill_matrix_prohibitions_and_allowed_ops():
    # Executor coder cannot self_verify
    assert SkillMatrix.is_effect_prohibited(Role.EXECUTOR_CODER, "self_verify") is True
    # Verifier cannot rewrite_executor_output
    assert SkillMatrix.is_effect_prohibited(Role.VERIFIER, "rewrite_executor_output") is True

    # Universal prohibition applies to all roles
    assert SkillMatrix.is_effect_prohibited(Role.PLANNER, "direct_provider_route") is True
    assert SkillMatrix.is_effect_prohibited(Role.EXECUTOR_CODER, "direct_provider_route") is True

    # Allowed operations
    assert SkillMatrix.is_operation_allowed(Role.PLANNER, "compile_dependencies") is True
    assert SkillMatrix.is_operation_allowed(Role.PLANNER, "execute_commands") is False
