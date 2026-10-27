"""F2 Capability Framework models — projections over the live foundation catalog.

These types describe capabilities already registered in
``runtime.foundation.capability_registry``. They do not introduce a second catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any
import uuid

from backend.aix_core.runtime.foundation.capability_registry import (
    CapabilitySpec,
    OperationSpec,
)


class ProviderKind(str, Enum):
    """Provider taxonomy (F2-P2 owns routing; P1 only labels descriptors)."""

    LOCAL_TOOL = "LOCAL_TOOL"
    HYEVE_SERVICE = "HYEVE_SERVICE"
    LLM_INFERENCE = "LLM_INFERENCE"


class EffectKind(str, Enum):
    READ = "READ"
    MUTATE = "MUTATE"
    EXECUTE = "EXECUTE"
    NETWORK = "NETWORK"


@dataclass(frozen=True)
class CapabilityDescriptor:
    """Framework view of one foundation ``CapabilitySpec``."""

    capability_id: str
    description: str
    supported_operations: tuple[str, ...]
    provider_kind: ProviderKind = ProviderKind.LOCAL_TOOL
    effect_kind: EffectKind = EffectKind.READ
    version: str = "1.0.0"
    requires_approval: bool = False
    operations: tuple[OperationSpec, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "provider_kind": self.provider_kind.value,
            "description": self.description,
            "supported_operations": list(self.supported_operations),
            "effect_kind": self.effect_kind.value,
            "requires_approval": self.requires_approval,
        }


def _provider_kind_for(capability_id: str) -> ProviderKind:
    # Foundation catalog is local-tool executed today. Hyeve/LLM kinds land in F2-P2.
    if capability_id in {"network", "ci", "deployment"}:
        return ProviderKind.HYEVE_SERVICE
    return ProviderKind.LOCAL_TOOL


def _effect_kind_for(spec: CapabilitySpec) -> EffectKind:
    if any(op.mutates for op in spec.operations):
        if spec.name == "network":
            return EffectKind.NETWORK
        if spec.name == "process":
            return EffectKind.EXECUTE
        return EffectKind.MUTATE
    if spec.name == "process":
        return EffectKind.EXECUTE
    if spec.name == "network":
        return EffectKind.NETWORK
    return EffectKind.READ


def descriptor_from_capability_spec(spec: CapabilitySpec) -> CapabilityDescriptor:
    """Project a live foundation CapabilitySpec into a framework descriptor."""
    return CapabilityDescriptor(
        capability_id=spec.name,
        description=spec.description or spec.name,
        supported_operations=tuple(op.name for op in spec.operations),
        provider_kind=_provider_kind_for(spec.name),
        effect_kind=_effect_kind_for(spec),
        requires_approval=any(op.approval_required for op in spec.operations),
        operations=tuple(spec.operations),
    )


@dataclass(frozen=True)
class CapabilityExecutionRequest:
    """Typed execution request after resolution (used by provider adapters in F2-P3)."""

    capability_id: str
    operation: str
    caller_role: str
    arguments: dict[str, Any]
    request_id: str = ""
    workspace_path: str | None = None
    correlation_id: str = ""
    model_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.request_id:
            object.__setattr__(self, "request_id", _uuid_str())
        if not self.correlation_id:
            object.__setattr__(self, "correlation_id", self.request_id)


@dataclass(frozen=True)
class CapabilityExecutionResult:
    request_id: str
    success: bool
    changed: bool = False
    output: dict[str, Any] | None = None
    evidence_paths: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = None


def _uuid_str() -> str:
    return str(uuid.uuid4())


__all__ = [
    "CapabilityDescriptor",
    "CapabilityExecutionRequest",
    "CapabilityExecutionResult",
    "EffectKind",
    "ProviderKind",
    "descriptor_from_capability_spec",
]
