"""Tests for P15 (model routing), P16 (credential brokerage), P17 (CI/CD), P18 (external tools)."""

from __future__ import annotations

import pytest


# ── P15: Model Selector ─────────────────────────────────────────────────────

class TestModelSelector:
    def _candidates(self):
        from backend.aix_core.runtime.intelligence.model_selector import ModelCandidate
        return [
            ModelCandidate("gpt-4o-mini", "openai", "low", 128000, "fast",
                           strengths=("code", "structured")),
            ModelCandidate("claude-sonnet", "anthropic", "medium", 200000, "standard",
                           strengths=("code", "reasoning")),
            ModelCandidate("gpt-4o", "openai", "high", 128000, "standard",
                           strengths=("code", "reasoning", "structured")),
            ModelCandidate("o1-pro", "openai", "premium", 128000, "slow",
                           strengths=("reasoning",)),
        ]

    def test_selects_cheapest_for_trivial(self):
        from backend.aix_core.runtime.intelligence.model_selector import ModelSelector, TaskProfile
        selector = ModelSelector(self._candidates())
        decision = selector.select(TaskProfile(role="coder", complexity="trivial"))
        assert decision.selected_model.cost_tier in ("free", "low")

    def test_selects_reasoning_for_complex(self):
        from backend.aix_core.runtime.intelligence.model_selector import ModelSelector, TaskProfile
        selector = ModelSelector(self._candidates())
        decision = selector.select(TaskProfile(
            role="planner", complexity="complex", requires_reasoning=True,
        ))
        assert "reasoning" in decision.selected_model.strengths

    def test_respects_cost_ceiling(self):
        from backend.aix_core.runtime.intelligence.model_selector import ModelSelector, TaskProfile
        selector = ModelSelector(self._candidates())
        decision = selector.select(TaskProfile(
            role="coder", complexity="expert", max_cost_tier="medium",
        ))
        assert decision.selected_model.cost_tier in ("free", "low", "medium")

    def test_skips_previously_failed_models(self):
        from backend.aix_core.runtime.intelligence.model_selector import ModelSelector, TaskProfile
        selector = ModelSelector(self._candidates())
        decision = selector.select(TaskProfile(
            role="coder", complexity="simple", previous_failures=("gpt-4o-mini",),
        ))
        assert decision.selected_model.model_id != "gpt-4o-mini"

    def test_provides_fallbacks(self):
        from backend.aix_core.runtime.intelligence.model_selector import ModelSelector, TaskProfile
        selector = ModelSelector(self._candidates())
        decision = selector.select(TaskProfile(role="coder", complexity="moderate"))
        assert len(decision.fallback_models) > 0

    def test_empty_candidates_raises(self):
        from backend.aix_core.runtime.intelligence.model_selector import ModelSelector, TaskProfile
        selector = ModelSelector([])
        with pytest.raises(ValueError, match="no model candidates"):
            selector.select(TaskProfile(role="coder", complexity="simple"))


# ── P16: Credential Brokerage ────────────────────────────────────────────────

