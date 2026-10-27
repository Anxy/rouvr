"""F2-P1 — CapabilityRegistryFacade parity with foundation capability_registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.aix_core.framework.capability import (
    CapabilityRegistryFacade,
    capability_facade_enabled,
)
from backend.aix_core.runtime.foundation.capability_registry import (
    CAPABILITIES,
    catalog_for_planner,
    resolve_capability,
    resolve_or_coerce_capability,
)
from backend.aix_core.runtime.foundation.policy_gateway import authorize_action
from backend.aix_core.runtime.objective.contracts import ActionRequestV1
from backend.hyeve_aix_service.config import settings


def test_flag_defaults_off() -> None:
    assert bool(getattr(settings, "AIX_FRAMEWORK_CAPABILITY_FACADE", True)) is False
    assert capability_facade_enabled() is False
    assert capability_facade_enabled(settings) is False


def test_facade_lists_exact_foundation_capabilities() -> None:
    facade = CapabilityRegistryFacade()
    listed = facade.list_capabilities()
    assert len(listed) == len(CAPABILITIES)
    assert [d.capability_id for d in listed] == [c.name for c in CAPABILITIES]
    for desc, spec in zip(listed, CAPABILITIES, strict=True):
        assert desc.supported_operations == tuple(op.name for op in spec.operations)
        assert desc.description == (spec.description or spec.name)


def test_resolve_parity_for_every_registered_operation() -> None:
    facade = CapabilityRegistryFacade()
    for cap in CAPABILITIES:
        for op in cap.operations:
            direct = resolve_capability(cap.name, op.name)
            via = facade.resolve(cap.name, op.name)
            assert via is not None
            assert direct is not None
            assert via == direct
            # Aliases resolve identically
            for alias in op.aliases:
                assert facade.resolve(cap.name, alias) == resolve_capability(cap.name, alias)


def test_resolve_or_coerce_parity() -> None:
    facade = CapabilityRegistryFacade()
    samples = [
        ("filesystem", "read"),
        ("filesystem", "create_complete_files"),  # coerce path
        ("not_a_real_capability", "nope"),
        ("CREATE_FILE", "write"),
    ]
    for capability, operation in samples:
        assert facade.resolve_or_coerce(capability, operation) == resolve_or_coerce_capability(
            capability, operation
        )


def test_catalog_for_planner_parity() -> None:
    facade = CapabilityRegistryFacade()
    assert facade.catalog_for_planner() == catalog_for_planner()


def test_authorize_delegates_to_policy_gateway(tmp_path: Path) -> None:
    facade = CapabilityRegistryFacade()
    request = ActionRequestV1(
        action_id="a1",
        capability="filesystem",
        operation="read",
        arguments={"path": "README.md"},
    )
    via = facade.authorize(
        request,
        repository_id="repo-1",
        repository_root=tmp_path,
        allowed_files=("README.md",),
        apply_edits=False,
    )
    direct = authorize_action(
        request,
        repository_id="repo-1",
        repository_root=tmp_path,
        allowed_files=("README.md",),
        apply_edits=False,
    )
    assert via == direct


def test_unknown_capability_denied_parity(tmp_path: Path) -> None:
    facade = CapabilityRegistryFacade()
    request = ActionRequestV1(
        action_id="a2",
        capability="nope.capability",
        operation="explode",
        arguments={},
    )
    via = facade.authorize(request, repository_id="repo-1", repository_root=tmp_path)
    direct = authorize_action(request, repository_id="repo-1", repository_root=tmp_path)
    assert via.allowed is False
    assert via == direct
    assert via.code == "CAPABILITY_NOT_AVAILABLE"


def test_facade_does_not_invent_parallel_catalog_ids() -> None:
    """Guard: design sketch workspace.file / llm.reasoning must NOT appear as SoR."""
    facade = CapabilityRegistryFacade()
    ids = {d.capability_id for d in facade.list_capabilities()}
    assert "workspace.file" not in ids
    assert "llm.reasoning" not in ids
    assert "filesystem" in ids
    assert "repository" in ids


def test_no_objective_cycle_imports_capability_facade() -> None:
    """Legacy guard retired: F2 wiring may import facade under flag. Kept as no-op document."""
    assert capability_facade_enabled() is False
