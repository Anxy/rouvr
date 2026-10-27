"""Tests for F7-P1 Agent / Role / Persona / Skill / Permission Models."""

from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from backend.aix_core.framework.agent.models import (
    Agent,
    Permission,
    PermissionEffect,
    Persona,
    Role,
    Skill,
)


def test_role_enumeration():
    assert Role.PLANNER.value == "planner"
    assert Role.EXECUTOR_CODER.value == "executor_coder"
    assert Role.VERIFIER.value == "verifier"
    assert Role.RECOVERY_PLANNER.value == "recovery_planner"
    assert Role.REVIEW_PACKET_CURATOR.value == "review_packet_curator"


def test_permission_model():
    perm = Permission(
        permission_id="perm_read_ctx",
        scope="filesystem.read",
        effect=PermissionEffect.ALLOW,
        description="Allow reading context files",
    )
    assert perm.permission_id == "perm_read_ctx"
    assert perm.effect == PermissionEffect.ALLOW
    d = perm.to_dict()
    assert d["scope"] == "filesystem.read"
    assert d["effect"] == "ALLOW"

    with pytest.raises(FrozenInstanceError):
        perm.scope = "filesystem.write"  # type: ignore[misc]


def test_skill_model():
    skill = Skill(
        skill_id="skill_code_gen",
        name="Source Code Generation",
        description="Generates diffs conforming to step contracts",
        required_capabilities=("filesystem", "repository"),
        required_permissions=("perm_read_ctx", "perm_write_patch"),
        prohibited_effects=("auto_close", "direct_provider_route"),
    )
    assert skill.skill_id == "skill_code_gen"
    assert "filesystem" in skill.required_capabilities
    assert "auto_close" in skill.prohibited_effects

    d = skill.to_dict()
    assert d["name"] == "Source Code Generation"
    assert len(d["prohibited_effects"]) == 2


def test_persona_and_agent_model():
    persona = Persona(
        persona_id="persona_planner_v1",
        name="Objective Planner",
        role=Role.PLANNER,
        system_prompt="You are an objective planner...",
        autonomy_cap="L1",
    )
    assert persona.role == Role.PLANNER
    assert persona.autonomy_cap == "L1"

    perm = Permission(permission_id="perm_read", scope="context.read")
    skill = Skill(skill_id="skill_plan", name="Planning", description="Decomposes objectives")

    agent = Agent(
        agent_id="agent_planner_01",
        role=Role.PLANNER,
        persona=persona,
        skills=(skill,),
        permissions=(perm,),
        autonomy_cap="L1",
        can_approve=False,
        can_escalate=True,
    )

    assert agent.agent_id == "agent_planner_01"
    assert agent.role == Role.PLANNER
    assert agent.persona.persona_id == "persona_planner_v1"
    assert len(agent.skills) == 1
    assert len(agent.permissions) == 1

    d = agent.to_dict()
    assert d["role"] == "planner"
    assert d["persona"]["name"] == "Objective Planner"
    assert d["can_approve"] is False
    assert d["can_escalate"] is True
