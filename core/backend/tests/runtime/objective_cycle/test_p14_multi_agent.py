"""Tests for P14: Multi-Agent Runtime."""

from __future__ import annotations

import pytest


class TestAgentRegistry:
    def test_all_roles_registered(self):
        from backend.aix_core.runtime.agents.agent_registry import AGENT_SPECS, AGENT_ROLES
        assert set(AGENT_SPECS.keys()) == AGENT_ROLES

    def test_planner_cannot_mutate(self):
        from backend.aix_core.runtime.agents.agent_registry import is_operation_allowed
        assert is_operation_allowed("planner", "filesystem", "read") is True
        assert is_operation_allowed("planner", "filesystem", "patch") is False
        assert is_operation_allowed("planner", "filesystem", "create") is False
        assert is_operation_allowed("planner", "filesystem", "delete") is False
        assert is_operation_allowed("planner", "filesystem", "mkdir") is False

    def test_coder_can_patch_but_not_delete(self):
        from backend.aix_core.runtime.agents.agent_registry import is_operation_allowed
        assert is_operation_allowed("coder", "filesystem", "patch") is True
        assert is_operation_allowed("coder", "filesystem", "create") is True
        assert is_operation_allowed("coder", "filesystem", "read") is True
        assert is_operation_allowed("coder", "filesystem", "delete") is False

    def test_coder_cannot_commit(self):
        from backend.aix_core.runtime.agents.agent_registry import is_operation_allowed
        assert is_operation_allowed("coder", "git", "status") is True
        assert is_operation_allowed("coder", "git", "diff") is True
        assert is_operation_allowed("coder", "git", "commit") is False

    def test_tester_is_read_only(self):
        from backend.aix_core.runtime.agents.agent_registry import is_operation_allowed
        assert is_operation_allowed("tester", "filesystem", "read") is True
        assert is_operation_allowed("tester", "tests", "run") is True
        assert is_operation_allowed("tester", "tests", "coverage") is True
        assert is_operation_allowed("tester", "filesystem", "patch") is False
        assert is_operation_allowed("tester", "filesystem", "create") is False

    def test_reviewer_cannot_mutate_anything(self):
        from backend.aix_core.runtime.agents.agent_registry import is_operation_allowed
        assert is_operation_allowed("reviewer", "filesystem", "read") is True
        assert is_operation_allowed("reviewer", "git", "diff") is True
        assert is_operation_allowed("reviewer", "filesystem", "patch") is False
        assert is_operation_allowed("reviewer", "git", "commit") is False
        assert is_operation_allowed("reviewer", "process", "run") is False

    def test_release_agent_can_commit(self):
        from backend.aix_core.runtime.agents.agent_registry import is_operation_allowed
        assert is_operation_allowed("release_agent", "git", "commit") is True
        assert is_operation_allowed("release_agent", "git", "create_branch") is True
        assert is_operation_allowed("release_agent", "git", "stage") is True

    def test_researcher_can_http_get_not_post(self):
        from backend.aix_core.runtime.agents.agent_registry import is_operation_allowed
        assert is_operation_allowed("researcher", "network", "http_get") is True
        assert is_operation_allowed("researcher", "network", "http_post") is False

    def test_unknown_role_denied(self):
        from backend.aix_core.runtime.agents.agent_registry import is_operation_allowed
        assert is_operation_allowed("hacker", "filesystem", "read") is False

    def test_to_principal(self):
        from backend.aix_core.runtime.agents.agent_registry import CODER
        p = CODER.to_principal("coder-instance-42")
        assert p.type == "agent"
        assert p.id == "coder-instance-42"
        assert p.role == "coder"

    def test_agent_catalog(self):
        from backend.aix_core.runtime.agents.agent_registry import agent_catalog
        catalog = agent_catalog()
        roles = {a["role"] for a in catalog}
        assert "planner" in roles
        assert "coder" in roles
        assert "tester" in roles
        assert "reviewer" in roles
        assert len(catalog) == 8


class TestAgentDelegation:
    def test_planner_can_delegate_to_coder(self):
        from backend.aix_core.runtime.agents.agent_registry import can_delegate
        assert can_delegate("planner", "coder") is True

    def test_planner_can_delegate_to_tester(self):
        from backend.aix_core.runtime.agents.agent_registry import can_delegate
        assert can_delegate("planner", "tester") is True

    def test_planner_can_delegate_to_researcher(self):
        from backend.aix_core.runtime.agents.agent_registry import can_delegate
        assert can_delegate("planner", "researcher") is True

    def test_coder_can_delegate_to_tester(self):
        from backend.aix_core.runtime.agents.agent_registry import can_delegate
        assert can_delegate("coder", "tester") is True

    def test_coder_cannot_delegate_to_planner(self):
        from backend.aix_core.runtime.agents.agent_registry import can_delegate
        assert can_delegate("coder", "planner") is False

    def test_tester_cannot_delegate(self):
        from backend.aix_core.runtime.agents.agent_registry import can_delegate
        assert can_delegate("tester", "coder") is False
        assert can_delegate("tester", "planner") is False

    def test_reviewer_cannot_delegate(self):
        from backend.aix_core.runtime.agents.agent_registry import can_delegate
        assert can_delegate("reviewer", "coder") is False


