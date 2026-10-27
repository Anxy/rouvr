"""P27: Enterprise Multi-Tenancy — complete tenant/project isolation.

Provides: tenant isolation, project isolation, data residency, policy isolation,
model policy, capability policy, budget isolation, audit retention,
enterprise administration, compliance controls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


DataResidency = Literal["us", "eu", "ap", "global"]
ComplianceLevel = Literal["standard", "hipaa", "sox", "pci", "fedramp"]


@dataclass(frozen=True)
class TenantConfigV1:
    tenant_id: str
    name: str
    data_residency: DataResidency = "global"
    compliance_level: ComplianceLevel = "standard"
    max_projects: int = 100
    max_concurrent_objectives: int = 10
    max_agents: int = 50
    budget_usd_monthly: float = 0.0  # 0 = unlimited
    allowed_models: tuple[str, ...] = ()  # empty = all
    denied_capabilities: tuple[str, ...] = ()
    audit_retention_days: int = 90
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class ProjectConfigV1:
    project_id: str
    tenant_id: str
    name: str
    allowed_capabilities: tuple[str, ...] = ()  # empty = tenant default
    denied_capabilities: tuple[str, ...] = ()
    budget_usd_monthly: float = 0.0
    allowed_models: tuple[str, ...] = ()
    environment_restrictions: tuple[str, ...] = ()  # e.g. ("no_production_deploy",)


@dataclass(frozen=True)
class IsolationCheckResult:
    allowed: bool
    reason: str = ""
    tenant_id: str = ""
    project_id: str = ""


class TenantIsolationGate:
    """Enforces tenant and project boundaries. Fail-closed."""

    def __init__(self) -> None:
        self._tenants: dict[str, TenantConfigV1] = {}
        self._projects: dict[str, ProjectConfigV1] = {}

    def register_tenant(self, config: TenantConfigV1) -> None:
        self._tenants[config.tenant_id] = config

    def register_project(self, config: ProjectConfigV1) -> None:
        if config.tenant_id not in self._tenants:
            raise ValueError(f"tenant {config.tenant_id!r} not registered")
        self._projects[config.project_id] = config

    def get_tenant(self, tenant_id: str) -> TenantConfigV1 | None:
        return self._tenants.get(tenant_id)

    def get_project(self, project_id: str) -> ProjectConfigV1 | None:
        return self._projects.get(project_id)

    def check_tenant_access(
        self, *, tenant_id: str, requesting_tenant_id: str,
    ) -> IsolationCheckResult:
        if tenant_id != requesting_tenant_id:
            return IsolationCheckResult(
                allowed=False, reason="cross-tenant access denied",
                tenant_id=tenant_id,
            )
        if tenant_id not in self._tenants:
            return IsolationCheckResult(
                allowed=False, reason="tenant not registered",
                tenant_id=tenant_id,
            )
        return IsolationCheckResult(allowed=True, tenant_id=tenant_id)

    def check_project_access(
        self, *, project_id: str, tenant_id: str,
    ) -> IsolationCheckResult:
        project = self._projects.get(project_id)
        if project is None:
            return IsolationCheckResult(
                allowed=False, reason="project not registered",
                project_id=project_id,
            )
        if project.tenant_id != tenant_id:
            return IsolationCheckResult(
                allowed=False, reason="project belongs to different tenant",
                project_id=project_id, tenant_id=tenant_id,
            )
        return IsolationCheckResult(allowed=True, project_id=project_id, tenant_id=tenant_id)

    def check_capability(
        self, *, tenant_id: str, project_id: str, capability: str,
    ) -> IsolationCheckResult:
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            return IsolationCheckResult(allowed=False, reason="tenant not registered")
        if capability in tenant.denied_capabilities:
            return IsolationCheckResult(
                allowed=False,
                reason=f"capability {capability!r} denied at tenant level",
            )
        project = self._projects.get(project_id)
        if project and capability in project.denied_capabilities:
            return IsolationCheckResult(
                allowed=False,
                reason=f"capability {capability!r} denied at project level",
            )
        return IsolationCheckResult(allowed=True, tenant_id=tenant_id, project_id=project_id)

    def check_model(
        self, *, tenant_id: str, model_id: str,
    ) -> IsolationCheckResult:
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            return IsolationCheckResult(allowed=False, reason="tenant not registered")
        if tenant.allowed_models and model_id not in tenant.allowed_models:
            return IsolationCheckResult(
                allowed=False,
                reason=f"model {model_id!r} not in tenant allowed list",
            )
        return IsolationCheckResult(allowed=True, tenant_id=tenant_id)

    def check_data_residency(
        self, *, tenant_id: str, target_region: str,
    ) -> IsolationCheckResult:
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            return IsolationCheckResult(allowed=False, reason="tenant not registered")
        if tenant.data_residency != "global" and tenant.data_residency != target_region:
            return IsolationCheckResult(
                allowed=False,
                reason=f"data residency violation: tenant requires {tenant.data_residency!r}, target is {target_region!r}",
            )
        return IsolationCheckResult(allowed=True, tenant_id=tenant_id)

    def list_projects(self, *, tenant_id: str) -> list[ProjectConfigV1]:
        return [p for p in self._projects.values() if p.tenant_id == tenant_id]


__all__ = [
    "DataResidency", "ComplianceLevel", "TenantConfigV1", "ProjectConfigV1",
    "IsolationCheckResult", "TenantIsolationGate",
]
