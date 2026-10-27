"""Check objective/cycle status for a given id."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import asyncpg

OBJ = "obj_200cf109fff5"
ENV = Path(r"D:\v2.1\AIX\hyeve-aix-service\.env")


def db_url() -> str:
    url = None
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("AIX_OBJECTIVE_CYCLE_TEST_DATABASE_URL="):
            url = line.split("=", 1)[1].strip()
            break
    if not url:
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith("HYEVE_AIX_SERVICE_DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
                break
    assert url
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql://" + url.split("://", 1)[1]
    return url


async def main() -> None:
    conn = await asyncpg.connect(db_url(), timeout=5.0, ssl=False)
    try:
        tables = await conn.fetch(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public'
              AND (table_name ILIKE '%cycle%' OR table_name ILIKE '%objective%' OR table_name ILIKE 'aix_%')
            ORDER BY 1
            """
        )
        print("tables", [t["table_name"] for t in tables])
        for t in tables:
            name = t["table_name"]
            cols = await conn.fetch(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name=$1 ORDER BY ordinal_position
                """,
                name,
            )
            colnames = [c["column_name"] for c in cols]
            search_cols = [
                c
                for c in colnames
                if c
                in {
                    "aggregate_id",
                    "objective_id",
                    "cycle_id",
                    "thread_id",
                    "id",
                    "payload",
                    "payload_json",
                    "event_type",
                }
            ]
            if not search_cols:
                continue
            clause = " OR ".join(f"CAST({c} AS text) ILIKE $1" for c in search_cols)
            rows = await conn.fetch(f"SELECT * FROM {name} WHERE {clause} LIMIT 10", f"%{OBJ}%")
            if not rows:
                # also search recent events
                if "created_at" in colnames or "recorded_at" in colnames:
                    order = "created_at" if "created_at" in colnames else "recorded_at"
                    recent = await conn.fetch(f"SELECT * FROM {name} ORDER BY {order} DESC LIMIT 5")
                    print(f"{name}: no hit for {OBJ}; recent={len(recent)}")
                    for r in recent:
                        d = {k: (str(v)[:100] if v is not None else None) for k, v in dict(r).items()}
                        print(" ", json.dumps(d)[:300])
                continue
            print(f"{name}: {len(rows)} hit(s)")
            for r in rows:
                d = {k: (str(v)[:200] if v is not None else None) for k, v in dict(r).items()}
                print(json.dumps(d, indent=2)[:1200])
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