class TestCredentialBroker:
    def test_grant_and_revoke(self):
        from backend.aix_core.runtime.governance.credential_broker import (
            CredentialBroker, CredentialRequestV1,
        )
        broker = CredentialBroker()
        broker.register_secret("db_password", "secret123")
        broker.set_role_access("db_password", frozenset({"coder", "operations_agent"}))

        cred = broker.request_credential(CredentialRequestV1(
            request_id="r-1", principal_id="coder-1", principal_role="coder",
            credential_name="db_password", scope="step",
            purpose="database migration", capability="process", operation="run",
        ))
        assert cred.value == "secret123"
        assert cred.scope == "step"
        assert not cred.revoked

        broker.revoke("r-1")
        audit = broker.get_audit_log()
        revoked = [e for e in audit if e.decision == "REVOKED"]
        assert len(revoked) == 1

    def test_unauthorized_role_denied(self):
        from backend.aix_core.runtime.governance.credential_broker import (
            CredentialBroker, CredentialRequestV1, CredentialBrokerError,
        )
        broker = CredentialBroker()
        broker.register_secret("deploy_key", "key123")
        broker.set_role_access("deploy_key", frozenset({"release_agent"}))

        with pytest.raises(CredentialBrokerError, match="ROLE_NOT_AUTHORIZED"):
            broker.request_credential(CredentialRequestV1(
                request_id="r-2", principal_id="coder-1", principal_role="coder",
                credential_name="deploy_key", scope="action",
                purpose="deploy", capability="deployment", operation="deploy",
            ))

    def test_unknown_credential_denied(self):
        from backend.aix_core.runtime.governance.credential_broker import (
            CredentialBroker, CredentialRequestV1, CredentialBrokerError,
        )
        broker = CredentialBroker()
        with pytest.raises(CredentialBrokerError, match="CREDENTIAL_NOT_FOUND"):
            broker.request_credential(CredentialRequestV1(
                request_id="r-3", principal_id="coder-1", principal_role="coder",
                credential_name="nonexistent", scope="action",
                purpose="test", capability="process", operation="run",
            ))

    def test_audit_trail(self):
        from backend.aix_core.runtime.governance.credential_broker import (
            CredentialBroker, CredentialRequestV1,
        )
        broker = CredentialBroker()
        broker.register_secret("api_key", "key456")
        broker.set_role_access("api_key", frozenset({"researcher"}))

        broker.request_credential(CredentialRequestV1(
            request_id="r-4", principal_id="researcher-1", principal_role="researcher",
            credential_name="api_key", scope="session",
            purpose="API lookup", capability="network", operation="http_get",
        ))
        audit = broker.get_audit_log()
        assert len(audit) == 1
        assert audit[0].decision == "GRANTED"
        assert audit[0].scope == "session"

    def test_scope_ttl_differences(self):
        from backend.aix_core.runtime.governance.credential_broker import (
            CredentialBroker, CredentialRequestV1,
        )
        broker = CredentialBroker()
        broker.register_secret("token", "tok")

        action_cred = broker.request_credential(CredentialRequestV1(
            request_id="r-a", principal_id="agent-1", principal_role="coder",
            credential_name="token", scope="action",
            purpose="one-time", capability="process", operation="run",
        ))
        session_cred = broker.request_credential(CredentialRequestV1(
            request_id="r-s", principal_id="agent-1", principal_role="coder",
            credential_name="token", scope="session",
            purpose="session use", capability="process", operation="run",
        ))
        assert action_cred.expires_at < session_cred.expires_at


# ── P17: CI/CD Capabilities ─────────────────────────────────────────────────

class TestCICDCapabilities:
    def test_ci_status_registered(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        result = resolve_capability("ci", "status")
        assert result is not None
        assert result[2].mutates is False

    def test_ci_trigger_requires_approval(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        result = resolve_capability("ci", "trigger")
        assert result is not None
        assert result[2].mutates is True
        assert result[2].approval_required is True

    def test_ci_logs_registered(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        assert resolve_capability("ci", "logs") is not None

    def test_ci_artifacts_registered(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        assert resolve_capability("ci", "artifacts") is not None

    def test_deployment_deploy_requires_approval(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        result = resolve_capability("deployment", "deploy")
        assert result is not None
        assert result[2].approval_required is True
        assert result[2].risk == "high"

    def test_deployment_rollback_requires_approval(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        result = resolve_capability("deployment", "rollback")
        assert result is not None
        assert result[2].approval_required is True

    def test_catalog_includes_ci_deployment(self):
        from backend.aix_core.runtime.foundation.capability_registry import catalog_for_planner
        catalog = catalog_for_planner()
        cap_ops = {(c["capability"], c["operation"]) for c in catalog}
        assert ("ci", "trigger") in cap_ops
        assert ("deployment", "deploy") in cap_ops
        assert ("deployment", "rollback") in cap_ops


# ── P18: External Tool Registry (RETIRED in F2-P4) ───────────────────────────

class TestExternalToolRegistryRetired:
    def test_module_removed(self):
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("backend.aix_core.runtime.tools.external_tool_registry")
