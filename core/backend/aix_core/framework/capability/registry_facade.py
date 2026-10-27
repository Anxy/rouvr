"""F2-P1 CapabilityRegistryFacade — additive view over foundation capability_registry.

Delegates resolve/list to the live SoR. Does not register a parallel catalog.
Flag ``AIX_FRAMEWORK_CAPABILITY_FACADE`` (default false) gates future OC wiring only;
this module never mutates Objective Cycle paths by itself.
"""

from __future__ import annotations

from typing import Any

from backend.aix_core.framework.capability.models import (
    CapabilityDescriptor,
    descriptor_from_capability_spec,
)
from backend.aix_core.runtime.foundation.capability_registry import (
    CAPABILITIES,
    CapabilitySpec,
    OperationSpec,
    all_registered_capability_ids,
    catalog_for_planner,
    resolve_capability,
    resolve_or_coerce_capability,
)
from backend.aix_core.runtime.foundation.policy_gateway import PolicyDecision, authorize_action
from backend.aix_core.runtime.objective.contracts import ActionRequestV1


class CapabilityRegistryFacade:
    """Thin façade over ``runtime.foundation.capability_registry`` (+ policy authorize).

    Construction never copies or mutates foundation registration tables.
    """

    def __init__(self, *, capabilities: tuple[CapabilitySpec, ...] | None = None) -> None:
        # Optional inject for tests only — must be a subset/view of foundation specs.
        self._capabilities: tuple[CapabilitySpec, ...] = (
            capabilities if capabilities is not None else CAPABILITIES
        )
        self._descriptors: dict[str, CapabilityDescriptor] = {
            spec.name: descriptor_from_capability_spec(spec) for spec in self._capabilities
        }

    def get_descriptor(self, capability_id: str) -> CapabilityDescriptor | None:
        return self._descriptors.get(str(capability_id or "").strip())

    def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return tuple(self._descriptors[spec.name] for spec in self._capabilities)

    def resolve(
        self, capability: str, operation: str
    ) -> tuple[str, str, OperationSpec] | None:
        """Identical to foundation ``resolve_capability`` for the live catalog."""
        return resolve_capability(capability, operation)

    def resolve_or_coerce(
        self, capability: str, operation: str
    ) -> tuple[str, str, OperationSpec] | None:
        """Identical to foundation ``resolve_or_coerce_capability``."""
        return resolve_or_coerce_capability(capability, operation)

    def registered_ids(self) -> frozenset[str]:
        return all_registered_capability_ids()

    def catalog_for_planner(self) -> list[dict[str, object]]:
        return catalog_for_planner()

    def authorize(
        self,
        request: ActionRequestV1,
        *,
        repository_id: str,
        repository_root: Any = None,
        allowed_files: tuple[str, ...] = (),
        apply_edits: bool = True,
        action_budget_remaining: int = 8,
        mutation_budget_remaining: int = 8,
        step_approval_required: bool = False,
        approved: bool = True,
        execution_context: Any = None,
    ) -> PolicyDecision:
        """Delegate authorization to live ``policy_gateway.authorize_action``."""
        return authorize_action(
            request,
            repository_id=repository_id,
            repository_root=repository_root,
            allowed_files=allowed_files,
            apply_edits=apply_edits,
            action_budget_remaining=action_budget_remaining,
            mutation_budget_remaining=mutation_budget_remaining,
            step_approval_required=step_approval_required,
            approved=approved,
            execution_context=execution_context,
        )


_default_facade: CapabilityRegistryFacade | None = None


def get_capability_registry_facade() -> CapabilityRegistryFacade:
    global _default_facade
    if _default_facade is None:
        _default_facade = CapabilityRegistryFacade()
    return _default_facade


__all__ = [
    "CapabilityRegistryFacade",
    "get_capability_registry_facade",
]
