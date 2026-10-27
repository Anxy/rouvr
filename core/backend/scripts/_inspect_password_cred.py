import asyncio
from pathlib import Path

import asyncpg


async def main() -> None:
    env = Path(r"D:\v2.1\hyeve\services\hyeve-auth-service\.env")
    url = None
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL=") or line.startswith("HYEVE_AUTH_SERVICE_DATABASE_URL="):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
    assert url
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql://" + url.split("://", 1)[1]
    conn = await asyncpg.connect(url)
    try:
        cols = await conn.fetch(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name='password_credential'
            ORDER BY ordinal_position
            """
        )
        for col in cols:
            print(col["column_name"], col["data_type"])
        row = await conn.fetchrow(
            "SELECT * FROM password_credential WHERE deleted_at IS NULL LIMIT 1"
        )
        print("sample_keys", list(row.keys()) if row else None)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
