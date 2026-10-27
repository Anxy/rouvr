"""Tests for F7-P3 Persona Registry."""

from __future__ import annotations

from backend.aix_core.framework.agent.models import Persona, Role
from backend.aix_core.framework.agent.persona_registry import (
    PersonaRegistry,
    get_persona_registry,
)


def test_canonical_personas_lookup():
    reg = get_persona_registry()
    for role in (
        Role.PLANNER,
        Role.EXECUTOR_CODER,
        Role.VERIFIER,
        Role.RECOVERY_PLANNER,
        Role.REVIEW_PACKET_CURATOR,
    ):
        p = reg.get_persona(role)
        assert p is not None
        assert p.role == role
        assert len(p.system_prompt) > 0


def test_persona_system_prompt_prohibitions():
    reg = get_persona_registry()
    planner = reg.get_persona(Role.PLANNER)
    assert "must NOT execute commands" in planner.system_prompt

    verifier = reg.get_persona(Role.VERIFIER)
    assert "Fail-closed is absolute" in verifier.system_prompt


def test_custom_persona_registration():
    reg = PersonaRegistry()
    custom = Persona(
        persona_id="persona_custom_planner",
        name="Custom Planner",
        role=Role.PLANNER,
        system_prompt="Custom planner instructions",
        autonomy_cap="L0",
    )
    reg.register_persona(custom)
    fetched = reg.get_persona(Role.PLANNER)
    assert fetched == custom
    assert fetched.autonomy_cap == "L0"
