"""Least-privilege command/tool allowlists for objective-cycle roles."""

from __future__ import annotations

import json
from pathlib import Path

from backend.aix_core.runtime.governance.experimental.agent_roles import AgentRoleId
from backend.aix_core.runtime.governance.experimental.persona_manifests import load_cycle_persona_manifests
from backend.models.command_allowlist_models import (
    CommandAllowlistProfileCurrent,
    validate_command_allowlist_profile,
)

_EXPECTED_REPO_PROFILES = {
    AgentRoleId.PLANNER: "RP-HYEVE-READONLY-CURRENT",
    AgentRoleId.EXECUTOR_CODER: "RP-HYEVE-PY-FASTAPI-CURRENT",
    AgentRoleId.VERIFIER: "RP-HYEVE-READONLY-CURRENT",
    AgentRoleId.RECOVERY_PLANNER: "RP-HYEVE-READONLY-CURRENT",
    AgentRoleId.REVIEW_PACKET_CURATOR: "RP-HYEVE-READONLY-CURRENT",
}

_MUTATION_TOOLS = {"apply_patch", "edit_file", "write_file"}
_FORBIDDEN_COMMAND_HEADS = {
    "bash",
    "cmd",
    "curl",
    "docker",
    "kubectl",
    "powershell",
    "pwsh",
    "scp",
    "sh",
    "ssh",
    "wget",
}
_FORBIDDEN_GIT_ACTIONS = {"add", "commit", "merge", "push", "rebase", "reset", "restore", "switch"}
_SHELL_MARKERS = {";", "&&", "||", "|", ">", "<", "`"}


def load_cycle_command_allowlists(
    *,
    manifests_dir: str | Path,
    allowlists_dir: str | Path,
) -> dict[AgentRoleId, CommandAllowlistProfileCurrent]:
    manifests = load_cycle_persona_manifests(manifests_dir)
    root = Path(allowlists_dir)
    by_profile_id: dict[str, CommandAllowlistProfileCurrent] = {}
    for path in sorted(root.glob("ACP-OBJECTIVE-CYCLE-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        profile = validate_command_allowlist_profile(payload)
        if profile.profile_id in by_profile_id:
            raise ValueError(f"OBJECTIVE_CYCLE_COMMAND_PROFILE_DUPLICATE:{profile.profile_id}")
        by_profile_id[profile.profile_id] = profile

    resolved: dict[AgentRoleId, CommandAllowlistProfileCurrent] = {}
    for role, manifest in manifests.items():
        if manifest.tool_policy.repo_profile != _EXPECTED_REPO_PROFILES[role]:
            raise ValueError(f"OBJECTIVE_CYCLE_REPO_PROFILE_INVALID:{role.value}")
        profile_id = manifest.tool_policy.allowed_commands_profile
        profile = by_profile_id.get(profile_id)
        if profile is None:
            raise ValueError(f"OBJECTIVE_CYCLE_COMMAND_PROFILE_MISSING:{role.value}:{profile_id}")
        _validate_profile_safety(role, profile)
        if role is not AgentRoleId.EXECUTOR_CODER and set(manifest.tool_policy.allowed_tools) & _MUTATION_TOOLS:
            raise ValueError(f"OBJECTIVE_CYCLE_MUTATION_TOOL_FORBIDDEN:{role.value}")
        resolved[role] = profile
    return resolved


def command_is_allowed(profile: CommandAllowlistProfileCurrent, argv: list[str]) -> bool:
    if not argv or any(marker in token for token in argv for marker in _SHELL_MARKERS):
        return False
    actual = [_normalized_executable(token, index) for index, token in enumerate(argv)]
    for rule in profile.rules:
        expected = [_normalized_executable(token, index) for index, token in enumerate(rule.argv)]
        if rule.mode == "exact" and actual == expected:
            return True
        if rule.mode == "prefix" and len(actual) >= len(expected) and actual[: len(expected)] == expected:
            return True
    return False


def _normalized_executable(token: str, index: int) -> str:
    normalized = str(token).strip()
    if index == 0:
        return Path(normalized).stem.lower()
    return normalized


def _validate_profile_safety(role: AgentRoleId, profile: CommandAllowlistProfileCurrent) -> None:
    for rule in profile.rules:
        head = Path(rule.argv[0]).stem.lower()
        if head in _FORBIDDEN_COMMAND_HEADS:
            raise ValueError(f"OBJECTIVE_CYCLE_FORBIDDEN_COMMAND:{role.value}:{head}")
        if head == "git" and len(rule.argv) > 1 and rule.argv[1].lower() in _FORBIDDEN_GIT_ACTIONS:
            raise ValueError(f"OBJECTIVE_CYCLE_FORBIDDEN_GIT_ACTION:{role.value}:{rule.argv[1]}")
        if any(marker in token for token in rule.argv for marker in _SHELL_MARKERS):
            raise ValueError(f"OBJECTIVE_CYCLE_SHELL_ESCAPE_IN_PROFILE:{role.value}")
        if head == "python" and rule.argv[1:3] != ["-m", "pytest"]:
            raise ValueError(f"OBJECTIVE_CYCLE_UNBOUNDED_PYTHON_COMMAND:{role.value}")
        if head == "pytest" and role not in {AgentRoleId.EXECUTOR_CODER, AgentRoleId.VERIFIER}:
            raise ValueError(f"OBJECTIVE_CYCLE_TEST_COMMAND_ROLE_FORBIDDEN:{role.value}")
