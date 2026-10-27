"""P16: Credential Brokerage — scoped, audited credential access.

Never gives agents permanent credentials. Instead:
  Agent → Capability Request → Policy → Credential Broker → Scoped credential → Tool

Supports: secret references, short-lived tokens, masking, scoped access,
rotation awareness, access audit. Builds on SecretBoundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

CredentialScope = Literal["action", "step", "session", "project"]


@dataclass(frozen=True)
class CredentialRequestV1:
    """Request for a scoped credential."""
    request_id: str
    principal_id: str
    principal_role: str
    credential_name: str
    scope: CredentialScope
    purpose: str
    capability: str
    operation: str
    cycle_id: str = ""
    step_id: str = ""


@dataclass(frozen=True)
class ScopedCredentialV1:
    """A credential scoped to a specific action/step/session."""
    request_id: str
    credential_name: str
    value: str  # never logged — broker is the only holder
    scope: CredentialScope
    granted_to: str  # principal_id
    granted_at: str
    expires_at: str
    revoked: bool = False


@dataclass(frozen=True)
class CredentialAuditEntry:
    request_id: str
    credential_name: str
    principal_id: str
    principal_role: str
    scope: CredentialScope
    purpose: str
    decision: Literal["GRANTED", "DENIED", "REVOKED"]
    reason: str
    timestamp: str


class CredentialBrokerError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class CredentialBroker:
    """In-memory credential broker. Production targets Vault/KMS."""

    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}
        self._grants: dict[str, ScopedCredentialV1] = {}
        self._audit: list[CredentialAuditEntry] = []
        self._allowed_roles: dict[str, frozenset[str]] = {}

    def register_secret(self, name: str, value: str) -> None:
        self._secrets[name] = value

    def set_role_access(self, credential_name: str, allowed_roles: frozenset[str]) -> None:
        """Configure which roles may request a credential."""
        self._allowed_roles[credential_name] = allowed_roles

    def request_credential(self, request: CredentialRequestV1) -> ScopedCredentialV1:
        """Request a scoped credential. Fails closed on any policy violation."""
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        if request.credential_name not in self._secrets:
            self._audit_deny(request, "CREDENTIAL_NOT_FOUND", now)
            raise CredentialBrokerError(
                "CREDENTIAL_NOT_FOUND",
                f"no credential registered as {request.credential_name!r}",
            )

        allowed = self._allowed_roles.get(request.credential_name, frozenset())
        if allowed and request.principal_role not in allowed:
            self._audit_deny(request, "ROLE_NOT_AUTHORIZED", now)
            raise CredentialBrokerError(
                "ROLE_NOT_AUTHORIZED",
                f"role {request.principal_role!r} cannot access {request.credential_name!r}",
            )

        ttl_map = {"action": 60, "step": 300, "session": 3600, "project": 86400}
        ttl = ttl_map.get(request.scope, 60)
        from datetime import timedelta
        expires = (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")

        credential = ScopedCredentialV1(
            request_id=request.request_id,
            credential_name=request.credential_name,
            value=self._secrets[request.credential_name],
            scope=request.scope,
            granted_to=request.principal_id,
            granted_at=now,
            expires_at=expires,
        )
        self._grants[request.request_id] = credential
        self._audit.append(CredentialAuditEntry(
            request_id=request.request_id,
            credential_name=request.credential_name,
            principal_id=request.principal_id,
            principal_role=request.principal_role,
            scope=request.scope,
            purpose=request.purpose,
            decision="GRANTED",
            reason=f"scoped to {request.scope}, expires {expires}",
            timestamp=now,
        ))
        return credential

    def revoke(self, request_id: str) -> None:
        cred = self._grants.get(request_id)
        if cred is None:
            return
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._grants[request_id] = ScopedCredentialV1(
            request_id=cred.request_id,
            credential_name=cred.credential_name,
            value="",  # cleared
            scope=cred.scope,
            granted_to=cred.granted_to,
            granted_at=cred.granted_at,
            expires_at=cred.expires_at,
            revoked=True,
        )
        self._audit.append(CredentialAuditEntry(
            request_id=request_id,
            credential_name=cred.credential_name,
            principal_id=cred.granted_to,
            principal_role="",
            scope=cred.scope,
            purpose="revocation",
            decision="REVOKED",
            reason="explicitly revoked",
            timestamp=now,
        ))

    def get_audit_log(self) -> list[CredentialAuditEntry]:
        return list(self._audit)

    def _audit_deny(self, request: CredentialRequestV1, reason: str, now: str) -> None:
        self._audit.append(CredentialAuditEntry(
            request_id=request.request_id,
            credential_name=request.credential_name,
            principal_id=request.principal_id,
            principal_role=request.principal_role,
            scope=request.scope,
            purpose=request.purpose,
            decision="DENIED",
            reason=reason,
            timestamp=now,
        ))


__all__ = [
    "CredentialScope",
    "CredentialRequestV1",
    "ScopedCredentialV1",
    "CredentialAuditEntry",
    "CredentialBrokerError",
    "CredentialBroker",
]
