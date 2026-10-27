"""W02: Role → model_ref registry — delegates to canonical aix_core ModelRegistry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

IntelligenceRole = Literal["conversation", "planner", "executor", "verifier"]

KNOWN_ROLES: tuple[IntelligenceRole, ...] = (
    "conversation",
    "planner",
    "executor",
    "verifier",
)

_INTELLIGENCE_TO_CANONICAL: dict[str, str] = {
    "conversation": "objective_understanding",
    "planner": "planner",
    "executor": "executor",
    "verifier": "verifier",
}


@dataclass(frozen=True)
class ModelRegistryV1:
    """Thin adapter over canonical ModelRegistry for intelligence-layer call sites."""

    enabled: bool
    stream_enabled: bool

    def model_ref_for(self, role: IntelligenceRole, **route_kwargs: object) -> str:
        from backend.aix_core.runtime.model_registry.registry import get_model_registry

        canonical = _INTELLIGENCE_TO_CANONICAL.get(role, role)
        registry = get_model_registry()
        route = registry.get_route(canonical, **route_kwargs)  # type: ignore[arg-type]
        return route.model_ref

    def as_dict(self) -> dict[str, object]:
        from backend.aix_core.runtime.model_registry.registry import get_model_registry

        registry = get_model_registry()
        return {
            "enabled": self.enabled,
            "stream_enabled": self.stream_enabled,
            "models": {
                role: self.model_ref_for(role) for role in KNOWN_ROLES
            },
            "profiles": list(registry._profiles.keys()),  # noqa: SLF001
        }


def load_model_registry() -> ModelRegistryV1:
    from backend.hyeve_aix_service.config import settings

    return ModelRegistryV1(
        enabled=bool(settings.INTELLIGENCE_LAYER_ENABLED),
        stream_enabled=bool(settings.INTELLIGENCE_LAYER_STREAM_ENABLED),
    )
