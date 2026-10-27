"""P14 Multi-Agent Runtime — Agent Registry (temporary canonical).

F7 remediation (audit R4): this module remains the temporary canonical registry for
`handoff.py` consumers. It is NOT a forwarding shim — `framework.agent` is a parallel
additive model surface (ADDITIVE_NOT_WIRED) until an explicit F10 rewire.

Do not claim RETIRE/shim here. Do not maintain a second live registry for OC dispatch
(OC does not currently consult this registry on the execute path).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from backend.aix_core.runtime.foundation.execution_context import PrincipalV1

AgentRole = Literal[
    "planner",
    "coder",
    "tester",
    "reviewer",
    "researcher",
    "security_reviewer",
    "release_agent",
    "operations_agent",
]

AGENT_ROLES: frozenset[str] = frozenset(AgentRole.__args__)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class AgentBudget:
    max_tokens: int = 500_000
    max_cost_usd: float = 10.0
    max_steps: int = 20
    max_retries: int = 3
    max_wall_clock_seconds: int = 3600


@dataclass(frozen=True)
class AgentCapabilityGrant:
    """Declares which capabilities an agent type may use."""

    allowed_capabilities: frozenset[str] = frozenset()
    denied_capabilities: frozenset[str] = frozenset()
    allowed_operations: frozenset[str] = frozenset()
    denied_operations: frozenset[str] = frozenset()
    max_mutations_per_step: int = 10
    requires_approval_for: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AgentSpec:
    """Defines an agent type with its capabilities, constraints, and authority."""

    role: AgentRole
    description: str
    capability_grant: AgentCapabilityGrant
    budget: AgentBudget = field(default_factory=AgentBudget)
    model_access: tuple[str, ...] = ("default",)
    memory_scope: Literal["step", "objective", "project", "organization"] = "objective"
    can_delegate_to: tuple[AgentRole, ...] = ()
    can_receive_from: tuple[AgentRole, ...] = ()
    can_approve: bool = False
    can_escalate: bool = True

    def to_principal(self, agent_id: str) -> PrincipalV1:
        return PrincipalV1(type="agent", id=agent_id, role=self.role)


# ── Canonical Agent Specs ───────────────────────────────────────────────────

PLANNER = AgentSpec(
    role="planner",
    description="Decomposes objectives into executable plans. Cannot mutate code.",
    capability_grant=AgentCapabilityGrant(
        allowed_capabilities=frozenset({"filesystem", "repository", "search", "tests"}),
        denied_capabilities=frozenset({"process", "git", "network"}),
        allowed_operations=frozenset({
            "filesystem.read",
            "filesystem.inspect",
            "filesystem.search",
            "filesystem.exists",
            "filesystem.stat",
            "repository.resolve_symbol",
            "search.code",
        }),
        denied_operations=frozenset({
            "filesystem.patch",
            "filesystem.create",
            "filesystem.mkdir",
            "filesystem.rename",
            "filesystem.copy",
            "filesystem.delete",
        }),
        max_mutations_per_step=0,
    ),
    memory_scope="objective",
    can_delegate_to=("coder", "tester", "researcher"),
    can_receive_from=(),
    model_access=("default", "reasoning"),
)

CODER = AgentSpec(
    role="coder",
    description="Implements code changes according to step contracts.",
    capability_grant=AgentCapabilityGrant(
        allowed_capabilities=frozenset({
            "filesystem",
            "repository",
            "search",
            "tests",
            "process",
            "git",
        }),
        denied_capabilities=frozenset({"network"}),
        allowed_operations=frozenset({
            "filesystem.read",
            "filesystem.inspect",
            "filesystem.search",
            "filesystem.exists",
            "filesystem.stat",
            "filesystem.patch",
            "filesystem.create",
            "filesystem.mkdir",
            "filesystem.rename",
            "filesystem.copy",
            "repository.resolve_symbol",
            "search.code",
            "tests.run",
            "tests.lint",
            "process.run",
            "git.status",
            "git.diff",
        }),
        denied_operations=frozenset({
            "filesystem.delete",
            "process.run_mutating",
            "git.commit",
            "git.checkout",
            "git.create_branch",
        }),
        max_mutations_per_step=10,
    ),
    memory_scope="objective",
    can_delegate_to=("tester",),
    can_receive_from=("planner",),
)

TESTER = AgentSpec(
    role="tester",
    description="Runs tests and verification. Cannot mutate source code.",
    capability_grant=AgentCapabilityGrant(
        allowed_capabilities=frozenset({
            "filesystem",
            "repository",
            "search",
            "tests",
            "process",
        }),
        denied_capabilities=frozenset({"git", "network"}),
        allowed_operations=frozenset({
            "filesystem.read",
            "filesystem.inspect",
            "filesystem.search",
            "filesystem.exists",
            "filesystem.stat",
            "repository.resolve_symbol",
            "search.code",
            "tests.run",
            "tests.lint",
            "tests.coverage",
            "process.run",
        }),
        denied_operations=frozenset({
            "filesystem.patch",
            "filesystem.create",
            "filesystem.mkdir",
            "filesystem.rename",
            "filesystem.copy",
            "filesystem.delete",
        }),
        max_mutations_per_step=0,
    ),
    memory_scope="step",
    can_delegate_to=(),
    can_receive_from=("planner", "coder"),
)

REVIEWER = AgentSpec(
    role="reviewer",
    description="Reviews code changes. Read-only access.",
    capability_grant=AgentCapabilityGrant(
        allowed_capabilities=frozenset({
            "filesystem",
            "repository",
            "search",
            "git",
        }),
        denied_capabilities=frozenset({"process", "network", "tests"}),
        allowed_operations=frozenset({
            "filesystem.read",
            "filesystem.inspect",
            "filesystem.search",
            "filesystem.exists",
            "filesystem.stat",
            "repository.resolve_symbol",
            "search.code",
            "git.status",
            "git.diff",
            "git.log",
        }),
        denied_operations=frozenset({
            "filesystem.patch",
            "filesystem.create",
            "filesystem.mkdir",
            "filesystem.rename",
            "filesystem.copy",
            "filesystem.delete",
            "git.stage",
            "git.commit",
            "git.checkout",
            "git.create_branch",
        }),
        max_mutations_per_step=0,
    ),
    memory_scope="objective",
    can_delegate_to=(),
    can_receive_from=("planner",),
    can_approve=True,
)

RESEARCHER = AgentSpec(
    role="researcher",
    description="Explores codebase and external sources for information.",
    capability_grant=AgentCapabilityGrant(
        allowed_capabilities=frozenset({
            "filesystem",
            "repository",
            "search",
            "network",
        }),
        denied_capabilities=frozenset({"process", "git", "tests"}),
        allowed_operations=frozenset({
            "filesystem.read",
            "filesystem.inspect",
            "filesystem.search",
            "filesystem.exists",
            "filesystem.stat",
            "repository.resolve_symbol",
            "search.code",
            "network.http_get",
        }),
        denied_operations=frozenset({
            "filesystem.patch",
            "filesystem.create",
            "filesystem.mkdir",
            "filesystem.rename",
            "filesystem.copy",
            "filesystem.delete",
            "network.http_post",
        }),
        max_mutations_per_step=0,
    ),
    memory_scope="project",
    can_delegate_to=(),
    can_receive_from=("planner",),
)

SECURITY_REVIEWER = AgentSpec(
    role="security_reviewer",
    description="Audits code for security vulnerabilities. Read-only.",
    capability_grant=AgentCapabilityGrant(
        allowed_capabilities=frozenset({
            "filesystem",
            "repository",
            "search",
            "git",
        }),
        denied_capabilities=frozenset({"process", "network", "tests"}),
        allowed_operations=frozenset({
            "filesystem.read",
            "filesystem.inspect",
            "filesystem.search",
            "filesystem.exists",
            "filesystem.stat",
            "repository.resolve_symbol",
            "search.code",
            "git.status",
            "git.diff",
            "git.log",
        }),
        denied_operations=frozenset({
            "filesystem.patch",
            "filesystem.create",
            "filesystem.mkdir",
            "filesystem.rename",
            "filesystem.copy",
            "filesystem.delete",
        }),
        max_mutations_per_step=0,
    ),
    memory_scope="project",
    can_delegate_to=(),
    can_receive_from=("planner",),
    can_approve=True,
)

RELEASE_AGENT = AgentSpec(
    role="release_agent",
    description="Manages git operations for release. Can commit and branch.",
    capability_grant=AgentCapabilityGrant(
        allowed_capabilities=frozenset({
            "filesystem",
            "repository",
            "search",
            "git",
            "tests",
            "process",
        }),
        denied_capabilities=frozenset({"network"}),
        allowed_operations=frozenset({
            "filesystem.read",
            "filesystem.inspect",
            "filesystem.search",
            "filesystem.exists",
            "filesystem.stat",
            "repository.resolve_symbol",
            "search.code",
            "git.status",
            "git.diff",
            "git.log",
            "git.branch",
            "git.stage",
            "git.commit",
            "git.checkout",
            "git.create_branch",
            "tests.run",
            "tests.lint",
            "process.run",
        }),
        denied_operations=frozenset({
            "filesystem.patch",
            "filesystem.create",
            "filesystem.delete",
        }),
        max_mutations_per_step=5,
        requires_approval_for=frozenset({"git.commit"}),
    ),
    memory_scope="project",
    can_delegate_to=("tester",),
    can_receive_from=("planner",),
)

OPERATIONS_AGENT = AgentSpec(
    role="operations_agent",
    description="Monitors and manages operational concerns.",
    capability_grant=AgentCapabilityGrant(
        allowed_capabilities=frozenset({
            "filesystem",
            "repository",
            "search",
            "process",
            "git",
        }),
        denied_capabilities=frozenset({"network"}),
        allowed_operations=frozenset({
            "filesystem.read",
            "filesystem.inspect",
            "filesystem.search",
            "filesystem.exists",
            "filesystem.stat",
            "repository.resolve_symbol",
            "search.code",
            "process.run",
            "git.status",
            "git.diff",
            "git.log",
        }),
        denied_operations=frozenset({
            "filesystem.patch",
            "filesystem.create",
            "filesystem.delete",
            "process.run_mutating",
        }),
        max_mutations_per_step=0,
    ),
    memory_scope="organization",
    can_delegate_to=(),
    can_receive_from=("planner",),
    can_escalate=True,
)

AGENT_SPECS: dict[AgentRole, AgentSpec] = {
    "planner": PLANNER,
    "coder": CODER,
    "tester": TESTER,
    "reviewer": REVIEWER,
    "researcher": RESEARCHER,
    "security_reviewer": SECURITY_REVIEWER,
    "release_agent": RELEASE_AGENT,
    "operations_agent": OPERATIONS_AGENT,
}


def get_agent_spec(role: str) -> AgentSpec | None:
    return AGENT_SPECS.get(role)  # type: ignore[arg-type]


def is_operation_allowed(role: str, capability: str, operation: str) -> bool:
    """Check if an agent role is allowed to perform a capability.operation."""
    spec = get_agent_spec(role)
    if spec is None:
        return False
    grant = spec.capability_grant
    fqn = f"{capability}.{operation}"
    if fqn in grant.denied_operations:
        return False
    if capability in grant.denied_capabilities:
        return False
    if grant.allowed_operations and fqn not in grant.allowed_operations:
        return False
    if grant.allowed_capabilities and capability not in grant.allowed_capabilities:
        return False
    return True


def can_delegate(from_role: str, to_role: str) -> bool:
    """Check if one agent role can delegate work to another."""
    spec = get_agent_spec(from_role)
    if spec is None:
        return False
    return to_role in spec.can_delegate_to


def agent_catalog() -> list[dict[str, Any]]:
    """Return agent catalog for the orchestrator."""
    return [
        {
            "role": spec.role,
            "description": spec.description,
            "allowed_capabilities": sorted(spec.capability_grant.allowed_capabilities),
            "denied_capabilities": sorted(spec.capability_grant.denied_capabilities),
            "max_mutations_per_step": spec.capability_grant.max_mutations_per_step,
            "can_delegate_to": list(spec.can_delegate_to),
            "can_approve": spec.can_approve,
            "memory_scope": spec.memory_scope,
        }
        for spec in AGENT_SPECS.values()
    ]


__all__ = [
    "AgentRole",
    "AGENT_ROLES",
    "AgentBudget",
    "AgentCapabilityGrant",
    "AgentSpec",
    "AGENT_SPECS",
    "get_agent_spec",
    "is_operation_allowed",
    "can_delegate",
    "agent_catalog",
    "PLANNER",
    "CODER",
    "TESTER",
    "REVIEWER",
    "RESEARCHER",
    "SECURITY_REVIEWER",
    "RELEASE_AGENT",
    "OPERATIONS_AGENT",
]
