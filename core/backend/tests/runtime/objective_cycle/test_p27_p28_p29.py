"""Tests for P27 (enterprise multi-tenancy), P28 (Hyeve convergence), P29 (autonomous operations)."""

from __future__ import annotations

import pytest


# ── P27: Enterprise Multi-Tenancy ────────────────────────────────────────────

class TestTenantIsolation:
    def test_cross_tenant_denied(self):
        from backend.aix_core.runtime.enterprise.tenant_isolation import (
            TenantIsolationGate, TenantConfigV1,
        )
        gate = TenantIsolationGate()
        gate.register_tenant(TenantConfigV1(tenant_id="acg", name="ACG Corp"))
        gate.register_tenant(TenantConfigV1(tenant_id="tenant-a", name="Tenant A"))

        result = gate.check_tenant_access(tenant_id="acg", requesting_tenant_id="tenant-a")
        assert result.allowed is False
        assert "cross-tenant" in result.reason

    def test_same_tenant_allowed(self):
        from backend.aix_core.runtime.enterprise.tenant_isolation import (
            TenantIsolationGate, TenantConfigV1,
        )
        gate = TenantIsolationGate()
        gate.register_tenant(TenantConfigV1(tenant_id="acg", name="ACG Corp"))
        result = gate.check_tenant_access(tenant_id="acg", requesting_tenant_id="acg")
        assert result.allowed is True

    def test_project_isolation(self):
        from backend.aix_core.runtime.enterprise.tenant_isolation import (
            TenantIsolationGate, TenantConfigV1, ProjectConfigV1,
        )
        gate = TenantIsolationGate()
        gate.register_tenant(TenantConfigV1(tenant_id="acg", name="ACG"))
        gate.register_tenant(TenantConfigV1(tenant_id="tenant-a", name="Tenant A"))
        gate.register_project(ProjectConfigV1(project_id="p-1", tenant_id="acg", name="Project A"))

        assert gate.check_project_access(project_id="p-1", tenant_id="acg").allowed is True
        assert gate.check_project_access(project_id="p-1", tenant_id="tenant-a").allowed is False

    def test_capability_denied_at_tenant_level(self):
        from backend.aix_core.runtime.enterprise.tenant_isolation import (
            TenantIsolationGate, TenantConfigV1, ProjectConfigV1,
        )
        gate = TenantIsolationGate()
        gate.register_tenant(TenantConfigV1(
            tenant_id="edu", name="Edprime",
            denied_capabilities=("deployment", "container"),
        ))
        gate.register_project(ProjectConfigV1(project_id="p-edu", tenant_id="edu", name="Edu Proj"))

        assert gate.check_capability(tenant_id="edu", project_id="p-edu", capability="deployment").allowed is False
        assert gate.check_capability(tenant_id="edu", project_id="p-edu", capability="filesystem").allowed is True

    def test_capability_denied_at_project_level(self):
        from backend.aix_core.runtime.enterprise.tenant_isolation import (
            TenantIsolationGate, TenantConfigV1, ProjectConfigV1,
        )
        gate = TenantIsolationGate()
        gate.register_tenant(TenantConfigV1(tenant_id="acg", name="ACG"))
        gate.register_project(ProjectConfigV1(
            project_id="p-prod", tenant_id="acg", name="Production",
            denied_capabilities=("process",),
        ))
        result = gate.check_capability(tenant_id="acg", project_id="p-prod", capability="process")
        assert result.allowed is False

    def test_model_restriction(self):
        from backend.aix_core.runtime.enterprise.tenant_isolation import (
            TenantIsolationGate, TenantConfigV1,
        )
        gate = TenantIsolationGate()
        gate.register_tenant(TenantConfigV1(
            tenant_id="restricted", name="Restricted Corp",
            allowed_models=("gpt-4o", "claude-sonnet"),
        ))
        assert gate.check_model(tenant_id="restricted", model_id="gpt-4o").allowed is True
        assert gate.check_model(tenant_id="restricted", model_id="llama-3").allowed is False

    def test_data_residency(self):
        from backend.aix_core.runtime.enterprise.tenant_isolation import (
            TenantIsolationGate, TenantConfigV1,
        )
        gate = TenantIsolationGate()
        gate.register_tenant(TenantConfigV1(
            tenant_id="eu-corp", name="EU Corp", data_residency="eu",
        ))
        assert gate.check_data_residency(tenant_id="eu-corp", target_region="eu").allowed is True
        assert gate.check_data_residency(tenant_id="eu-corp", target_region="us").allowed is False

    def test_project_requires_tenant(self):
        from backend.aix_core.runtime.enterprise.tenant_isolation import (
            TenantIsolationGate, ProjectConfigV1,
        )
        gate = TenantIsolationGate()
        with pytest.raises(ValueError, match="tenant"):
            gate.register_project(ProjectConfigV1(project_id="p-1", tenant_id="nonexistent", name="X"))


# ── P28: Hyeve Platform Convergence ──────────────────────────────────────────

