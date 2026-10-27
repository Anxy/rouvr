"""F2 Capability Framework — additive façade + provider + tool wiring (F2-P1..P3).

Feature flag: ``AIX_FRAMEWORK_CAPABILITY_FACADE`` (default False).
When disabled, Objective Cycle must not import or branch on this package.
"""

from __future__ import annotations

from backend.aix_core.framework.capability.models import (
    CapabilityDescriptor,
    CapabilityExecutionRequest,
    CapabilityExecutionResult,
    EffectKind,
    ProviderKind,
    descriptor_from_capability_spec,
)
from backend.aix_core.framework.capability.provider import (
    CapabilityProvider,
    CapabilityProviderResolver,
    CapabilityResolutionResult,
    ProviderRouter,
)
from backend.aix_core.framework.capability.registry_facade import (
    CapabilityRegistryFacade,
    get_capability_registry_facade,
)
from backend.aix_core.framework.capability.tool_wiring import (
    ToolCapabilityAdapter,
    bind_local_tool_provider,
)


def capability_facade_enabled(settings: object | None = None) -> bool:
    """Return True only when AIX_FRAMEWORK_CAPABILITY_FACADE is explicitly enabled."""
    if settings is not None and hasattr(settings, "AIX_FRAMEWORK_CAPABILITY_FACADE"):
        return bool(getattr(settings, "AIX_FRAMEWORK_CAPABILITY_FACADE"))
    try:
        from backend.hyeve_aix_service.config import settings as _settings

        return bool(getattr(_settings, "AIX_FRAMEWORK_CAPABILITY_FACADE", False))
    except Exception:
        return False


__all__ = [
    "CapabilityDescriptor",
    "CapabilityExecutionRequest",
    "CapabilityExecutionResult",
    "CapabilityProvider",
    "CapabilityProviderResolver",
    "CapabilityRegistryFacade",
    "CapabilityResolutionResult",
    "EffectKind",
    "ProviderKind",
    "ProviderRouter",
    "ToolCapabilityAdapter",
    "bind_local_tool_provider",
    "capability_facade_enabled",
    "descriptor_from_capability_spec",
    "get_capability_registry_facade",
]
