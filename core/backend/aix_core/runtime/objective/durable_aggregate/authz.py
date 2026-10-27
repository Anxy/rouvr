"""C014: authorization, ownership, and tenant-bound idempotency.

DoD: an ID, token, or key from tenant A cannot observe or control tenant B.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class AuthzError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ActorClaims:
    tenant_id: str
    actor_id: str
    workspace_id: str | None = None
    roles: tuple[str, ...] = ()


def require_tenant(tenant_id: str) -> str:
    tid = str(tenant_id or "").strip()
    if not tid:
        raise AuthzError("EMPTY_TENANT", "tenant_id required")
    if tid.lower() in {"default", "public", "*"}:
        raise AuthzError("FORBIDDEN_TENANT", tid)
    return tid


def require_actor(actor_id: str) -> str:
    aid = str(actor_id or "").strip()
    if not aid:
        raise AuthzError("EMPTY_ACTOR", "actor_id required")
    return aid


def bind_idempotency_key(*, tenant_id: str, operation: str, idempotency_key: str) -> str:
    """Tenant-bound idempotency binding — keys never cross tenants."""
    tid = require_tenant(tenant_id)
    key = str(idempotency_key or "").strip()
    if not key:
        raise AuthzError("EMPTY_IDEMPOTENCY_KEY", operation)
    if ":" in key and key.split(":", 1)[0] not in {tid, operation}:
        # Reject keys that embed a foreign tenant prefix.
        maybe_tenant = key.split(":", 1)[0]
        if maybe_tenant.startswith("tenant") and maybe_tenant != tid:
            raise AuthzError("CROSS_TENANT_IDEMPOTENCY_KEY", key)
    return f"{tid}:{operation}:{key}"


def assert_same_tenant(*, resource_tenant_id: str, caller_tenant_id: str, resource_id: str) -> None:
    require_tenant(caller_tenant_id)
    require_tenant(resource_tenant_id)
    if resource_tenant_id != caller_tenant_id:
        raise AuthzError("CROSS_TENANT_ACCESS", resource_id)


def claims_from_request(*, tenant_id: str, actor_id: str, body: Mapping[str, Any] | None = None) -> ActorClaims:
    tid = require_tenant(tenant_id)
    aid = require_actor(actor_id)
    body = body or {}
    # Reject body that tries to override tenant.
    if "tenant_id" in body and str(body.get("tenant_id") or "").strip() not in {"", tid}:
        raise AuthzError("TENANT_OVERRIDE_FORBIDDEN", str(body.get("tenant_id")))
    workspace_id = str(body.get("workspace_id") or "").strip() or None
    return ActorClaims(tenant_id=tid, actor_id=aid, workspace_id=workspace_id)


def authz_error_payload(*, operation: str, tenant_id: str, exc: AuthzError) -> dict[str, Any]:
    return {
        "ok": False,
        "operation": operation,
        "tenant_id": tenant_id,
        "error": {"code": exc.code, "detail": exc.detail},
    }


__all__ = [
    "AuthzError",
    "ActorClaims",
    "require_tenant",
    "require_actor",
    "bind_idempotency_key",
    "assert_same_tenant",
    "claims_from_request",
    "authz_error_payload",
]
