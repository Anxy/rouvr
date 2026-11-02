"""Hyeve auth integration for the ledger (hackathon cut of Slice 1).

Verification is local: tokens issued by hyeve-auth-service are checked
against its JWKS (no per-request network beyond key fetch + cache).
Upstream writes (person create, user create, login) are thin httpx calls
so tests can monkeypatch them. Fail closed everywhere: missing config or
an unverifiable token raises HyeveAuthError, never a silent pass.

Env:
  HYEVE_AUTH_BASE_URL   e.g. the auth service base URL (enables the flow)
  HYEVE_AUTH_TENANT     the one Hyeve tenant Rouvr lives in (rouvr-demo)
  HYEVE_PERSON_BASE_URL person service base; unset = person provisioning off
  HYEVE_PERSON_PATH     person create path (default /v1/persons)
  HYEVE_PERSON_TOKEN    optional bearer for the person service
"""
from __future__ import annotations

import os
import uuid

import httpx


class HyeveAuthError(RuntimeError):
    """Upstream auth/person failure or unverifiable token. str() is a
    stable machine-readable code, safe to surface to the caller."""


def auth_base() -> str:
    return os.environ.get("HYEVE_AUTH_BASE_URL", "").rstrip("/")


def auth_tenant() -> str:
    return os.environ.get("HYEVE_AUTH_TENANT", "")


def enabled() -> bool:
    return bool(auth_base() and auth_tenant())


def _headers(extra: dict | None = None) -> dict:
    h = {"X-Hyeve-Tenant-Id": auth_tenant(),
         "Idempotency-Key": str(uuid.uuid4())}
    if extra:
        h.update(extra)
    return h


_jwk_clients: dict[str, object] = {}


def verify_hyeve_jwt(token: str) -> dict:
    """Verify signature + expiry against the auth service JWKS.
    Returns claims. Raises HyeveAuthError on any failure."""
    if not enabled():
        raise HyeveAuthError("HYEVE_AUTH_NOT_CONFIGURED")
    import jwt
    from jwt import PyJWKClient

    url = f"{auth_base()}/v1/auth/jwks.json"
    client = _jwk_clients.get(url)
    if client is None:
        client = PyJWKClient(url, headers={"X-Hyeve-Tenant-Id": auth_tenant()})
        _jwk_clients[url] = client
    try:
        key = client.get_signing_key_from_jwt(token)
        # Audience is token-specific on this service (service tokens carry
        # their target audience); accept any aud but require the claim set
        # to identify a subject in our tenant below.
        claims = jwt.decode(token, key.key, algorithms=["RS256", "ES256"],
                            options={"verify_aud": False})
    except Exception as e:  # jwt errors + transport: all fail closed
        raise HyeveAuthError(f"HYEVE_TOKEN_INVALID:{type(e).__name__}") from e
    if not claims.get("sub"):
        raise HyeveAuthError("HYEVE_TOKEN_NO_SUBJECT")
    tenant_claim = claims.get("tenant_id") or claims.get("tenant")
    if tenant_claim and tenant_claim != auth_tenant():
        raise HyeveAuthError("HYEVE_TOKEN_WRONG_TENANT")
    return claims


def create_person(*, given_name: str, family_name: str, display_name: str,
                  person_type: str = "Customer") -> str:
    """Create a person in the person service, return its person_id.
    The auth service refuses user creation without one."""
    base = os.environ.get("HYEVE_PERSON_BASE_URL", "").rstrip("/")
    if not base:
        raise HyeveAuthError("PERSON_SERVICE_NOT_CONFIGURED")
    path = os.environ.get("HYEVE_PERSON_PATH", "/v1/persons")
    extra = {}
    tok = os.environ.get("HYEVE_PERSON_TOKEN", "")
    if tok:
        extra["Authorization"] = f"Bearer {tok}"
    try:
        with httpx.Client(timeout=15) as c:
            r = c.post(base + path, headers=_headers(extra), json={
                "person_type": person_type, "given_name": given_name,
                "family_name": family_name, "display_name": display_name})
    except httpx.TransportError as e:
        raise HyeveAuthError(f"PERSON_SERVICE_UNREACHABLE:{type(e).__name__}") from e
    if r.status_code not in (200, 201):
        raise HyeveAuthError(f"PERSON_CREATE_FAILED:{r.status_code}")
    body = r.json()
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    pid = data.get("person_id") or data.get("id")
    if not pid:
        raise HyeveAuthError("PERSON_CREATE_NO_ID")
    return str(pid)


