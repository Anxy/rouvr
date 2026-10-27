"""AIX-E2E-CYCLE-001B-W02: PostgreSQL schema for the objective-cycle ledger.

Per `docs/current/AIX-OBJECTIVE-CYCLE-PERSISTENCE-DECISION.md`: append-only, versioned-
snapshot ledger, independent of the shared `DalGateway`/`RemotePostgresDalGateway`
(insert-only, six unrelated tables). Raw `asyncpg` DDL, idempotent
`CREATE TABLE/INDEX IF NOT EXISTS`, matching the hand-written schema-ensure convention
already used in `aix_adapters/live_phase23/remote_dal.py` — this is its own table, not
routed through that module's `SUPPORTED_TABLES` dispatch.

Each row's `payload` is the *full current snapshot* of the aggregate at that sequence
number, not a delta against the previous row. This is a deliberate simplification over
strict delta-based event sourcing: it keeps folding trivial (current state = payload of
the highest `sequence_number` for that aggregate) while still giving full history,
optimistic concurrency, and idempotency. `event_type` is a label for audit/filtering
(e.g. `"CYCLE_STATUS_CHANGED"`), not a delta-replay instruction.

Two named unique constraints do the safety-critical work and must keep these exact names
— `repository.py` inspects `asyncpg.UniqueViolationError.constraint_name` to tell a
concurrency conflict apart from a duplicate command:

- `objective_cycle_events_tenant_aggregate_seq_key` — optimistic concurrency.
- `objective_cycle_events_tenant_command_key` — idempotency.
"""

from __future__ import annotations

import logging

import asyncpg  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

OBJECTIVE_CYCLE_EVENTS_TABLE = "objective_cycle_events"

AGGREGATE_TYPES = frozenset({"objective", "cycle", "step", "attempt", "replan", "outcome"})

SEQUENCE_CONSTRAINT_NAME = "objective_cycle_events_tenant_aggregate_seq_key"
COMMAND_CONSTRAINT_NAME = "objective_cycle_events_tenant_command_key"


async def ensure_objective_cycle_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {OBJECTIVE_CYCLE_EVENTS_TABLE} (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            aggregate_type TEXT NOT NULL
                CHECK (aggregate_type IN ('objective', 'cycle', 'step', 'attempt', 'replan', 'outcome')),
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
    try:
        await conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{OBJECTIVE_CYCLE_EVENTS_TABLE}_tenant_aggregate
            ON {OBJECTIVE_CYCLE_EVENTS_TABLE} (tenant_id, aggregate_type, aggregate_id, sequence_number)
            """
        )
    except asyncpg.InsufficientPrivilegeError:
        # Unlike CREATE TABLE IF NOT EXISTS above, CREATE INDEX has no separate
        # "index" privilege in Postgres -- it always requires owning the target
        # table, even with IF NOT EXISTS, even to no-op past an index that
        # already exists. A runtime role that only has DML rights on a table
        # created by a different (migration/admin) role will hit this on every
        # single call, since `repository.py`'s `_ensure_schema` re-runs this
        # function on every read/write rather than once. Live proof: with the
        # table already present but this specific index missing, this raised
        # InsufficientPrivilegeError on every objective admit, which surfaced
        # as a hard 500 all the way up through _persist_event -- i.e. it was
        # blocking all new objective-cycle writes, not just missing an
        # optimization. The table (created above, or by an earlier migration)
        # is what read/write correctness actually depends on; the index is a
        # query-speed nicety that a non-owner role simply cannot provision, so
        # this must not fail the caller.
        logger.warning(
            "ensure_objective_cycle_schema: skipping idx_%s_tenant_aggregate "
            "(runtime role is not the table owner, cannot CREATE INDEX)",
            OBJECTIVE_CYCLE_EVENTS_TABLE,
        )

    # F12.4 — durable gaps table (AC-25)
    from backend.aix_core.runtime.verification.gap import ensure_gaps_schema

    try:
        await ensure_gaps_schema(conn)
    except Exception:
        logger.exception("ensure_objective_cycle_schema: gaps table ensure failed")


__all__ = [
    "OBJECTIVE_CYCLE_EVENTS_TABLE",
    "AGGREGATE_TYPES",
    "SEQUENCE_CONSTRAINT_NAME",
    "COMMAND_CONSTRAINT_NAME",
    "ensure_objective_cycle_schema",
]
