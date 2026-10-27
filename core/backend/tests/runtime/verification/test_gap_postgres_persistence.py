"""AIX Feature 12 (Phase B) — Unit tests for Postgres GapStore persistence."""

import json
from unittest.mock import AsyncMock
import pytest

from backend.aix_core.runtime.verification.gap import (
    GapStore,
    GapV1,
    ensure_gaps_schema,
)


@pytest.mark.asyncio
async def test_gap_postgres_save_and_load():
    mock_conn = AsyncMock()
    
    # Setup mock storage table
    stored_rows: list[dict] = []

    async def mock_execute(query: str, *args):
        if "INSERT INTO gaps" in query:
            row = {
                "id": args[0],
                "objective_id": args[1],
                "verification_id": args[2],
                "status": args[3],
                "category": args[4],
                "expected": args[5],
                "observed": args[6],
                "evidence": args[7],
                "severity": args[8],
                "ov_id": args[9],
                "repair_attempt_count": args[10],
                "payload": args[11],
                "created_at": args[12],
                "updated_at": args[13],
            }
            # Handle conflict
            for idx, existing in enumerate(stored_rows):
                if existing["id"] == row["id"]:
                    stored_rows[idx] = row
                    return
            stored_rows.append(row)
        return

    async def mock_fetch(query: str, *args):
        if "SELECT payload FROM gaps" in query:
            obj_id = args[0]
            return [{"payload": r["payload"]} for r in stored_rows if r["objective_id"] == obj_id]
        return []

    mock_conn.execute.side_effect = mock_execute
    mock_conn.fetch.side_effect = mock_fetch

    store = GapStore(pg_conn=mock_conn)

    gap = GapV1(
        gap_id="gap-pg-1",
        objective_id="obj_pg_101",
        verification_id="v_pg_101",
        ov_id="OV-002",
        category="OBJECTIVE_DEVIATION",
        expected="SQLite persistence",
        observed="PostgreSQL psycopg",
        severity="BLOCKER",
        status="OPEN",
    )

    # Save to PG
    await store.save_pg(gap)
    assert len(stored_rows) == 1

    # Load from PG
    store_fresh = GapStore(pg_conn=mock_conn)
    loaded = await store_fresh.load_pg("obj_pg_101")
    assert len(loaded) == 1
    assert loaded[0].gap_id == "gap-pg-1"
    assert loaded[0].status == "OPEN"
    assert loaded[0].category == "OBJECTIVE_DEVIATION"