def create_user(*, username: str, password: str, email: str,
                  person_id: str = "", person: dict | None = None,
                  token: str = "") -> dict:
    """Create the auth user. Two deployment shapes exist (2026-11-02):
    VPS1 build requires the inline `person` object ("create/link") and
    has no person_id field; hosted build requires a pre-created
    person_id. Send exactly one, never both."""
    if not enabled():
        raise HyeveAuthError("HYEVE_AUTH_NOT_CONFIGURED")
    payload: dict = {"username": username, "password": password, "email": email}
    if person:
        payload["person"] = person
    elif person_id:
        payload["person_id"] = person_id
    else:
        raise HyeveAuthError("PERSON_REQUIRED:pass person or person_id")
    extra = {}
    if token:
        extra["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(timeout=15) as c:
            r = c.post(f"{auth_base()}/v1/auth/users", headers=_headers(extra), json=payload)
    except httpx.TransportError as e:
        raise HyeveAuthError(f"HYEVE_AUTH_UNREACHABLE:{type(e).__name__}") from e
    if r.status_code not in (200, 201):
        detail = ""
        try:
            detail = str(r.json().get("code") or r.json().get("detail") or "")[:120]
        except ValueError:
            pass
        raise HyeveAuthError(f"USER_CREATE_FAILED:{r.status_code}:{detail}")
    body = r.json()
    return body.get("data") if isinstance(body.get("data"), dict) else body


ROUVR_PERMISSION = "rouvr:use"
ROUVR_ROLE = "rouvr-user"

_admin: dict = {}


def admin_token() -> str:
    """Tenant-admin session (rouvr-demo-owner): cached until near expiry.
    Credentials come from HYEVE_ADMIN_USERNAME/HYEVE_ADMIN_PASSWORD env
    (0600 file sourced into the process env, never the repo)."""
    import time

    if _admin.get("token") and time.time() < _admin.get("exp", 0) - 60:
        return _admin["token"]
    user = os.environ.get("HYEVE_ADMIN_USERNAME", "")
    pw = os.environ.get("HYEVE_ADMIN_PASSWORD", "")
    if not user or not pw:
        raise HyeveAuthError("ADMIN_NOT_CONFIGURED:set HYEVE_ADMIN_USERNAME/_PASSWORD")
    try:
        with httpx.Client(timeout=15) as c:
            r = c.post(f"{auth_base()}/v1/auth/login", headers=_headers(),
                       json={"username": user, "password": pw})
    except httpx.TransportError as e:
        raise HyeveAuthError(f"HYEVE_AUTH_UNREACHABLE:{type(e).__name__}") from e
    if r.status_code != 200:
        raise HyeveAuthError(f"ADMIN_LOGIN_FAILED:{r.status_code}")
    body = r.json()
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    tok = data.get("access_token", "")
    if not tok:
        raise HyeveAuthError("ADMIN_LOGIN_NO_TOKEN")
    import base64

    try:
        claims = __import__("json").loads(base64.urlsafe_b64decode(
            tok.split(".")[1] + "=" * (-len(tok.split(".")[1]) % 4)))
        exp = float(claims.get("exp", 0))
    except Exception:
        exp = time.time() + 1800
    _admin.update(token=tok, exp=exp)
    return tok


def _admin_post(path: str, idem: str, payload: dict):
    tok = admin_token()
    try:
        with httpx.Client(timeout=15) as c:
            r = c.post(f"{auth_base()}{path}", headers=_headers(
                {"Authorization": f"Bearer {tok}", "Idempotency-Key": idem}), json=payload)
    except httpx.TransportError as e:
        raise HyeveAuthError(f"HYEVE_AUTH_UNREACHABLE:{type(e).__name__}") from e
    if r.status_code in (200, 201):
        body = r.json()
        return body.get("data") if isinstance(body.get("data"), dict) else body
    if r.status_code in (409, 422) and any(
            k in r.text.lower() for k in ("exists", "duplicate", "conflict", "already")):
        return {"already": True}
    raise HyeveAuthError(f"RBAC_FAILED:{path}:{r.status_code}:{r.text[:160]}")


def ensure_rouvr_rbac() -> dict:
    """Idempotent bootstrap: permission rouvr:use, role rouvr-user, attached.
    Safe to call on every register (server-side idempotency keys + 409s)."""
    _admin_post("/v1/auth/rbac/permissions", "rouvr-permission-use-v1",
                {"key": ROUVR_PERMISSION, "description": "Use the Rouvr application"})
    _admin_post("/v1/auth/rbac/roles", "rouvr-role-user-v1",
                {"name": ROUVR_ROLE, "description": "Standard Rouvr user"})
    _admin_post("/v1/auth/rbac/assign-permission", "rouvr-role-user-permission-v1",
                {"role_name": ROUVR_ROLE, "permission_key": ROUVR_PERMISSION})
    return {"permission": ROUVR_PERMISSION, "role": ROUVR_ROLE}


def assign_role(*, user_id: str, role_name: str = ROUVR_ROLE, idem: str | None = None) -> dict:
    return _admin_post("/v1/auth/rbac/assign-role", idem or f"rouvr-assign-{user_id}",
                       {"user_id": user_id, "role_name": role_name})


def me(token: str) -> dict:
    """GET /v1/auth/me with the caller's own token (username fallback when
    the JWT carries no username claim)."""
    if not enabled():
        raise HyeveAuthError("HYEVE_AUTH_NOT_CONFIGURED")
    try:
        with httpx.Client(timeout=15) as c:
            r = c.get(f"{auth_base()}/v1/auth/me",
                      headers=_headers({"Authorization": f"Bearer {token}"}))
    except httpx.TransportError as e:
        raise HyeveAuthError(f"HYEVE_AUTH_UNREACHABLE:{type(e).__name__}") from e
    if r.status_code != 200:
        raise HyeveAuthError(f"ME_FAILED:{r.status_code}")
    body = r.json()
    result = body.get("data") if isinstance(body.get("data"), dict) else body
    # Current Auth returns identity under user.username while older builds
    # exposed username at the top level. Normalize both shapes for callers.
    user = result.get("user") if isinstance(result, dict) else None
    if isinstance(user, dict) and user.get("username") and not result.get("username"):
        result = {**result, "username": user["username"]}
    return result


def login(*, username: str, password: str) -> dict:
    if not enabled():
        raise HyeveAuthError("HYEVE_AUTH_NOT_CONFIGURED")
    try:
        with httpx.Client(timeout=15) as c:
            r = c.post(f"{auth_base()}/v1/auth/login", headers=_headers(), json={
                "username": username, "password": password})
    except httpx.TransportError as e:
        raise HyeveAuthError(f"HYEVE_AUTH_UNREACHABLE:{type(e).__name__}") from e
    if r.status_code != 200:
        raise HyeveAuthError(f"LOGIN_FAILED:{r.status_code}")
    body = r.json()
    return body.get("data") if isinstance(body.get("data"), dict) else body
