"""Tests for runtime agent_registry (temporary canonical; not a framework.agent shim)."""

from __future__ import annotations

import pytest


def test_handoff_module_import_integrity():
    import backend.aix_core.runtime.agents.handoff as handoff_mod

    assert hasattr(handoff_mod, "HandoffRequestV1")
    assert hasattr(handoff_mod, "HandoffResultV1")
    assert hasattr(handoff_mod, "HandoffStatus")


def test_agent_registry_shim_exports():
    from backend.aix_core.runtime.agents.agent_registry import (
        AGENT_ROLES,
        AGENT_SPECS,
        CODER,
        OPERATIONS_AGENT,
        PLANNER,
        RELEASE_AGENT,
        RESEARCHER,
        REVIEWER,
        SECURITY_REVIEWER,
        TESTER,
        AgentBudget,
        AgentCapabilityGrant,
        AgentRole,
        AgentSpec,
        agent_catalog,
        can_delegate,
        get_agent_spec,
        is_operation_allowed,
    )

    assert "planner" in AGENT_ROLES
    assert "coder" in AGENT_ROLES
    assert len(AGENT_SPECS) == 8

    planner_spec = get_agent_spec("planner")
    assert planner_spec is not None
    assert planner_spec.role == "planner"

    # Delegation checks
    assert can_delegate("planner", "coder") is True
    assert can_delegate("coder", "planner") is False

    # Operation permission checks
    assert is_operation_allowed("planner", "filesystem", "read") is True
    assert is_operation_allowed("planner", "filesystem", "patch") is False
    assert is_operation_allowed("coder", "filesystem", "patch") is True
    assert is_operation_allowed("coder", "filesystem", "delete") is False

    # Catalog
    catalog = agent_catalog()
    assert len(catalog) == 8
    assert any(c["role"] == "planner" for c in catalog)
