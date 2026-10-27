from __future__ import annotations

from pathlib import Path

import pytest

from backend.aix_core.runtime.governance.experimental.agent_roles import AgentRoleId
from backend.aix_core.runtime.governance.experimental.persona_manifests import (
    load_cycle_persona_manifests,
    validate_cycle_persona_manifests,
)

BACKEND_ROOT = Path(__file__).resolve().parents[3]
MANIFESTS_DIR = BACKEND_ROOT / "config" / "persona_manifests"


def test_all_five_versioned_cycle_manifests_load() -> None:
    manifests = load_cycle_persona_manifests(MANIFESTS_DIR)

    assert set(manifests) == set(AgentRoleId)
    assert all(manifest.persona_version == "v1" for manifest in manifests.values())


def test_manifests_bind_role_autonomy_tools_and_evidence() -> None:
    manifests = load_cycle_persona_manifests(MANIFESTS_DIR)

    assert manifests[AgentRoleId.PLANNER].autonomy_cap == "L1"
    assert manifests[AgentRoleId.EXECUTOR_CODER].autonomy_cap == "L2"
    assert "apply_patch" in manifests[AgentRoleId.EXECUTOR_CODER].tool_policy.allowed_tools
    assert "apply_patch" not in manifests[AgentRoleId.VERIFIER].tool_policy.allowed_tools
    assert all(item.evidence_policy.require_acceptance_mapping for item in manifests.values())


def test_manifests_do_not_contain_direct_provider_tools() -> None:
    manifests = load_cycle_persona_manifests(MANIFESTS_DIR)

    for manifest in manifests.values():
        assert not any("provider" in tool or "model_call" in tool for tool in manifest.tool_policy.allowed_tools)
        assert manifest.metadata["provider_transport_authority"] == "hyeve_integration_only"


def test_validator_rejects_missing_role_manifest() -> None:
    manifests = list(load_cycle_persona_manifests(MANIFESTS_DIR).values())

    with pytest.raises(ValueError, match="OBJECTIVE_CYCLE_PERSONAS_MISSING"):
        validate_cycle_persona_manifests(manifests[:-1])


def test_validator_rejects_autonomy_escalation() -> None:
    manifests = list(load_cycle_persona_manifests(MANIFESTS_DIR).values())
    planner_index = next(
        index for index, manifest in enumerate(manifests)
        if manifest.metadata["objective_cycle_role"] == "planner"
    )
    manifests[planner_index] = manifests[planner_index].model_copy(update={"autonomy_cap": "L4"})

    with pytest.raises(ValueError, match="OBJECTIVE_CYCLE_AUTONOMY_EXCEEDS_ROLE_CAP"):
        validate_cycle_persona_manifests(manifests)


def test_validator_rejects_direct_provider_tool() -> None:
    manifests = list(load_cycle_persona_manifests(MANIFESTS_DIR).values())
    manifest = manifests[0]
    unsafe_tool_policy = manifest.tool_policy.model_copy(
        update={"allowed_tools": [*manifest.tool_policy.allowed_tools, "provider.openai.call"]}
    )
    manifests[0] = manifest.model_copy(update={"tool_policy": unsafe_tool_policy})

    with pytest.raises(ValueError, match="OBJECTIVE_CYCLE_DIRECT_PROVIDER_TOOL_FORBIDDEN"):
        validate_cycle_persona_manifests(manifests)


def test_validator_rejects_missing_authority_constraint() -> None:
    manifests = list(load_cycle_persona_manifests(MANIFESTS_DIR).values())
    manifest = manifests[0]
    manifests[0] = manifest.model_copy(
        update={"constraints": [item for item in manifest.constraints if item != "no_auto_accept"]}
    )

    with pytest.raises(ValueError, match="OBJECTIVE_CYCLE_CONSTRAINTS_MISSING"):
        validate_cycle_persona_manifests(manifests)
