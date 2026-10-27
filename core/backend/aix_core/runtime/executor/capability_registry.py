"""EX4-R2-P6-S001: Internal Capability Registry — reusable AIX execution capabilities.

Distinct from ontology Capability (Project domain aggregate). Marketplace out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError

COST_CLASSES = frozenset({"low", "medium", "high"})
RISK_CLASSES = frozenset({"low", "medium", "high"})
REGISTRY_STATUSES = frozenset({"ACTIVE", "DEPRECATED", "RETIRED"})

CostClass = Literal["low", "medium", "high"]
RiskClass = Literal["low", "medium", "high"]
RegistryStatus = Literal["ACTIVE", "DEPRECATED", "RETIRED"]


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


@dataclass(frozen=True)
class RegisteredCapability:
    """Versioned unit of reuse for AIX execution — not a Hyeve service, not ontology Capability."""

    capability_key: str
    version: str
    tenant_id: str
    display_name: str
    description: str = ""
    input_contract: dict[str, Any] = field(default_factory=dict)
    output_contract: dict[str, Any] = field(default_factory=dict)
    authority_requirements: dict[str, Any] = field(default_factory=dict)
    executor_binding: str = ""
    verification_method: str = ""
    dependencies: tuple[str, ...] = ()
    cost_class: str = "medium"
    risk_class: str = "medium"
    status: str = "ACTIVE"


@dataclass
class InMemoryCapabilityRegistry:
    _entries: dict[tuple[str, str, str], RegisteredCapability] = field(default_factory=dict)
    # (tenant_id, capability_key, version) -> entry

    def register(self, entry: RegisteredCapability) -> RegisteredCapability:
        tenant_id = _require_tenant(entry.tenant_id)
        key = str(entry.capability_key or "").strip()
        version = str(entry.version or "").strip()
        if not key or not version:
            raise ExecutionKernelRepositoryError("capability_key and version are required")
        if entry.cost_class not in COST_CLASSES:
            raise ExecutionKernelRepositoryError(f"invalid cost_class={entry.cost_class!r}")
        if entry.risk_class not in RISK_CLASSES:
            raise ExecutionKernelRepositoryError(f"invalid risk_class={entry.risk_class!r}")
        if entry.status not in REGISTRY_STATUSES:
            raise ExecutionKernelRepositoryError(f"invalid status={entry.status!r}")
        if not isinstance(entry.input_contract, dict) or not isinstance(entry.output_contract, dict):
            raise ExecutionKernelRepositoryError("input_contract and output_contract must be objects")
        if not isinstance(entry.authority_requirements, dict):
            raise ExecutionKernelRepositoryError("authority_requirements must be an object")
        normalized = RegisteredCapability(
            capability_key=key,
            version=version,
            tenant_id=tenant_id,
            display_name=str(entry.display_name or key).strip() or key,
            description=str(entry.description or ""),
            input_contract=dict(entry.input_contract),
            output_contract=dict(entry.output_contract),
            authority_requirements=dict(entry.authority_requirements),
            executor_binding=str(entry.executor_binding or "").strip(),
            verification_method=str(entry.verification_method or "").strip(),
            dependencies=tuple(str(d).strip() for d in entry.dependencies if str(d).strip()),
            cost_class=entry.cost_class,
            risk_class=entry.risk_class,
            status=entry.status,
        )
        self._entries[(tenant_id, key, version)] = normalized
        return normalized

    def get(
        self, *, tenant_id: str, capability_key: str, version: str | None = None
    ) -> RegisteredCapability | None:
        tenant_id = _require_tenant(tenant_id)
        key = str(capability_key or "").strip()
        if version:
            return self._entries.get((tenant_id, key, str(version).strip()))
        # Latest ACTIVE by version string sort (deterministic, not semver-aware — documented)
        matches = [
            e
            for (t, k, _v), e in self._entries.items()
            if t == tenant_id and k == key and e.status == "ACTIVE"
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda e: e.version)[-1]

    def list(
        self, *, tenant_id: str, active_only: bool = True, capability_key: str | None = None
    ) -> list[RegisteredCapability]:
        tenant_id = _require_tenant(tenant_id)
        out: list[RegisteredCapability] = []
        for (t, k, _v), e in self._entries.items():
            if t != tenant_id:
                continue
            if capability_key and k != capability_key:
                continue
            if active_only and e.status != "ACTIVE":
                continue
            out.append(e)
        return sorted(out, key=lambda e: (e.capability_key, e.version))

    def resolve_dependencies(
        self, *, tenant_id: str, capability_key: str, version: str | None = None
    ) -> list[RegisteredCapability]:
        """Return the capability plus ACTIVE dependency closures (1 level + recurse, cycle-safe)."""
        root = self.get(tenant_id=tenant_id, capability_key=capability_key, version=version)
        if root is None:
            raise ExecutionKernelRepositoryError(f"capability not found: {capability_key}")
        seen: set[str] = set()
        ordered: list[RegisteredCapability] = []

        def walk(cap: RegisteredCapability) -> None:
            if cap.capability_key in seen:
                return
            seen.add(cap.capability_key)
            for dep_key in cap.dependencies:
                dep = self.get(tenant_id=tenant_id, capability_key=dep_key)
                if dep is None:
                    raise ExecutionKernelRepositoryError(f"missing dependency: {dep_key}")
                walk(dep)
            ordered.append(cap)

        walk(root)
        return ordered


def registered_capability_to_dict(entry: RegisteredCapability) -> dict[str, Any]:
    return {
        "capability_key": entry.capability_key,
        "version": entry.version,
        "tenant_id": entry.tenant_id,
        "display_name": entry.display_name,
        "description": entry.description,
        "input_contract": dict(entry.input_contract),
        "output_contract": dict(entry.output_contract),
        "authority_requirements": dict(entry.authority_requirements),
        "executor_binding": entry.executor_binding,
        "verification_method": entry.verification_method,
        "dependencies": list(entry.dependencies),
        "cost_class": entry.cost_class,
        "risk_class": entry.risk_class,
        "status": entry.status,
    }


__all__ = [
    "COST_CLASSES",
    "RISK_CLASSES",
    "REGISTRY_STATUSES",
    "RegisteredCapability",
    "InMemoryCapabilityRegistry",
    "registered_capability_to_dict",
]
