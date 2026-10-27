from __future__ import annotations

from pathlib import Path

import pytest

from backend.aix_core.runtime.governance.experimental.agent_roles import AgentRoleId
from backend.aix_core.runtime.governance.experimental.command_allowlists import (
    _validate_profile_safety,
    command_is_allowed,
    load_cycle_command_allowlists,
)
from backend.models.command_allowlist_models import validate_command_allowlist_profile

BACKEND_ROOT = Path(__file__).resolve().parents[4]
MANIFESTS_DIR = BACKEND_ROOT / "config" / "persona_manifests"
ALLOWLISTS_DIR = BACKEND_ROOT / "config" / "command_allowlists"


def _profiles():
    return load_cycle_command_allowlists(
        manifests_dir=MANIFESTS_DIR,
        allowlists_dir=ALLOWLISTS_DIR,
    )


def test_all_five_persona_command_references_resolve() -> None:
    profiles = _profiles()

    assert set(profiles) == set(AgentRoleId)
    assert len({profile.profile_id for profile in profiles.values()}) == 5


def test_read_roles_have_read_only_repository_profiles() -> None:
    from backend.aix_core.runtime.governance.experimental.persona_manifests import load_cycle_persona_manifests

    manifests = load_cycle_persona_manifests(MANIFESTS_DIR)
    for role in {
        AgentRoleId.PLANNER,
        AgentRoleId.VERIFIER,
        AgentRoleId.RECOVERY_PLANNER,
        AgentRoleId.REVIEW_PACKET_CURATOR,
    }:
        assert manifests[role].tool_policy.repo_profile == "RP-HYEVE-READONLY-CURRENT"
        assert "apply_patch" not in manifests[role].tool_policy.allowed_tools


def test_only_executor_has_mutation_tool() -> None:
    from backend.aix_core.runtime.governance.experimental.persona_manifests import load_cycle_persona_manifests

    manifests = load_cycle_persona_manifests(MANIFESTS_DIR)

    assert "apply_patch" in manifests[AgentRoleId.EXECUTOR_CODER].tool_policy.allowed_tools
    assert all(
        "apply_patch" not in manifest.tool_policy.allowed_tools
        for role, manifest in manifests.items()
        if role is not AgentRoleId.EXECUTOR_CODER
    )


def test_verifier_can_run_only_bounded_test_commands() -> None:
    verifier = _profiles()[AgentRoleId.VERIFIER]

    assert command_is_allowed(verifier, ["python", "-m", "pytest", "tests/test_one.py", "-q"])
    assert command_is_allowed(verifier, ["pytest", "tests/test_one.py", "-q"])
    assert not command_is_allowed(verifier, ["python", "-c", "print('escape')"])


@pytest.mark.parametrize(
    "command",
    [
        ["git", "push"],
        ["git", "commit", "-m", "unsafe"],
        ["curl", "https://provider.invalid"],
        ["powershell", "-Command", "Get-ChildItem"],
        ["docker", "run", "image"],
        ["rg", "needle", "&&", "git", "push"],
    ],
)
def test_all_profiles_deny_forbidden_commands(command: list[str]) -> None:
    for profile in _profiles().values():
        assert not command_is_allowed(profile, command)


def test_exact_git_status_rule_does_not_allow_extra_arguments() -> None:
    planner = _profiles()[AgentRoleId.PLANNER]

    assert command_is_allowed(planner, ["git", "status", "--porcelain"])
    assert not command_is_allowed(planner, ["git", "status", "--porcelain", "--ignored"])


def test_profile_validator_rejects_forbidden_git_write_rule() -> None:
    unsafe = validate_command_allowlist_profile(
        {
            "schema_version": "command_allowlist.current",
            "profile_id": "ACP-OBJECTIVE-CYCLE-PLANNER-V1",
            "rules": [{"mode": "prefix", "argv": ["git", "push"]}],
        }
    )
    with pytest.raises(ValueError, match="OBJECTIVE_CYCLE_FORBIDDEN_GIT_ACTION"):
        _validate_profile_safety(AgentRoleId.PLANNER, unsafe)
