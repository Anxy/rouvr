"""Remote Hyeve Auth response parsing helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RemoteAuthError(Exception):
    """Controlled remote-auth failure that service dependencies can map to HTTP."""

    status_code: int
    detail: str
    code: str


def extract_bearer_token(authorization: str | None) -> str:
    """Extract a token from an Authorization header."""
    if not authorization:
        raise RemoteAuthError(401, "Authorization header required", "SYS.UNAUTHENTICATED")
    value = authorization.strip()
    if value.lower() == "bearer":
        raise RemoteAuthError(401, "Authorization header required", "SYS.UNAUTHENTICATED")
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if not value:
        raise RemoteAuthError(401, "Authorization header required", "SYS.UNAUTHENTICATED")
    return value


def build_auth_validate_url(core_api_url: Any | None) -> str:
    """Build the Hyeve Auth validate URL from a configured Core/Auth base URL."""
    base_url = str(core_api_url or "http://localhost:8000").strip().rstrip("/")
    if not base_url:
        raise RemoteAuthError(503, "Authentication service URL is not configured", "AUTH.UPSTREAM_UNAVAILABLE")
    if base_url.endswith("/v1/auth"):
        return f"{base_url}/validate"
    if base_url.endswith("/auth"):
        return f"{base_url}/validate"
    if base_url.endswith("/v1"):
        return f"{base_url}/auth/validate"
    if "/v1/" not in base_url and not base_url.endswith("/v1"):
        return f"{base_url}/v1/auth/validate"
    return f"{base_url}/auth/validate"


def _required_string(payload: dict[str, Any], *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise RemoteAuthError(
        502,
        f"Malformed auth response: missing {names[0]}",
        "AUTH.CONTRACT_MALFORMED",
    )


def _optional_string(payload: dict[str, Any], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise RemoteAuthError(
        502,
        f"Malformed auth response: {name} must be a string",
        "AUTH.CONTRACT_MALFORMED",
    )


def _string_list(payload: dict[str, Any], name: str) -> list[str]:
    value = payload.get(name)
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RemoteAuthError(
            502,
            f"Malformed auth response: {name} must be a list of strings",
            "AUTH.CONTRACT_MALFORMED",
        )
    return value


def parse_remote_auth_context(
    payload: dict[str, Any],
    *,
    requested_tenant_id: str | None,
    auth_source: str = "hyeve-auth-service",
    correlation_id: str | None = None,
    trace_id: str | None = None,
    allow_service_token: bool = False,
) -> dict[str, Any]:
    """Parse and validate the canonical remote Auth context."""
    if not isinstance(payload, dict):
        raise RemoteAuthError(502, "Malformed auth response: object required", "AUTH.CONTRACT_MALFORMED")

    active = payload.get("active", True)
    if active is not True:
        raise RemoteAuthError(401, "Invalid or expired token", "SYS.UNAUTHENTICATED")

    tenant_id = _required_string(payload, "tenant_id")
    requested = (requested_tenant_id or "").strip()
    if requested and tenant_id != requested:
        raise RemoteAuthError(403, "Tenant mismatch", "SYS.UNAUTHORIZED")

    if allow_service_token:
        user_id = payload.get("user_id") or payload.get("sub") or payload.get("service_id")
        if not isinstance(user_id, str) or not user_id.strip():
            raise RemoteAuthError(
                502,
                "Malformed auth response: missing user_id",
                "AUTH.CONTRACT_MALFORMED",
            )
        user_id = user_id.strip()
    else:
        user_id = _required_string(payload, "user_id", "sub")

    roles = _string_list(payload, "roles")
    permissions = _string_list(payload, "permissions")
    scopes = _string_list(payload, "scopes")

    return {
        "id": user_id,
        "user_id": user_id,
        "sub": user_id,
        "tenant_id": tenant_id,
        "person_id": _optional_string(payload, "person_id"),
        "email": _optional_string(payload, "email"),
        "name": _optional_string(payload, "name"),
        "roles": roles,
        "permissions": permissions,
        "scopes": scopes,
        "auth_source": auth_source,
        "session_id": _optional_string(payload, "session_id"),
        "expires_at": payload.get("expires_at", payload.get("exp")),
        "active": True,
        "is_active": True,
        "correlation_id": correlation_id,
        "trace_id": trace_id,
    }
