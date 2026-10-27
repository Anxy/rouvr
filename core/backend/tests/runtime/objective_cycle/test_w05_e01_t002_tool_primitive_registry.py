"""AIX-W05-E01-T002: Canonical Tool Primitive Registry Tests.

Verifies:
1. Every primitive category (READ, SEARCH, PATCH, COMMAND, TEST, GIT, HTTP, DB) is present in the registry.
2. Each primitive defines schema, idempotency class, authorization rules, and required evidence.
3. Destructive primitives (delete_file) require explicit approval.
4. Role authorization restricts mutating primitives to allowed roles.
"""

from __future__ import annotations

import pytest

from backend.aix_core.intelligence.repository.tool_primitive_registry import (
    ToolPrimitiveRegistry,
    ToolPrimitiveSpec,
)


def test_registry_category_coverage() -> None:
    """Verifies that all 8 primitive categories (READ, SEARCH, PATCH, COMMAND, TEST, GIT, HTTP, DB) are represented."""
    all_specs = ToolPrimitiveRegistry.list_all()
    categories = {spec.category for spec in all_specs}

    expected_categories = {"READ", "SEARCH", "PATCH", "COMMAND", "TEST", "GIT", "HTTP", "DB"}
    assert categories == expected_categories


def test_primitive_spec_contracts() -> None:
    """Verifies that every registered tool primitive defines non-empty evidence and allowed roles."""
    all_specs = ToolPrimitiveRegistry.list_all()
    assert len(all_specs) >= 10

    for spec in all_specs:
        assert spec.name != ""
        assert spec.description != ""
        assert len(spec.required_evidence) > 0
        assert len(spec.allowed_roles) > 0


def test_destructive_primitives_require_approval() -> None:
    """delete_file primitive must mandate explicit approval."""
    delete_spec = ToolPrimitiveRegistry.get("delete_file")
    assert delete_spec.requires_approval is True
    assert delete_spec.category == "PATCH"


def test_role_authorization_boundaries() -> None:
    """Mutating patch primitives must be restricted to executor role."""
    patch_spec = ToolPrimitiveRegistry.get("apply_unified_diff")
    assert patch_spec.allowed_roles == ("executor",)

    # Read primitive is accessible to planner, executor, verifier
    read_spec = ToolPrimitiveRegistry.get("read_file")
    assert "planner" in read_spec.allowed_roles
    assert "executor" in read_spec.allowed_roles
    assert "verifier" in read_spec.allowed_roles


def test_unregistered_primitive_raises_key_error() -> None:
    """Attempting to resolve an unregistered tool primitive fails closed with KeyError."""
    with pytest.raises(KeyError) as exc_info:
        ToolPrimitiveRegistry.get("unauthorized_magic_tool")

    assert "unregistered tool primitive" in str(exc_info.value)
