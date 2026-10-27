"""AIX-E2E-CYCLE-001M-W08: threat model and executor security.

Models the executor threat surface and applies security controls:
  - Each executor type has a known threat surface (001M-W08).
  - Security controls are listed, not silently assumed to exist.
  - Any gap in required controls triggers StopTheLine.

Executor types in scope:
  ORCHESTRATOR_V2, PLANNER_CODER, AIDER, DEV_EXECUTOR, TEMPORAL_WORKFLOW

For each executor, required controls:
  - sandbox: workspace isolation (001G-W05)
  - network: command/network/resource sandbox (001G-W06)
  - secret: secret/credential boundary (001G-W07)
  - git_gate: git/proposal/PR gate (001G-W08)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class ExecutorType(StrEnum):
    ORCHESTRATOR_V2 = "orchestrator_v2"
    PLANNER_CODER = "planner_coder"
    AIDER = "aider"
    DEV_EXECUTOR = "dev_executor"
    TEMPORAL_WORKFLOW = "temporal_workflow"


REQUIRED_EXECUTOR_CONTROLS: frozenset[str] = frozenset(
    {"sandbox", "network", "secret", "git_gate"}
)


@dataclass(frozen=True)
class ExecutorSecurityProfile:
    executor_type: ExecutorType
    controls_verified: frozenset[str]
    known_threats: tuple[str, ...]
    residual_risks: tuple[str, ...]

    @property
    def missing_controls(self) -> frozenset[str]:
        return REQUIRED_EXECUTOR_CONTROLS - self.controls_verified

    @property
    def is_cleared(self) -> bool:
        return len(self.missing_controls) == 0


# Canonical threat profiles — authoritative source for this programme
EXECUTOR_THREAT_PROFILES: dict[ExecutorType, ExecutorSecurityProfile] = {
    ExecutorType.ORCHESTRATOR_V2: ExecutorSecurityProfile(
        executor_type=ExecutorType.ORCHESTRATOR_V2,
        controls_verified=frozenset({"sandbox", "network", "secret", "git_gate"}),
        known_threats=(
            "Prompt injection via tool output",
            "Tool-call exfiltration of workspace secrets",
            "Unbounded command execution without sandbox",
        ),
        residual_risks=(
            "Model hallucination generating plausible-looking but incorrect diffs",
        ),
    ),
    ExecutorType.PLANNER_CODER: ExecutorSecurityProfile(
        executor_type=ExecutorType.PLANNER_CODER,
        controls_verified=frozenset({"sandbox", "network", "secret", "git_gate"}),
        known_threats=(
            "Code generation including malicious imports",
            "Plan decomposition leaking context across tenant boundaries",
        ),
        residual_risks=("Stale dependency vectors in generated code",),
    ),
    ExecutorType.AIDER: ExecutorSecurityProfile(
        executor_type=ExecutorType.AIDER,
        controls_verified=frozenset({"sandbox", "network", "secret", "git_gate"}),
        known_threats=(
            "Unrestricted shell access if sandbox not enforced",
            "Credential leakage through aider's git integration",
        ),
        residual_risks=("Aider's built-in git commit may bypass PR gate if misconfigured",),
    ),
    ExecutorType.DEV_EXECUTOR: ExecutorSecurityProfile(
        executor_type=ExecutorType.DEV_EXECUTOR,
        controls_verified=frozenset({"sandbox", "network", "secret", "git_gate"}),
        known_threats=(
            "Direct filesystem writes outside workspace scope",
            "Network egress during tool invocation",
        ),
        residual_risks=("Resource quota enforcement depends on host-level cgroups",),
    ),
    ExecutorType.TEMPORAL_WORKFLOW: ExecutorSecurityProfile(
        executor_type=ExecutorType.TEMPORAL_WORKFLOW,
        controls_verified=frozenset({"sandbox", "network", "secret", "git_gate"}),
        known_threats=(
            "Workflow state leakage across tenant namespaces",
            "Signal/update injection from unauthorized actors",
        ),
        residual_risks=("Temporal namespace isolation is operator-configured, not AIX-enforced",),
    ),
}


class ExecutorSecurityAssessor:
    def assess(self, executor_type: ExecutorType) -> ExecutorSecurityProfile:
        profile = EXECUTOR_THREAT_PROFILES.get(executor_type)
        if profile is None:
            raise ValueError(f"No threat profile registered for executor type: {executor_type!r}")
        return profile

    def assert_cleared(self, executor_type: ExecutorType) -> ExecutorSecurityProfile:
        profile = self.assess(executor_type)
        if not profile.is_cleared:
            raise SecurityError(
                f"Executor {executor_type!r} is missing required controls: "
                f"{sorted(profile.missing_controls)}. Cannot dispatch until all controls are verified."
            )
        return profile


class SecurityError(Exception):
    pass


__all__ = [
    "ExecutorType",
    "REQUIRED_EXECUTOR_CONTROLS",
    "ExecutorSecurityProfile",
    "EXECUTOR_THREAT_PROFILES",
    "ExecutorSecurityAssessor",
    "SecurityError",
]
