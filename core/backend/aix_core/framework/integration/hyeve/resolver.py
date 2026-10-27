"""AIX Framework Hyeve Capability Resolver.

Sprint: F9 Hyeve Capability Layer (F9-P1)
Maps capability requests to canonical Hyeve capability definitions.
"""

from __future__ import annotations

from typing import Any

from backend.aix_core.framework.integration.hyeve.manifest import (
    CANONICAL_HYEVE_CAPABILITIES,
    HyeveCapabilityDefinition,
)


class HyeveCapabilityResolver:
    """Resolves required platform capabilities against the Hyeve Fleet Manifest."""

    def __init__(self, custom_manifest: dict[str, HyeveCapabilityDefinition] | None = None) -> None:
        self._manifest = dict(custom_manifest or CANONICAL_HYEVE_CAPABILITIES)

    def resolve(self, capability_id: str) -> HyeveCapabilityDefinition | None:
        return self._manifest.get(capability_id)

    def list_capabilities(self) -> list[HyeveCapabilityDefinition]:
        return list(self._manifest.values())

    def has_capability(self, capability_id: str) -> bool:
        return capability_id in self._manifest


_default_resolver = HyeveCapabilityResolver()


def get_hyeve_capability_resolver() -> HyeveCapabilityResolver:
    return _default_resolver
