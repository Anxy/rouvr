"""F11-P1: aix_work_* projection schema (Postgres DDL + ensure).

Dual-model: objective_cycle_events remain SoR; these tables are indexed projections.
"""

from __future__ import annotations

from typing import Any

TABLE_WORK_GRAPHS = "aix_work_graphs"
TABLE_WORK_UNITS = "aix_work_units"
TABLE_WORK_DEPS = "aix_work_unit_dependencies"
TABLE_WORK_LOCKS = "aix_work_locks"

ALL_WORK_TABLES: tuple[str, ...] = (
    TABLE_WORK_GRAPHS,
    TABLE_WORK_UNITS,
    TABLE_WORK_DEPS,
    TABLE_WORK_LOCKS,
)

DDL_STATEMENTS: tuple[str, ...] = (
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_WORK_GRAPHS} (
        graph_id TEXT PRIMARY KEY,
        work_id TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'ACTIVE',
        total_units INTEGER NOT NULL DEFAULT 0,
        completed_units INTEGER NOT NULL DEFAULT 0,
        failed_units INTEGER NOT NULL DEFAULT 0,
        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_WORK_UNITS} (
        unit_id TEXT NOT NULL,
        graph_id TEXT NOT NULL REFERENCES {TABLE_WORK_GRAPHS}(graph_id) ON DELETE CASCADE,
        work_id TEXT NOT NULL,
        unit_index INTEGER NOT NULL,
        purpose TEXT NOT NULL,
        target_scope JSONB NOT NULL DEFAULT '[]'::jsonb,
        contract JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        actions JSONB NOT NULL DEFAULT '[]'::jsonb,
        status TEXT NOT NULL DEFAULT 'PENDING',
        assigned_role TEXT,
        assigned_lane_id TEXT,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (graph_id, unit_id)
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_WORK_DEPS} (
        graph_id TEXT NOT NULL,
        child_unit_id TEXT NOT NULL,
        parent_unit_id TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (graph_id, child_unit_id, parent_unit_id)
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_WORK_LOCKS} (
        file_path TEXT PRIMARY KEY,
        graph_id TEXT NOT NULL,
        locked_by_unit_id TEXT NOT NULL,
        locked_by_lane_id TEXT NOT NULL,
        acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL
    )
    """.strip(),
)

SQLITE_DDL: tuple[str, ...] = (
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_WORK_GRAPHS} (
        graph_id TEXT PRIMARY KEY,
        work_id TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'ACTIVE',
        total_units INTEGER NOT NULL DEFAULT 0,
        completed_units INTEGER NOT NULL DEFAULT 0,
        failed_units INTEGER NOT NULL DEFAULT 0,
        payload TEXT NOT NULL DEFAULT '{{}}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_WORK_UNITS} (
        unit_id TEXT NOT NULL,
        graph_id TEXT NOT NULL,
        work_id TEXT NOT NULL,
        unit_index INTEGER NOT NULL,
        purpose TEXT NOT NULL,
        target_scope TEXT NOT NULL DEFAULT '[]',
        contract TEXT NOT NULL DEFAULT '{{}}',
        actions TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'PENDING',
        assigned_role TEXT,
        assigned_lane_id TEXT,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (graph_id, unit_id),
        FOREIGN KEY (graph_id) REFERENCES {TABLE_WORK_GRAPHS}(graph_id) ON DELETE CASCADE
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_WORK_DEPS} (
        graph_id TEXT NOT NULL,
        child_unit_id TEXT NOT NULL,
        parent_unit_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (graph_id, child_unit_id, parent_unit_id)
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_WORK_LOCKS} (
        file_path TEXT PRIMARY KEY,
        graph_id TEXT NOT NULL,
        locked_by_unit_id TEXT NOT NULL,
        locked_by_lane_id TEXT NOT NULL,
        acquired_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS aix_work_hibernation (
        cycle_id TEXT PRIMARY KEY,
        graph_id TEXT NOT NULL,
        work_id TEXT NOT NULL,
        status TEXT NOT NULL,
        record_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """.strip(),
)


async def ensure_aix_work_schema(conn: Any) -> None:
    """Idempotent Postgres DDL for F11 work-graph projections."""
    for statement in DDL_STATEMENTS:
        await conn.execute(statement)
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE_WORK_GRAPHS}_work_id "
        f"ON {TABLE_WORK_GRAPHS}(work_id)"
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE_WORK_UNITS}_ready "
        f"ON {TABLE_WORK_UNITS}(graph_id, status)"
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE_WORK_DEPS}_parent "
        f"ON {TABLE_WORK_DEPS}(parent_unit_id)"
    )


def ensure_sqlite_work_schema(conn: Any) -> None:
    """Idempotent SQLite DDL (dev/test durable adapter + restart proof)."""
    for statement in SQLITE_DDL:
        conn.execute(statement)
    conn.commit()


__all__ = [
    "ALL_WORK_TABLES",
    "DDL_STATEMENTS",
    "SQLITE_DDL",
    "TABLE_WORK_DEPS",
    "TABLE_WORK_GRAPHS",
    "TABLE_WORK_LOCKS",
    "TABLE_WORK_UNITS",
    "ensure_aix_work_schema",
    "ensure_sqlite_work_schema",
]
