"""List local auth users (no secrets). Temp helper."""

from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg


def _db_url() -> str:
    env = Path(r"D:\v2.1\hyeve\services\hyeve-auth-service\.env")
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL=") or line.startswith("HYEVE_AUTH_SERVICE_DATABASE_URL="):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")
            if url.startswith("postgresql+asyncpg://"):
                return "postgresql://" + url.split("://", 1)[1]
            return url
    raise SystemExit("DATABASE_URL missing in auth .env")


async def main() -> None:
    conn = await asyncpg.connect(_db_url())
    try:
        rows = await conn.fetch(
            """
            SELECT tenant_id, username, status
            FROM user_account
            WHERE deleted_at IS NULL
            ORDER BY updated_at DESC NULLS LAST
            LIMIT 30
            """
        )
        for row in rows:
            print(row["tenant_id"], row["username"], row["status"])
        print("---tenants---")
        tenants = await conn.fetch(
            "SELECT tenant_id, tenant_code, status FROM tenant ORDER BY created_at DESC NULLS LAST LIMIT 20"
        )
        for tenant in tenants:
            print(tenant["tenant_id"], tenant["tenant_code"], tenant["status"])
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