class TestHyeveConvergence:
    def test_boundary_declaration(self):
        from backend.aix_core.runtime.enterprise.hyeve_convergence import CONVERGENCE_BOUNDARY
        assert "identity" in CONVERGENCE_BOUNDARY.hyeve_owns
        assert "capability_definitions" in CONVERGENCE_BOUNDARY.aix_owns
        assert "execution_policy" in CONVERGENCE_BOUNDARY.aix_owns

    def test_no_overlap(self):
        from backend.aix_core.runtime.enterprise.hyeve_convergence import CONVERGENCE_BOUNDARY
        overlap = set(CONVERGENCE_BOUNDARY.hyeve_owns) & set(CONVERGENCE_BOUNDARY.aix_owns)
        assert len(overlap) == 0, f"boundary overlap: {overlap}"

    def test_service_binding(self):
        from backend.aix_core.runtime.enterprise.hyeve_convergence import (
            HyeveIntegrationRegistry, HyeveServiceBindingV1,
        )
        registry = HyeveIntegrationRegistry()
        registry.register(HyeveServiceBindingV1(
            service="auth", endpoint="grpc://hyeve-auth:50051",
            owns=("identity", "roles"), consumes=(),
        ))
        assert registry.is_available("auth") is True
        assert registry.is_available("codeintel") is False

    def test_identity_resolution(self):
        from backend.aix_core.runtime.enterprise.hyeve_convergence import (
            HyeveIntegrationRegistry, HyeveServiceBindingV1,
        )
        registry = HyeveIntegrationRegistry()
        registry.register(HyeveServiceBindingV1(
            service="auth", endpoint="grpc://hyeve-auth:50051",
        ))
        identity = registry.resolve_identity(principal_id="agent-1", tenant_id="t1")
        assert identity is not None
        assert identity.principal_type == "agent"

    def test_identity_unavailable_without_binding(self):
        from backend.aix_core.runtime.enterprise.hyeve_convergence import HyeveIntegrationRegistry
        registry = HyeveIntegrationRegistry()
        assert registry.resolve_identity(principal_id="x", tenant_id="t1") is None


# ── P29: Enterprise Autonomous Operations ────────────────────────────────────

class TestAutonomousOperations:
    def test_full_operation_lifecycle(self):
        from backend.aix_core.runtime.enterprise.autonomous_operations import (
            AutonomousOperationOrchestrator, AutonomousOperationV1,
        )
        orch = AutonomousOperationOrchestrator()
        op = orch.create(AutonomousOperationV1(
            operation_id="op-1", tenant_id="t1", project_id="p-1",
            objective="Upgrade auth service, fix regressions, deploy to staging",
        ))
        assert op.phase == "understand"

        assert orch.transition("op-1", "plan") is not None
        assert orch.transition("op-1", "authorize") is not None
        assert orch.transition("op-1", "execute") is not None
        assert orch.transition("op-1", "test") is not None
        assert orch.transition("op-1", "review") is not None
        assert orch.transition("op-1", "commit") is not None
        assert orch.transition("op-1", "deploy") is not None
        assert orch.transition("op-1", "observe") is not None
        assert orch.transition("op-1", "report") is not None
        assert orch.transition("op-1", "remember") is not None
        assert orch.transition("op-1", "complete") is not None

        final = orch.get("op-1")
        assert final.outcome == "success"
        assert final.completed_at is not None
        assert len(final.phases_completed) == 11

    def test_invalid_transition_denied(self):
        from backend.aix_core.runtime.enterprise.autonomous_operations import (
            AutonomousOperationOrchestrator, AutonomousOperationV1,
        )
        orch = AutonomousOperationOrchestrator()
        orch.create(AutonomousOperationV1(
            operation_id="op-2", tenant_id="t1", project_id="p-1",
            objective="test",
        ))
        result = orch.transition("op-2", "deploy")  # can't jump to deploy from understand
        assert result is None
        assert orch.get("op-2").phase == "understand"

    def test_repair_loop(self):
        from backend.aix_core.runtime.enterprise.autonomous_operations import (
            AutonomousOperationOrchestrator, AutonomousOperationV1,
        )
        orch = AutonomousOperationOrchestrator()
        orch.create(AutonomousOperationV1(
            operation_id="op-3", tenant_id="t1", project_id="p-1",
            objective="fix bug",
        ))
        orch.transition("op-3", "plan")
        orch.transition("op-3", "authorize")
        orch.transition("op-3", "execute")
        orch.transition("op-3", "repair")  # execution failed
        orch.transition("op-3", "execute")  # retry
        orch.transition("op-3", "test")
        assert orch.get("op-3").phase == "test"

    def test_approval_required_for_production_deploy(self):
        from backend.aix_core.runtime.enterprise.autonomous_operations import (
            AutonomousOperationOrchestrator, AutonomousOperationV1,
        )
        orch = AutonomousOperationOrchestrator()
        orch.create(AutonomousOperationV1(
            operation_id="op-4", tenant_id="t1", project_id="p-1",
            objective="deploy", requires_approval_for=("deploy_production",),
        ))
        assert orch.requires_approval("op-4", "deploy_production") is True
        assert orch.requires_approval("op-4", "deploy_staging") is False

    def test_cancel(self):
        from backend.aix_core.runtime.enterprise.autonomous_operations import (
            AutonomousOperationOrchestrator, AutonomousOperationV1,
        )
        orch = AutonomousOperationOrchestrator()
        orch.create(AutonomousOperationV1(
            operation_id="op-5", tenant_id="t1", project_id="p-1",
            objective="cancel test",
        ))
        assert orch.cancel("op-5") is True
        assert orch.get("op-5").outcome == "cancelled"

    def test_transition_history(self):
        from backend.aix_core.runtime.enterprise.autonomous_operations import (
            AutonomousOperationOrchestrator, AutonomousOperationV1,
        )
        orch = AutonomousOperationOrchestrator()
        orch.create(AutonomousOperationV1(
            operation_id="op-6", tenant_id="t1", project_id="p-1",
            objective="history test",
        ))
        orch.transition("op-6", "plan", reason="decomposing objective")
        transitions = orch.get_transitions("op-6")
        assert len(transitions) == 1
        assert transitions[0].from_phase == "understand"
        assert transitions[0].to_phase == "plan"
