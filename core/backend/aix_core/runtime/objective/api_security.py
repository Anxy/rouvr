"""Gateway-only authentication context and tenant-scoped cycle RBAC."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from enum import StrEnum
from typing import Callable, Iterable

from pydantic import BaseModel, ConfigDict, Field

SECURITY_POLICY_VERSION = "aix.objective-cycle.api-security.v1"


class CyclePermission(StrEnum):
    READ = "aix:cycle:read"
    COMMAND = "aix:cycle:command"
    ADMIN = "aix:cycle:admin"


class CycleApiSecurityError(PermissionError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class GatewayAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authorization: str = Field(min_length=1)
    gateway_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    roles: tuple[str, ...] = Field(min_length=1)
    issued_at_epoch: int = Field(ge=1)
    request_id: str = Field(min_length=1)
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class CycleApiSecurityContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = SECURITY_POLICY_VERSION
    gateway_id: str
    tenant_id: str
    actor_id: str
    roles: tuple[str, ...]
    request_id: str


def _canonical_message(
    *,
    gateway_id: str,
    tenant_id: str,
    actor_id: str,
    roles: Iterable[str],
    issued_at_epoch: int,
    request_id: str,
) -> bytes:
    role_text = ",".join(sorted({str(role).strip() for role in roles if str(role).strip()}))
    return "|".join(
        (
            gateway_id,
            tenant_id,
            actor_id,
            role_text,
            str(issued_at_epoch),
            request_id,
        )
    ).encode("utf-8")


def sign_gateway_assertion(
    *,
    secret: bytes,
    gateway_id: str,
    tenant_id: str,
    actor_id: str,
    roles: Iterable[str],
    issued_at_epoch: int,
    request_id: str,
) -> str:
    if not secret:
        raise ValueError("gateway assertion secret is required")
    return hmac.new(
        secret,
        _canonical_message(
            gateway_id=gateway_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            roles=roles,
            issued_at_epoch=issued_at_epoch,
            request_id=request_id,
        ),
        hashlib.sha256,
    ).hexdigest()


class CycleApiSecurityPolicy:
    def __init__(
        self,
        *,
        secret: bytes,
        allowed_gateway_ids: Iterable[str],
        max_assertion_age_seconds: int = 60,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not secret:
            raise ValueError("gateway assertion secret is required")
        self._secret = bytes(secret)
        self._allowed_gateway_ids = frozenset(str(item).strip() for item in allowed_gateway_ids if str(item).strip())
        if not self._allowed_gateway_ids:
            raise ValueError("at least one gateway identity is required")
        if max_assertion_age_seconds < 1:
            raise ValueError("max_assertion_age_seconds must be positive")
        self._max_age = max_assertion_age_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._seen_request_ids: set[str] = set()

    def authorize(
        self,
        assertion: GatewayAssertion,
        *,
        required_permission: CyclePermission,
        resource_tenant_id: str | None = None,
    ) -> CycleApiSecurityContext:
        if not assertion.authorization.startswith("Bearer ") or not assertion.authorization.removeprefix(
            "Bearer "
        ).strip():
            raise CycleApiSecurityError("AUTH_BEARER_REQUIRED")
        if assertion.gateway_id not in self._allowed_gateway_ids:
            raise CycleApiSecurityError("GATEWAY_IDENTITY_FORBIDDEN")
        now_epoch = int(self._clock().timestamp())
        age = now_epoch - assertion.issued_at_epoch
        if age < 0 or age > self._max_age:
            raise CycleApiSecurityError("GATEWAY_ASSERTION_EXPIRED")
        expected = sign_gateway_assertion(
            secret=self._secret,
            gateway_id=assertion.gateway_id,
            tenant_id=assertion.tenant_id,
            actor_id=assertion.actor_id,
            roles=assertion.roles,
            issued_at_epoch=assertion.issued_at_epoch,
            request_id=assertion.request_id,
        )
        if not hmac.compare_digest(expected, assertion.signature):
            raise CycleApiSecurityError("GATEWAY_ASSERTION_INVALID")
        if assertion.request_id in self._seen_request_ids:
            raise CycleApiSecurityError("GATEWAY_ASSERTION_REPLAYED")
        if resource_tenant_id is not None and assertion.tenant_id != resource_tenant_id:
            raise CycleApiSecurityError("TENANT_SCOPE_MISMATCH")
        roles = set(assertion.roles)
        if required_permission.value not in roles and CyclePermission.ADMIN.value not in roles:
            raise CycleApiSecurityError("CYCLE_RBAC_FORBIDDEN")
        self._seen_request_ids.add(assertion.request_id)
        return CycleApiSecurityContext(
            gateway_id=assertion.gateway_id,
            tenant_id=assertion.tenant_id,
            actor_id=assertion.actor_id,
            roles=tuple(sorted(roles)),
            request_id=assertion.request_id,
        )
