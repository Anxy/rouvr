from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

CANONICAL_TENANT_HEADER = "X-Tenant-ID"
CANONICAL_PROJECT_HEADER = "X-Project-ID"
CANONICAL_CORRELATION_HEADER = "X-Correlation-ID"
ACCEPTED_TENANT_HEADERS = ("X-Tenant-ID", "X-Tenant-Id", "X-Hyeve-Tenant-Id")
ACCEPTED_PROJECT_HEADERS = ("X-Project-ID", "X-Project-Id")


def build_service_headers(
    *,
    service_token: str | None = None,
    authorization: str | None = None,
    tenant_id: str | None = None,
    project_id: str | None = None,
    correlation_id: str | None = None,
    fallback_project_id: str | None = None,
    fallback_correlation_id: str | None = None,
    idempotency_key: str | None = None,
    auto_generate_correlation: bool = False,
    correlation_factory: Callable[[], str] | None = None,
    require_auth: bool = False,
    require_tenant: bool = False,
) -> dict[str, str]:
    auth_value = str(authorization or "").strip()
    if not auth_value and service_token:
        auth_value = f"Bearer {service_token}"
    if require_auth and not auth_value:
        raise ValueError("Authorization header required")

    tenant_value = str(tenant_id or "").strip()
    if require_tenant and not tenant_value:
        raise ValueError(f"{CANONICAL_TENANT_HEADER} header required")

    resolved_project_id = str(project_id or "").strip() or str(fallback_project_id or "").strip()
    resolved_correlation_id = str(correlation_id or "").strip() or str(fallback_correlation_id or "").strip()
    if auto_generate_correlation and not resolved_correlation_id:
        generate = correlation_factory or (lambda: f"corr-{uuid4().hex}")
        resolved_correlation_id = str(generate() or "").strip()

    headers: dict[str, str] = {}
    if auth_value:
        headers["Authorization"] = auth_value
    if tenant_value:
        headers[CANONICAL_TENANT_HEADER] = tenant_value
    if resolved_project_id:
        headers[CANONICAL_PROJECT_HEADER] = resolved_project_id
    if resolved_correlation_id:
        headers[CANONICAL_CORRELATION_HEADER] = resolved_correlation_id
    if idempotency_key and str(idempotency_key).strip():
        headers["Idempotency-Key"] = str(idempotency_key).strip()
    return headers


def get_header_alias(headers: Any, *names: str) -> str | None:
    for name in names:
        value = str(getattr(headers, "get", lambda *_args, **_kwargs: None)(name) or "").strip()
        if value:
            return value
    return None


def resolve_tenant_header(headers: Any, default: str | None = None) -> str | None:
    return get_header_alias(headers, *ACCEPTED_TENANT_HEADERS) or (str(default or "").strip() or None)


def resolve_project_header(headers: Any, default: str | None = None) -> str | None:
    return get_header_alias(headers, *ACCEPTED_PROJECT_HEADERS) or (str(default or "").strip() or None)


def diagnose_service_token(token: str | None) -> dict[str, Any]:
    """Return a lightweight diagnostic for a service-to-service JWT or opaque token.

    For JWTs, decodes the payload segment and checks the ``exp`` claim against
    ``time.time()``.  No signature verification is performed — this is purely a
    fail-fast expiry diagnostic so callers can detect stale env tokens before
    making API calls.

    Returns a dict with keys:
    - ``token_kind``: ``"jwt"`` | ``"opaque"`` | ``"empty"``
    - ``expired``: ``True`` if a JWT exp claim is in the past, else ``False``
    - ``exp``: the raw exp value (int) or ``None``
    - ``seconds_until_expiry``: positive = still valid, negative = already expired, ``None`` if unknown
    - ``diagnostic``: human-readable summary string
    """
    raw = str(token or "").strip()
    if not raw:
        return {
            "token_kind": "empty",
            "expired": True,
            "exp": None,
            "seconds_until_expiry": None,
            "diagnostic": "token is empty — service-to-service calls will fail with 401",
        }

    parts = raw.split(".")
    if len(parts) != 3:
        return {
            "token_kind": "opaque",
            "expired": False,
            "exp": None,
            "seconds_until_expiry": None,
            "diagnostic": "opaque token (not a JWT) — cannot determine expiry",
        }

    try:
        # Pad to a multiple of 4 for base64 decoding
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
    except Exception:
        return {
            "token_kind": "jwt",
            "expired": False,
            "exp": None,
            "seconds_until_expiry": None,
            "diagnostic": "JWT payload could not be decoded — treating as non-expired",
        }

    exp = payload.get("exp")
    if exp is None:
        return {
            "token_kind": "jwt",
            "expired": False,
            "exp": None,
            "seconds_until_expiry": None,
            "diagnostic": "JWT has no exp claim — expiry unknown",
        }

    try:
        exp_int = int(exp)
    except (TypeError, ValueError):
        return {
            "token_kind": "jwt",
            "expired": False,
            "exp": exp,
            "seconds_until_expiry": None,
            "diagnostic": "JWT exp claim is not a valid integer",
        }

    now = time.time()
    seconds_until_expiry = exp_int - now
    expired = seconds_until_expiry < 0
    if expired:
        diagnostic = f"JWT is expired ({abs(int(seconds_until_expiry))}s ago) — refresh or rotate the service token"
    else:
        diagnostic = f"JWT is valid (expires in {int(seconds_until_expiry)}s)"
    return {
        "token_kind": "jwt",
        "expired": expired,
        "exp": exp_int,
        "seconds_until_expiry": seconds_until_expiry,
        "diagnostic": diagnostic,
    }


def inspect_service_token_claims(token: str | None) -> dict[str, Any]:
    raw = str(token or "").strip()
    if not raw:
        return {
            "token_kind": "empty",
            "claims_decoded": False,
            "service": False,
            "roles": [],
            "tenant_id": None,
            "sub": None,
        }

    parts = raw.split(".")
    if len(parts) != 3:
        return {
            "token_kind": "opaque",
            "claims_decoded": False,
            "service": False,
            "roles": [],
            "tenant_id": None,
            "sub": None,
        }

    try:
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
    except Exception:
        return {
            "token_kind": "jwt",
            "claims_decoded": False,
            "service": False,
            "roles": [],
            "tenant_id": None,
            "sub": None,
        }

    roles_raw = payload.get("roles")
    if isinstance(roles_raw, str):
        roles = [roles_raw.strip().lower()] if roles_raw.strip() else []
    elif isinstance(roles_raw, list):
        roles = [str(role).strip().lower() for role in roles_raw if str(role).strip()]
    else:
        roles = []

    return {
        "token_kind": "jwt",
        "claims_decoded": True,
        "service": bool(payload.get("service")),
        "roles": roles,
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "sub": str(payload.get("sub") or "").strip() or None,
    }
