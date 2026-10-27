"""P28: Hyeve Platform Convergence.

Defines the integration boundary between AIX and Hyeve platform services.
AIX does not duplicate what Hyeve provides — it consumes via contracts.

Hyeve owns: Identity, Person/org context, CodeIntel, Data, Comm,
            Test orchestration, Business rules, Audit, API Gateway.
AIX owns:   Capability execution, Execution context, Execution policy,
            Resource policy, Authorization decisions, Evidence, Learning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


HyeveService = Literal[
    "auth", "person", "codeintel", "data", "comm",
    "test_orchestrator", "business_rules", "audit", "api_gateway",
]


@dataclass(frozen=True)
class HyeveServiceBindingV1:
    """Declares how AIX consumes a Hyeve platform service."""
    service: HyeveService
    endpoint: str = ""
    protocol: str = "grpc"
    owns: tuple[str, ...] = ()
    consumes: tuple[str, ...] = ()
    health_check: str = ""
    fallback_behavior: str = "fail_closed"


@dataclass(frozen=True)
class HyeveIdentityV1:
    """Identity resolved from Hyeve Auth."""
    principal_id: str
    tenant_id: str
    principal_type: Literal["human", "agent", "service", "system"]
    roles: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    memberships: tuple[str, ...] = ()  # project memberships


@dataclass(frozen=True)
class ConvergenceBoundary:
    """Declares what AIX owns vs what Hyeve owns."""
    hyeve_owns: tuple[str, ...]
    aix_owns: tuple[str, ...]


# The authoritative boundary declaration
CONVERGENCE_BOUNDARY = ConvergenceBoundary(
    hyeve_owns=(
        "identity", "tenant_membership", "groups", "roles",
        "service_identity", "agent_identity", "person_context",
        "org_context", "code_intelligence", "governed_data",
        "communications", "test_orchestration", "business_rules",
        "enterprise_audit", "api_ingress",
    ),
    aix_owns=(
        "capability_definitions", "execution_context", "execution_policy",
        "resource_policy", "authorization_decisions", "approval_workflow",
        "execution_evidence", "action_observations", "verification",
        "repair_protocol", "learning", "self_improvement",
        "capability_discovery", "scheduling", "budget_governance",
        "control_room", "recovery",
    ),
)


class HyeveIntegrationRegistry:
    """Registry of Hyeve service bindings for AIX consumption."""

    def __init__(self) -> None:
        self._bindings: dict[HyeveService, HyeveServiceBindingV1] = {}

    def register(self, binding: HyeveServiceBindingV1) -> None:
        self._bindings[binding.service] = binding

    def get(self, service: HyeveService) -> HyeveServiceBindingV1 | None:
        return self._bindings.get(service)

    def list_services(self) -> list[HyeveServiceBindingV1]:
        return list(self._bindings.values())

    def is_available(self, service: HyeveService) -> bool:
        binding = self._bindings.get(service)
        return binding is not None and bool(binding.endpoint)

    def resolve_identity(self, *, principal_id: str, tenant_id: str) -> HyeveIdentityV1 | None:
        """Resolve identity via Hyeve Auth binding. Returns None if not available."""
        if not self.is_available("auth"):
            return None
        return HyeveIdentityV1(
            principal_id=principal_id,
            tenant_id=tenant_id,
            principal_type="agent",
        )

    @staticmethod
    def boundary() -> ConvergenceBoundary:
        return CONVERGENCE_BOUNDARY


__all__ = [
    "HyeveService", "HyeveServiceBindingV1", "HyeveIdentityV1",
    "ConvergenceBoundary", "CONVERGENCE_BOUNDARY", "HyeveIntegrationRegistry",
]
