"""C018: tool/effect capability registry.

DoD: no executor effect outside an admitted, certified, environment-compatible,
policy-authorized registry capability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


CapabilityStatus = Literal[
    "AVAILABLE",
    "UNAVAILABLE",
    "UNCERTIFIED",
    "UNAUTHORIZED",
    "ENVIRONMENT_MISMATCH",
    "TEMPORARILY_UNHEALTHY",
    "NEEDS_APPROVAL",
    "UNKNOWN",
]


class CapabilityError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CapabilitySpec:
    capability_id: str
    effect_kind: str  # read | patch | test | network | shell
    certified: bool
    requires_approval: bool = False
    environments: tuple[str, ...] = ()
    status: CapabilityStatus = "AVAILABLE"


@dataclass
class CapabilityRegistry:
    _caps: dict[str, CapabilitySpec] = field(default_factory=dict)

    def register(self, spec: CapabilitySpec) -> None:
        self._caps[spec.capability_id] = spec

    def get(self, capability_id: str) -> CapabilitySpec | None:
        return self._caps.get(capability_id)

    def resolve(
        self,
        *,
        capability_id: str,
        authorized: bool,
        environment_fingerprint_id: str | None = None,
        approved: bool = False,
    ) -> CapabilitySpec:
        spec = self._caps.get(capability_id)
        if spec is None:
            raise CapabilityError("UNKNOWN", capability_id)
        if not authorized:
            raise CapabilityError("UNAUTHORIZED", capability_id)
        if not spec.certified:
            raise CapabilityError("UNCERTIFIED", capability_id)
        if spec.status == "TEMPORARILY_UNHEALTHY":
            raise CapabilityError("TEMPORARILY_UNHEALTHY", capability_id)
        if spec.status == "UNAVAILABLE":
            raise CapabilityError("UNAVAILABLE", capability_id)
        if spec.requires_approval and not approved:
            raise CapabilityError("NEEDS_APPROVAL", capability_id)
        if spec.environments and environment_fingerprint_id:
            # environments holds allowed fingerprint ids or language tags.
            if environment_fingerprint_id not in spec.environments and not any(
                environment_fingerprint_id.startswith(f"env_") for _ in [0]
            ):
                # Soft match: if environments lists language tags, caller should pass langs.
                pass
        if spec.environments and environment_fingerprint_id and environment_fingerprint_id not in spec.environments:
            # Only hard-fail when environments looks like fingerprint ids.
            if all(e.startswith("env_") for e in spec.environments):
                raise CapabilityError("ENVIRONMENT_MISMATCH", capability_id)
        return spec

    def admit_effect(
        self,
        *,
        capability_id: str,
        authorized: bool,
        environment_fingerprint_id: str | None = None,
        approved: bool = False,
    ) -> dict[str, Any]:
        spec = self.resolve(
            capability_id=capability_id,
            authorized=authorized,
            environment_fingerprint_id=environment_fingerprint_id,
            approved=approved,
        )
        return {
            "ok": True,
            "capability_id": spec.capability_id,
            "effect_kind": spec.effect_kind,
            "status": "AVAILABLE",
        }


def default_registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    for spec in (
        CapabilitySpec("filesystem.read", "read", certified=True),
        CapabilitySpec("filesystem.patch", "patch", certified=True, requires_approval=True),
        CapabilitySpec("tests.run", "test", certified=True),
        CapabilitySpec("network.egress", "network", certified=False),
        CapabilitySpec("shell.exec", "shell", certified=True, requires_approval=True),
    ):
        reg.register(spec)
    return reg


__all__ = [
    "CapabilityStatus",
    "CapabilityError",
    "CapabilitySpec",
    "CapabilityRegistry",
    "default_registry",
]
