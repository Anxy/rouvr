from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.experimental.agent_roles import AgentRoleId, FIVE_ROLE_DECISIONS
from backend.aix_core.persona_runtime.capability_registry import (
    NO_REAL_CONTENT_SOURCE_REF,
    CapabilityRegistry,
    CapabilityRegistryError,
    build_default_registry,
)
from backend.aix_core.persona_runtime.entities import CapabilityV1


def _capability(capability_id: str = "cap.test") -> CapabilityV1:
    return CapabilityV1(capability_id=capability_id, capability_name="Test", requested_tools=("read_file",))


def test_register_requires_source_ref() -> None:
    registry = CapabilityRegistry()
    with pytest.raises(CapabilityRegistryError, match="CAPABILITY_SOURCE_REF_REQUIRED"):
        registry.register(_capability(), source_ref="")
    with pytest.raises(CapabilityRegistryError, match="CAPABILITY_SOURCE_REF_REQUIRED"):
        registry.register(_capability(), source_ref="   ")


def test_register_and_get_round_trips() -> None:
    registry = CapabilityRegistry()
    capability = _capability()
    registry.register(capability, source_ref="unit test fixture, not real production content")
    assert registry.get("cap.test") == capability
    assert registry.list_ids() == ("cap.test",)


def test_duplicate_version_registration_rejected() -> None:
    registry = CapabilityRegistry()
    registry.register(_capability(), source_ref="first registration")
    with pytest.raises(CapabilityRegistryError, match="CAPABILITY_VERSION_ALREADY_REGISTERED"):
        registry.register(_capability(), source_ref="second registration, same id+version")


def test_resolve_omits_unregistered_ids_without_raising() -> None:
    registry = CapabilityRegistry()
    registry.register(_capability("cap.a"), source_ref="fixture")
    resolved = registry.resolve(("cap.a", "cap.does_not_exist"))
    assert len(resolved) == 1
    assert resolved[0].capability_id == "cap.a"


def test_deprecate_removes_from_active_but_keeps_history() -> None:
    registry = CapabilityRegistry()
    registry.register(_capability(), source_ref="fixture")
    registry.deprecate("cap.test", reason="superseded in test")
    with pytest.raises(CapabilityRegistryError, match="CAPABILITY_NOT_REGISTERED"):
        registry.get("cap.test")
    assert any(event["event"] == "deprecate" for event in registry.evidence())


def test_get_unregistered_raises() -> None:
    registry = CapabilityRegistry()
    with pytest.raises(CapabilityRegistryError, match="CAPABILITY_NOT_REGISTERED"):
        registry.get("cap.nonexistent")


# --- build_default_registry: real data, no fabrication ---


def test_default_registry_has_exactly_one_capability_per_role() -> None:
    registry = build_default_registry()
    expected_ids = {f"cap.{decision.role_id.value}.tools" for decision in FIVE_ROLE_DECISIONS}
    assert set(registry.list_ids()) == expected_ids


def test_default_registry_executor_coder_has_real_extracted_content_and_honest_source_ref() -> None:
    registry = build_default_registry()
    record = registry.get_record("cap.executor_coder.tools")
    assert record.capability.tool_invocation_protocol  # real extracted content, non-empty
    assert "PlannerPromptBuilder" in record.source_ref
    assert NO_REAL_CONTENT_SOURCE_REF not in record.source_ref


@pytest.mark.parametrize(
    "role",
    [AgentRoleId.PLANNER, AgentRoleId.VERIFIER, AgentRoleId.RECOVERY_PLANNER, AgentRoleId.REVIEW_PACKET_CURATOR],
)
def test_default_registry_other_four_roles_have_no_fabricated_content(role: AgentRoleId) -> None:
    """Locks in the explicit instruction: no invented knowledge/protocol content for
    roles with no real prompt code. The registry must say so honestly, not silently."""
    registry = build_default_registry()
    record = registry.get_record(f"cap.{role.value}.tools")
    assert record.capability.knowledge == ()
    assert record.capability.verification_rules == ()
    assert record.capability.tool_invocation_protocol == ()
    assert NO_REAL_CONTENT_SOURCE_REF in record.source_ref
    # requested_tools must still be real, non-fabricated data from the manifest.
    assert record.capability.requested_tools
