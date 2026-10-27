"""Reset local admin password, login, and refresh WORKHUB_API_TOKEN in AIX .env.

Local-dev helper only. Does not print the token.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import sys
import time
from pathlib import Path

import asyncpg
import httpx

AUTH_ENV = Path(r"D:\v2.1\hyeve\services\hyeve-auth-service\.env")
AIX_ENV = Path(r"D:\v2.1\AIX\hyeve-aix-service\.env")
AUTH_ROOT = Path(r"D:\v2.1\hyeve\services\hyeve-auth-service")
LOGIN_URL = "http://127.0.0.1:8222/v1/auth/login"
WORKHUB_CREATE_URL = "http://127.0.0.1:8226/api/v1/tasks"
DEFAULT_PROJECT_ID = "01KYCPDEPJ9STR36FB8P8DT31F"
USERNAME = "ydaiya"
PASSWORD = "Simple@123"
TENANT_CODE = "ydaiya"


def _load_auth_db_url() -> str:
    for line in AUTH_ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL=") or line.startswith("HYEVE_AUTH_SERVICE_DATABASE_URL="):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")
            if url.startswith("postgresql+asyncpg://"):
                return "postgresql://" + url.split("://", 1)[1]
            return url
    raise SystemExit("auth DATABASE_URL missing")


def _jwt_payload(token: str) -> dict:
    parts = token.split(".")
    pad = "=" * ((4 - len(parts[1]) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(parts[1] + pad))


def _load_dotenv(path: Path, *, override: bool = False) -> None:
    import os

    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if override or key not in os.environ or not str(os.environ.get(key) or "").strip():
            os.environ[key] = value


async def _reset_password() -> str:
    _load_dotenv(AUTH_ENV, override=True)
    sys.path.insert(0, str(AUTH_ROOT))
    from app.password import hash_password  # noqa: WPS433

    conn = await asyncpg.connect(_load_auth_db_url())
    try:
        user = await conn.fetchrow(
            """
            SELECT user_account_id, tenant_id, username
            FROM user_account
            WHERE deleted_at IS NULL AND lower(username) = lower($1)
            ORDER BY updated_at DESC NULLS LAST
            LIMIT 1
            """,
            USERNAME,
        )
        if user is None:
            raise SystemExit(f"user {USERNAME!r} not found")
        password_hash = hash_password(PASSWORD)
        algo = "argon2" if password_hash.startswith("$argon2") else "bcrypt"
        from ulid import ULID

        updated = await conn.execute(
            """
            UPDATE password_credential
            SET password_hash = $1,
                hash_algorithm = $2,
                password_changed_at = clock_timestamp(),
                updated_at = clock_timestamp()
            WHERE user_account_id = $3
            """,
            password_hash,
            algo,
            user["user_account_id"],
        )
        if updated.endswith("0"):
            await conn.execute(
                """
                INSERT INTO password_credential (
                    password_credential_id, tenant_id, user_account_id,
                    password_hash, hash_algorithm, password_changed_at
                ) VALUES ($1, $2, $3, $4, $5, clock_timestamp())
                """,
                str(ULID()),
                user["tenant_id"],
                user["user_account_id"],
                password_hash,
                algo,
            )
        print(f"password_reset_ok user={user['username']} tenant={user['tenant_id']}")
        return str(user["tenant_id"])
    finally:
        await conn.close()


async def _login(tenant_id: str) -> str:
    headers = {
        "Content-Type": "application/json",
        "X-Hyeve-Tenant-Id": TENANT_CODE,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            LOGIN_URL,
            headers=headers,
            json={"username": USERNAME, "password": PASSWORD},
        )
        if response.status_code >= 400:
            # retry with ULID tenant
            headers["X-Hyeve-Tenant-Id"] = tenant_id
            response = await client.post(
                LOGIN_URL,
                headers=headers,
                json={"username": USERNAME, "password": PASSWORD},
            )
        if response.status_code >= 400:
            raise SystemExit(f"login_failed {response.status_code} {response.text[:300]}")
        body = response.json()
        token = (
            body.get("access_token")
            or body.get("accessToken")
            or (body.get("data") or {}).get("access_token")
        )
        if not token:
            raise SystemExit(f"login_ok_but_no_token keys={list(body.keys())}")
        payload = _jwt_payload(token)
        hours = round((int(payload.get("exp") or 0) - time.time()) / 3600, 2)
        print(f"login_ok roles={payload.get('roles')} hours_left={hours}")
        return str(token)


async def _probe_create(token: str) -> None:
    payload = _jwt_payload(token)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Hyeve-Tenant-Id": str(payload.get("tenant_id") or ""),
        "X-Project-ID": DEFAULT_PROJECT_ID,
        "Idempotency-Key": f"aix-token-refresh-{int(time.time())}",
    }
    body = {
        "project_id": DEFAULT_PROJECT_ID,
        "title": "AIX token refresh probe",
        "description": "auto probe after token refresh",
        "priority": "medium",
        "status": "queued",
        "verifier_required": True,
        "evidence_required": True,
        "allow_weak_closure": False,
        "metadata_json": {"source": "refresh_workhub_api_token"},
        "tags_json": ["probe"],
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(WORKHUB_CREATE_URL, headers=headers, json=body)
        print(f"workhub_create_status={response.status_code}")
        if response.status_code >= 400:
            print(f"workhub_create_body={response.text[:300]}")


def _write_env_token(token: str) -> None:
    text = AIX_ENV.read_text(encoding="utf-8")
    pattern = re.compile(r"^HYEVE_AIX_SERVICE_WORKHUB_API_TOKEN=.*$", re.MULTILINE)
    replacement = f"HYEVE_AIX_SERVICE_WORKHUB_API_TOKEN={token}"
    if pattern.search(text):
        text = pattern.sub(replacement, text, count=1)
    else:
        text = text.rstrip() + "\n" + replacement + "\n"
    AIX_ENV.write_text(text, encoding="utf-8")
    print("env_updated HYEVE_AIX_SERVICE_WORKHUB_API_TOKEN")


async def main() -> None:
    tenant_id = await _reset_password()
    token = await _login(tenant_id)
    await _probe_create(token)
    _write_env_token(token)
    print("restart_aix_required")


if __name__ == "__main__":
    asyncio.run(main())
