"""EX2-S001: PostgreSQL schema for the AIX execution-kernel ledger.

Per ``AIX-PERSISTENCE-CONTRACTS.md`` §1 and the objective_cycle Option B
pattern: append-only, full-snapshot payloads, tenant-scoped, own table
(does not widen ``objective_cycle_events`` CHECK constraint).
"""

from __future__ import annotations

import logging

import asyncpg  # type: ignore[import-untyped]

from backend.infrastructure.postgres.db_schema_utils import create_index_tolerating_non_owner

logger = logging.getLogger(__name__)

EXECUTION_EVENTS_TABLE = "aix_execution_events"

AGGREGATE_TYPES = frozenset(
    {"project", "capability", "problem", "work_item", "decision", "artifact", "proposal"}
)

SEQUENCE_CONSTRAINT_NAME = "aix_execution_events_tenant_aggregate_seq_key"
COMMAND_CONSTRAINT_NAME = "aix_execution_events_tenant_command_key"


async def ensure_execution_kernel_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {EXECUTION_EVENTS_TABLE} (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            aggregate_type TEXT NOT NULL
                CHECK (aggregate_type IN (
                    'project', 'capability', 'problem', 'work_item',
                    'decision', 'artifact', 'proposal'
                )),
            aggregate_id TEXT NOT NULL,
            sequence_number INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            command_id TEXT NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT {SEQUENCE_CONSTRAINT_NAME}
                UNIQUE (tenant_id, aggregate_id, sequence_number),
            CONSTRAINT {COMMAND_CONSTRAINT_NAME}
                UNIQUE (tenant_id, command_id)
        )
        """
    )
    await create_index_tolerating_non_owner(
        conn,
        index_name=f"idx_{EXECUTION_EVENTS_TABLE}_tenant_aggregate",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{EXECUTION_EVENTS_TABLE}_tenant_aggregate "
            f"ON {EXECUTION_EVENTS_TABLE} (tenant_id, aggregate_type, aggregate_id, sequence_number)"
        ),
        logger=logger,
    )
    # NOTE: _ensure_tenant_isolation_policy below requires table ownership too
    # (ALTER TABLE ... FORCE ROW LEVEL SECURITY has the same Postgres
    # ownership requirement as CREATE INDEX) and is deliberately NOT given the
    # same tolerate-non-owner treatment here: RLS is a tenant-isolation
    # security control, not a performance nicety, so a runtime role that
    # can't enable it should keep failing loudly rather than silently running
    # tenant-isolation-free. If this trips in the same non-owner environment
    # that hit the CREATE INDEX bug, that's a real deployment/ops question
    # (should this role own its tables?) for a human, not something to paper
    # over here.
    await _ensure_tenant_isolation_policy(conn)


async def _ensure_tenant_isolation_policy(conn: asyncpg.Connection) -> None:
    """EX5-S8: DB-level tenant isolation, proving the gap EX3-S009 found
    live (a raw SQL query with no tenant_id filter could read another
    tenant's rows) is now actually closed for this table — not merely
    relying on every call site remembering to filter.

    `FORCE ROW LEVEL SECURITY` is essential: by default Postgres RLS
    policies do not apply to the table owner, and this service connects
    as the owning role — without FORCE, this would be a silent no-op.
    `current_setting(..., true)` returns NULL (never errors) when the
    session variable was never set, and `tenant_id = NULL` is never true
    — so a connection that forgets to set tenant context sees zero rows,
    fail-closed, matching this codebase's existing tenant discipline."""
    await conn.execute(f"ALTER TABLE {EXECUTION_EVENTS_TABLE} ENABLE ROW LEVEL SECURITY")
    await conn.execute(f"ALTER TABLE {EXECUTION_EVENTS_TABLE} FORCE ROW LEVEL SECURITY")
    await conn.execute(f"DROP POLICY IF EXISTS {EXECUTION_EVENTS_TABLE}_tenant_isolation ON {EXECUTION_EVENTS_TABLE}")
    await conn.execute(
        f"""
        CREATE POLICY {EXECUTION_EVENTS_TABLE}_tenant_isolation ON {EXECUTION_EVENTS_TABLE}
        USING (tenant_id = current_setting('app.current_tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))
        """
    )


__all__ = [
    "EXECUTION_EVENTS_TABLE",
    "AGGREGATE_TYPES",
    "SEQUENCE_CONSTRAINT_NAME",
    "COMMAND_CONSTRAINT_NAME",
    "ensure_execution_kernel_schema",
]