class TestHandoffContracts:
    def test_valid_handoff_planner_to_coder(self):
        from backend.aix_core.runtime.agents.handoff import (
            HandoffRequestV1, validate_handoff,
        )
        req = HandoffRequestV1(
            handoff_id="h-1",
            from_agent_id="planner-1",
            from_role="planner",
            to_role="coder",
            cycle_id="cyc-1",
            step_id="step-1",
            objective="Implement the UserService class",
        )
        validate_handoff(req)  # should not raise

    def test_invalid_delegation_rejected(self):
        from backend.aix_core.runtime.agents.handoff import (
            HandoffRequestV1, validate_handoff, HandoffError,
        )
        req = HandoffRequestV1(
            handoff_id="h-2",
            from_agent_id="tester-1",
            from_role="tester",
            to_role="coder",
            cycle_id="cyc-1",
            step_id="step-1",
            objective="Fix the bug",
        )
        with pytest.raises(HandoffError, match="DELEGATION_NOT_AUTHORIZED"):
            validate_handoff(req)

    def test_empty_objective_rejected(self):
        from backend.aix_core.runtime.agents.handoff import (
            HandoffRequestV1, validate_handoff, HandoffError,
        )
        req = HandoffRequestV1(
            handoff_id="h-3",
            from_agent_id="planner-1",
            from_role="planner",
            to_role="coder",
            cycle_id="cyc-1",
            step_id="step-1",
            objective="",
        )
        with pytest.raises(HandoffError, match="EMPTY_OBJECTIVE"):
            validate_handoff(req)

    def test_unknown_target_role_rejected(self):
        from backend.aix_core.runtime.agents.handoff import (
            HandoffRequestV1, validate_handoff, HandoffError,
        )
        req = HandoffRequestV1(
            handoff_id="h-4",
            from_agent_id="planner-1",
            from_role="planner",
            to_role="coder",  # valid role but...
            cycle_id="cyc-1",
            step_id="step-1",
            objective="Do something",
        )
        # This should pass since planner->coder is valid
        validate_handoff(req)

    def test_complete_handoff(self):
        from backend.aix_core.runtime.agents.handoff import (
            HandoffRequestV1, complete_handoff,
        )
        req = HandoffRequestV1(
            handoff_id="h-5",
            from_agent_id="planner-1",
            from_role="planner",
            to_role="coder",
            cycle_id="cyc-1",
            step_id="step-1",
            objective="Add the feature",
        )
        result = complete_handoff(
            req, "coder-1",
            outputs={"files_changed": ["src/feature.py"]},
            evidence_refs=("diff://abc123",),
            tokens_used=5000,
        )
        assert result.status == "COMPLETED"
        assert result.to_agent_id == "coder-1"
        assert result.outputs["files_changed"] == ["src/feature.py"]

    def test_fail_handoff(self):
        from backend.aix_core.runtime.agents.handoff import (
            HandoffRequestV1, fail_handoff,
        )
        req = HandoffRequestV1(
            handoff_id="h-6",
            from_agent_id="planner-1",
            from_role="planner",
            to_role="coder",
            cycle_id="cyc-1",
            step_id="step-1",
            objective="Impossible task",
        )
        result = fail_handoff(
            req, "coder-1",
            error_code="BUDGET_EXHAUSTED",
            error_detail="ran out of tokens",
        )
        assert result.status == "FAILED"
        assert result.error_code == "BUDGET_EXHAUSTED"


class TestAgentPolicyIntegration:
    """Verify that agent roles integrate with the existing PolicyStore."""

    def test_planner_denied_patch_via_policy(self):
        from backend.aix_core.runtime.foundation.policy_store import (
            PolicyStore, PolicyRuleV1,
        )
        store = PolicyStore()
        store.add_rule(PolicyRuleV1(
            id="deny-planner-mutation",
            capability="filesystem",
            operation="patch",
            decision="DENY",
            principal_role="planner",
            reason="planners cannot mutate",
        ))
        decision, rule = store.evaluate(
            capability="filesystem", operation="patch",
            principal_role="planner",
        )
        assert decision == "DENY"

    def test_coder_allowed_patch_via_policy(self):
        from backend.aix_core.runtime.foundation.policy_store import (
            PolicyStore, PolicyRuleV1,
        )
        store = PolicyStore()
        store.add_rule(PolicyRuleV1(
            id="deny-planner-mutation",
            capability="filesystem",
            operation="patch",
            decision="DENY",
            principal_role="planner",
        ))
        store.add_rule(PolicyRuleV1(
            id="allow-coder-mutation",
            capability="filesystem",
            operation="patch",
            decision="ALLOW",
            principal_role="coder",
        ))
        decision, _ = store.evaluate(
            capability="filesystem", operation="patch",
            principal_role="coder",
        )
        assert decision == "ALLOW"

    def test_reviewer_approval_authority(self):
        from backend.aix_core.runtime.agents.agent_registry import REVIEWER
        assert REVIEWER.can_approve is True

    def test_coder_no_approval_authority(self):
        from backend.aix_core.runtime.agents.agent_registry import CODER
        assert CODER.can_approve is False
