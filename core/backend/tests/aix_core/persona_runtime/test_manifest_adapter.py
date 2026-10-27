"""Real adapter, all five roles: PersonaManifestCurrent (from the actual JSON files
under backend/config/persona_manifests/) -> PersonaV1/PolicyV1/CapabilityV1 ->
compose() -> render(), using real manifest data throughout. No hand-built test data.
"""
from __future__ import annotations

from backend.aix_core.runtime.governance.experimental.agent_roles import AgentRoleId, FIVE_ROLE_DECISIONS
from backend.aix_core.persona_runtime.composer import compose
from backend.aix_core.persona_runtime.manifest_adapter import (
    MISSING_REAL_CONTENT,
    capabilities_from_manifest,
    load_real_manifests,
    persona_from_manifest,
    policy_from_manifest,
)
from backend.aix_core.persona_runtime.renderer import render

ALL_ROLES = tuple(decision.role_id for decision in FIVE_ROLE_DECISIONS)


def test_all_five_real_manifests_load_and_validate() -> None:
    manifests = load_real_manifests()
    assert set(manifests) == set(ALL_ROLES)


def test_compose_and_render_succeeds_for_all_five_roles_with_real_data() -> None:
    manifests = load_real_manifests()
    for role in ALL_ROLES:
        manifest = manifests[role]
        persona = persona_from_manifest(manifest, role)
        capabilities = capabilities_from_manifest(manifest, role)
        policy = policy_from_manifest(manifest)
        context = compose(
            persona=persona,
            capabilities=capabilities,
            policy=policy,
            overlay=None,
            objective=f"real-data compose smoke test for role={role.value}",
        )
        rendered = render(context)
        assert rendered.render().strip(), f"empty render for role={role.value}"
        # Mission must be the real AgentRoleDecision.responsibility, not empty/invented.
        assert context.mission
        # Every requested tool for these roles equals the manifest's own granted list
        # exactly, so nothing should ever be denied for this synthetic single-capability
        # setup -- if this fails, either the adapter or compose() has a real bug.
        assert context.denied_requested_tools == ()
        assert set(context.granted_tools) == set(manifest.tool_policy.allowed_tools)


def test_persona_mission_is_sourced_from_real_agent_role_decision() -> None:
    manifests = load_real_manifests()
    for role in ALL_ROLES:
        persona = persona_from_manifest(manifests[role], role)
        from backend.aix_core.runtime.governance.experimental.agent_roles import get_role_decision

        assert persona.mission == get_role_decision(role).responsibility


def test_only_executor_coder_has_real_tool_invocation_protocol_content() -> None:
    """This is the honest accounting of the gap from the last pass: four of the five
    roles have no real production prompt content to source capability knowledge/
    protocol from. This test locks that fact in so it can't silently regress into
    someone filling in invented content later without this test forcing a decision."""
    manifests = load_real_manifests()
    for role in ALL_ROLES:
        capabilities = capabilities_from_manifest(manifests[role], role)
        assert len(capabilities) == 1
        protocol = capabilities[0].tool_invocation_protocol
        if role is AgentRoleId.EXECUTOR_CODER:
            assert protocol, "executor_coder must have real extracted protocol content"
        else:
            assert protocol == (), f"role={role.value} unexpectedly has protocol content -- was it invented?"
            assert role in MISSING_REAL_CONTENT, f"role={role.value} missing an honest MISSING_REAL_CONTENT entry"


def test_constraints_are_real_and_differ_by_role() -> None:
    manifests = load_real_manifests()
    per_role_constraints = {
        role: policy_from_manifest(manifests[role]).constraints for role in ALL_ROLES
    }
    # executor_coder's manifest has more constraints than verifier's real manifest --
    # this is a fact about the two real JSON files, not an assumption.
    assert len(per_role_constraints[AgentRoleId.EXECUTOR_CODER]) > 0
    assert len(per_role_constraints[AgentRoleId.VERIFIER]) > 0
    assert per_role_constraints[AgentRoleId.EXECUTOR_CODER] != per_role_constraints[AgentRoleId.VERIFIER]
