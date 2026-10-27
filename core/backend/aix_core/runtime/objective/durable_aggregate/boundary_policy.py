"""C019: sandbox, egress, secret, and resource boundary policy.

Wraps objective_cycle sandbox + secret_boundary with a single fail-closed surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.aix_core.runtime.governance.sandbox import CommandNetworkResourceSandbox
from backend.aix_core.runtime.governance.secret_boundary import (
    ResolvedSecretV1,
    SecretBoundary,
    SecretRefV1,
    looks_like_unredacted_secret,
    redact_objective_text,
)


class BoundaryError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


_EGRESS_BLOCKED_HOSTS = frozenset({"metadata.google.internal", "169.254.169.254", "localhost"})


@dataclass(frozen=True)
class ResourceLimits:
    max_seconds: float = 120.0
    max_output_bytes: int = 2_000_000
    max_files_touched: int = 500


@dataclass
class BoundaryPolicy:
    allow_network: bool = False
    allowed_hosts: frozenset[str] = frozenset()
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    _sandbox: CommandNetworkResourceSandbox = field(default_factory=CommandNetworkResourceSandbox)

    def assert_command_safe(self, command: str, *, allowlist: tuple[str, ...] = ()) -> None:
        decision = self._sandbox.evaluate_command(command=command, command_allowlist=allowlist or ())
        if decision.disposition != "ALLOW":
            raise BoundaryError("COMMAND_INJECTION", ",".join(decision.reason_codes))

    def assert_egress_allowed(self, host: str) -> None:
        h = (host or "").strip().lower()
        if not h:
            raise BoundaryError("EGRESS_HOST_REQUIRED")
        if not self.allow_network:
            raise BoundaryError("EGRESS_DENIED", h)
        if h in _EGRESS_BLOCKED_HOSTS:
            raise BoundaryError("EGRESS_DENIED", h)
        if self.allowed_hosts and h not in self.allowed_hosts:
            raise BoundaryError("EGRESS_DENIED", h)

    def assert_resource_within_limits(
        self, *, seconds: float, output_bytes: int, files_touched: int
    ) -> None:
        if seconds > self.limits.max_seconds:
            raise BoundaryError("RESOURCE_EXHAUSTION", f"seconds={seconds}")
        if output_bytes > self.limits.max_output_bytes:
            raise BoundaryError("RESOURCE_EXHAUSTION", f"output_bytes={output_bytes}")
        if files_touched > self.limits.max_files_touched:
            raise BoundaryError("RESOURCE_EXHAUSTION", f"files_touched={files_touched}")

    def redact_secrets(self, text: str, known_values: dict[str, str] | None = None) -> str:
        if known_values:
            boundary = SecretBoundary(known_values, now="2099-01-01T00:00:00Z")
            resolved = [
                ResolvedSecretV1(
                    ref=SecretRefV1(name=name, rotated_at="2000-01-01T00:00:00Z"),
                    value=value,
                )
                for name, value in known_values.items()
            ]
            return boundary.redact_text(text or "", resolved=resolved)
        redacted, _kinds = redact_objective_text(text or "")
        return redacted

    def assert_no_secret_leak(self, text: str, known_values: dict[str, str] | None = None) -> None:
        raw = text or ""
        if known_values:
            boundary = SecretBoundary(known_values, now="2099-01-01T00:00:00Z")
            resolved = [
                ResolvedSecretV1(
                    ref=SecretRefV1(name=name, rotated_at="2000-01-01T00:00:00Z"),
                    value=value,
                )
                for name, value in known_values.items()
            ]
            try:
                boundary.assert_no_leakage(raw, resolved=resolved)
            except Exception as exc:
                raise BoundaryError("SECRET_LEAK", str(exc)) from exc
        if looks_like_unredacted_secret(raw):
            raise BoundaryError("SECRET_LEAK", "unredacted secret-shaped material")


def default_boundary_policy() -> BoundaryPolicy:
    return BoundaryPolicy(allow_network=False, allowed_hosts=frozenset(), limits=ResourceLimits())


__all__ = [
    "BoundaryError",
    "ResourceLimits",
    "BoundaryPolicy",
    "default_boundary_policy",
]
