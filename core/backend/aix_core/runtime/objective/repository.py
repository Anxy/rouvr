"""AIX-E2E-CYCLE-001B-W03/W04/W05: repository, unit-of-work, optimistic concurrency,
and idempotency for the objective-cycle ledger.

Design note: unlike `RemotePostgresDalGateway` (which wraps its async implementation in
a synchronous facade to match the shared `DalGateway` Protocol), this repository is
natively async. It deliberately does not implement `DalGateway` — see
`docs/current/AIX-OBJECTIVE-CYCLE-PERSISTENCE-DECISION.md` — so it is free to match this
codebase's other async idiom (`hyeve_aix_service/database.py`'s `AsyncSession`) instead.

No production/VPS mutation is performed by importing this module: schema is only touched
when `ensure_schema=True` is passed explicitly, and no connection is opened at import
time or class-construction time.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

import asyncpg  # type: ignore[import-untyped]

from backend.aix_core.runtime.objective.ledger_schema import (
    COMMAND_CONSTRAINT_NAME,
    OBJECTIVE_CYCLE_EVENTS_TABLE,
    SEQUENCE_CONSTRAINT_NAME,
    ensure_objective_cycle_schema,
)

TEST_DATABASE_URL_ENV_KEYS = (
    "AIX_OBJECTIVE_CYCLE_TEST_DATABASE_URL",
    "AIX_LEDGER_DAL_DATABASE_URL",
)


class ObjectiveCycleRepositoryError(RuntimeError):
    """Base error for repository operations. Never silently swallowed."""


class ConcurrencyConflictError(ObjectiveCycleRepositoryError):
    """Raised when `expected_sequence_number` was not the true next sequence number for
    this (tenant_id, aggregate_id) — someone else already appended at that slot."""

    def __init__(self, tenant_id: str, aggregate_id: str, expected_sequence_number: int) -> None:
        super().__init__(
            f"concurrency conflict for tenant={tenant_id!r} aggregate_id={aggregate_id!r}: "
            f"sequence_number={expected_sequence_number} was already taken"
        )
        self.tenant_id = tenant_id
        self.aggregate_id = aggregate_id
        self.expected_sequence_number = expected_sequence_number


@dataclass(frozen=True)
class ObjectiveCycleEvent:
    id: str
    tenant_id: str
    aggregate_type: str
    aggregate_id: str
    sequence_number: int
    event_type: str
    command_id: str
    payload: dict[str, Any]
    created_at: str
    replayed: bool = False
    """True when this event was returned because `command_id` already existed
    (idempotent replay), not because a new row was inserted."""


def resolve_test_database_url() -> str | None:
    """Return a configured test database URL, or None if none is set. Integration tests
    that need a real Postgres must call this and skip (not fabricate a connection) when
    it returns None — see test_repository.py."""
    for key in TEST_DATABASE_URL_ENV_KEYS:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return None


def _row_to_event(row: asyncpg.Record, *, replayed: bool = False) -> ObjectiveCycleEvent:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return ObjectiveCycleEvent(
        id=row["id"],
        tenant_id=row["tenant_id"],
        aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"],
        sequence_number=row["sequence_number"],
        event_type=row["event_type"],
        command_id=row["command_id"],
        payload=payload,
        created_at=row["created_at"].isoformat(),
        replayed=replayed,
    )


class ObjectiveCycleRepository:
    """Append-only access to the objective-cycle ledger. One instance per database URL;
    safe to share across requests (each call opens its own short-lived connection)."""

    def __init__(self, database_url: str, *, ensure_schema: bool = False, command_timeout_seconds: float = 5.0) -> None:
        if not database_url:
            raise ObjectiveCycleRepositoryError("database_url is required")
        self._database_url = database_url
        self._ensure_schema = ensure_schema
        self._command_timeout_seconds = command_timeout_seconds

    async def _connect(self) -> asyncpg.Connection:
        try:
            return await asyncpg.connect(
                self._database_url,
                command_timeout=self._command_timeout_seconds,
                timeout=self._command_timeout_seconds,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ObjectiveCycleRepositoryError("objective-cycle ledger connection failed") from exc

    async def append_event(
        self,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        expected_sequence_number: int,
        event_type: str,
        command_id: str,
        payload: dict[str, Any],
    ) -> ObjectiveCycleEvent:
        """Append one versioned snapshot event. Raises `ConcurrencyConflictError` if
        `expected_sequence_number` was already taken for this aggregate. Returns the
        existing event (with `replayed=True`) instead of raising if `command_id` was
        already used — this is the idempotency path (001B-W05), not an error path."""
        conn = await self._connect()
        try:
            return await self._append_within(conn, ensure_schema=self._ensure_schema, tenant_id=tenant_id,
                aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                expected_sequence_number=expected_sequence_number, event_type=event_type,
                command_id=command_id, payload=payload)
        except (ConcurrencyConflictError, ObjectiveCycleRepositoryError):
            raise
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ObjectiveCycleRepositoryError(
                f"objective-cycle ledger append failed: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            await conn.close()

    @staticmethod
    async def _append_within(
        conn: asyncpg.Connection,
        *,
        ensure_schema: bool,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        expected_sequence_number: int,
        event_type: str,
        command_id: str,
        payload: dict[str, Any],
    ) -> ObjectiveCycleEvent:
        if ensure_schema:
            await ensure_objective_cycle_schema(conn)
            from backend.aix_core.runtime.objective.durable_aggregate import ensure_durable_aggregate_schema
            from backend.aix_core.framework.scale.schema import ensure_aix_work_schema

            await ensure_durable_aggregate_schema(conn)
            await ensure_aix_work_schema(conn)
        event_id = new_ulid()
        try:
            # UniqueViolation aborts the Postgres transaction. Replay/conflict
            # lookups must run after this block so the INSERT transaction has
            # rolled back — querying inside the same aborted transaction raises
            # InFailedSQLTransactionError ("ledger append failed") on auto-repair
            # retries that reuse command_id.
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO {OBJECTIVE_CYCLE_EVENTS_TABLE}
                        (id, tenant_id, aggregate_type, aggregate_id, sequence_number,
                         event_type, command_id, payload)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                    RETURNING *
                    """,
                    event_id,
                    tenant_id,
                    aggregate_type,
                    aggregate_id,
                    expected_sequence_number,
                    event_type,
                    command_id,
                    json.dumps(payload, sort_keys=True),
                )
            return _row_to_event(row)
        except asyncpg.UniqueViolationError as exc:
            constraint = getattr(exc, "constraint_name", None) or ""
            detail = f"{constraint} {exc}".lower()
            if constraint == COMMAND_CONSTRAINT_NAME or COMMAND_CONSTRAINT_NAME in detail:
                existing = await conn.fetchrow(
                    f"SELECT * FROM {OBJECTIVE_CYCLE_EVENTS_TABLE} WHERE tenant_id = $1 AND command_id = $2",
                    tenant_id,
                    command_id,
                )
                if existing is None:
                    raise ObjectiveCycleRepositoryError(
                        "duplicate command_id reported but existing row not found"
                    ) from exc
                return _row_to_event(existing, replayed=True)
            if constraint == SEQUENCE_CONSTRAINT_NAME or SEQUENCE_CONSTRAINT_NAME in detail:
                raise ConcurrencyConflictError(tenant_id, aggregate_id, expected_sequence_number) from exc
            raise ObjectiveCycleRepositoryError(
                f"unexpected unique violation on constraint {constraint!r}"
            ) from exc

    async def load_events(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> list[ObjectiveCycleEvent]:
        """Ordered (ascending sequence_number) full event history for one aggregate.
        Used both for audit/replay and for crash reconstruction (001B happy-path
        acceptance criterion): the caller reconstructs current state either by folding
        this list, or trivially by taking the last element's payload."""
        conn = await self._connect()
        try:
            if self._ensure_schema:
                await ensure_objective_cycle_schema(conn)
            rows = await conn.fetch(
                f"""
                SELECT * FROM {OBJECTIVE_CYCLE_EVENTS_TABLE}
                WHERE tenant_id = $1 AND aggregate_type = $2 AND aggregate_id = $3
                ORDER BY sequence_number ASC
                """,
                tenant_id,
                aggregate_type,
                aggregate_id,
            )
            return [_row_to_event(row) for row in rows]
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ObjectiveCycleRepositoryError("objective-cycle ledger read failed") from exc
        finally:
            await conn.close()

    async def latest_snapshot(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> ObjectiveCycleEvent | None:
        """The highest-sequence_number event for this aggregate, i.e. its current
        reconstructed state (each payload is a full snapshot, not a delta — see
        ledger_schema.py). None if the aggregate has no events."""
        events = await self.load_events(
            tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id
        )
        return events[-1] if events else None

    async def upsert_aggregate_snapshot(
        self,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        sequence_number: int,
        schema_version: str,
        snapshot: dict[str, Any],
    ) -> None:
        """C011: persist a decision-state projection row (idempotent per sequence)."""
        from backend.aix_core.runtime.objective.durable_aggregate import (
                TABLE_SNAPSHOT,
                ensure_durable_aggregate_schema,
            )

        conn = await self._connect()
        try:
            if self._ensure_schema:
                await ensure_objective_cycle_schema(conn)
                await ensure_durable_aggregate_schema(conn)
            # Normalize cycle→run for the projection PK space.
            agg = "run" if aggregate_type in {"cycle", "run"} else aggregate_type
            await conn.execute(
                f"""
                INSERT INTO {TABLE_SNAPSHOT}
                    (tenant_id, aggregate_type, aggregate_id, sequence_number,
                     schema_version, snapshot)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                ON CONFLICT (tenant_id, aggregate_type, aggregate_id, sequence_number)
                DO UPDATE SET
                    schema_version = EXCLUDED.schema_version,
                    snapshot = EXCLUDED.snapshot,
                    created_at = now()
                """,
                tenant_id,
                agg,
                aggregate_id,
                int(sequence_number),
                schema_version,
                json.dumps(snapshot, sort_keys=True),
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ObjectiveCycleRepositoryError(
                f"aggregate snapshot upsert failed: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            await conn.close()

    async def load_latest_aggregate_snapshot(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> dict[str, Any] | None:
        """C011: highest-sequence decision snapshot from aix_aggregate_snapshots."""
        from backend.aix_core.runtime.objective.durable_aggregate import (
                TABLE_SNAPSHOT,
                ensure_durable_aggregate_schema,
            )

        conn = await self._connect()
        try:
            if self._ensure_schema:
                await ensure_objective_cycle_schema(conn)
                await ensure_durable_aggregate_schema(conn)
            agg = "run" if aggregate_type in {"cycle", "run"} else aggregate_type
            row = await conn.fetchrow(
                f"""
                SELECT tenant_id, aggregate_type, aggregate_id, sequence_number,
                       schema_version, snapshot
                FROM {TABLE_SNAPSHOT}
                WHERE tenant_id = $1 AND aggregate_type = $2 AND aggregate_id = $3
                ORDER BY sequence_number DESC
                LIMIT 1
                """,
                tenant_id,
                agg,
                aggregate_id,
            )
            if row is None:
                return None
            payload = row["snapshot"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            return {
                "tenant_id": row["tenant_id"],
                "aggregate_type": row["aggregate_type"],
                "aggregate_id": row["aggregate_id"],
                "sequence_number": int(row["sequence_number"]),
                "schema_version": row["schema_version"],
                "snapshot": payload,
            }
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ObjectiveCycleRepositoryError(
                f"aggregate snapshot read failed: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            await conn.close()


class ObjectiveCycleUnitOfWork:
    """Atomic multi-event append within one transaction — e.g. a step-attempt-completed
    event and the cycle's cursor-advanced event must land together or not at all."""

    def __init__(self, database_url: str, *, ensure_schema: bool = False) -> None:
        self._database_url = database_url
        self._ensure_schema = ensure_schema
        self._conn: asyncpg.Connection | None = None
        self._transaction: Any = None

    async def __aenter__(self) -> "ObjectiveCycleUnitOfWork":
        self._conn = await asyncpg.connect(self._database_url)
        self._transaction = self._conn.transaction()
        await self._transaction.start()
        if self._ensure_schema:
            await ensure_objective_cycle_schema(self._conn)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        assert self._conn is not None and self._transaction is not None
        try:
            if exc_type is None:
                await self._transaction.commit()
            else:
                await self._transaction.rollback()
        finally:
            await self._conn.close()

    async def append(
        self,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        expected_sequence_number: int,
        event_type: str,
        command_id: str,
        payload: dict[str, Any],
    ) -> ObjectiveCycleEvent:
        assert self._conn is not None, "append() must be called inside 'async with ObjectiveCycleUnitOfWork(...)'"
        event_id = new_ulid()
        try:
            row = await self._conn.fetchrow(
                f"""
                INSERT INTO {OBJECTIVE_CYCLE_EVENTS_TABLE}
                    (id, tenant_id, aggregate_type, aggregate_id, sequence_number,
                     event_type, command_id, payload)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                RETURNING *
                """,
                event_id,
                tenant_id,
                aggregate_type,
                aggregate_id,
                expected_sequence_number,
                event_type,
                command_id,
                json.dumps(payload, sort_keys=True),
            )
        except asyncpg.UniqueViolationError as exc:
            constraint = getattr(exc, "constraint_name", None)
            if constraint == SEQUENCE_CONSTRAINT_NAME:
                raise ConcurrencyConflictError(tenant_id, aggregate_id, expected_sequence_number) from exc
            if constraint == COMMAND_CONSTRAINT_NAME:
                raise ObjectiveCycleRepositoryError(
                    "duplicate command_id inside a multi-event unit of work is not "
                    "auto-resolved as a replay — the caller must not retry a whole "
                    "unit of work with a reused command_id"
                ) from exc
            raise ObjectiveCycleRepositoryError(f"unexpected unique violation on constraint {constraint!r}") from exc
        return _row_to_event(row)


__all__ = [
    "TEST_DATABASE_URL_ENV_KEYS",
    "ObjectiveCycleRepositoryError",
    "ConcurrencyConflictError",
    "ObjectiveCycleEvent",
    "ObjectiveCycleRepository",
    "ObjectiveCycleUnitOfWork",
    "resolve_test_database_url",
]
