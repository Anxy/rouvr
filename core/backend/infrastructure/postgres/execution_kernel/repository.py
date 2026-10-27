"""EX2-S001: repository for the execution-kernel ledger (Postgres + in-memory)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]

from backend.infrastructure.postgres.execution_kernel.db_pool import acquire_pooled_connection
from backend.infrastructure.postgres.execution_kernel.ledger_schema import (
    AGGREGATE_TYPES,
    COMMAND_CONSTRAINT_NAME,
    EXECUTION_EVENTS_TABLE,
    SEQUENCE_CONSTRAINT_NAME,
    ensure_execution_kernel_schema,
)

TEST_DATABASE_URL_ENV_KEYS = (
    "AIX_EXECUTION_KERNEL_TEST_DATABASE_URL",
    "AIX_OBJECTIVE_CYCLE_TEST_DATABASE_URL",
    "AIX_LEDGER_DAL_DATABASE_URL",
)


class ExecutionKernelRepositoryError(RuntimeError):
    """Base error for kernel ledger operations. Never silently swallowed."""


class ConcurrencyConflictError(ExecutionKernelRepositoryError):
    def __init__(self, tenant_id: str, aggregate_id: str, expected_sequence_number: int) -> None:
        super().__init__(
            f"concurrency conflict for tenant={tenant_id!r} aggregate_id={aggregate_id!r}: "
            f"sequence_number={expected_sequence_number} was already taken"
        )
        self.tenant_id = tenant_id
        self.aggregate_id = aggregate_id
        self.expected_sequence_number = expected_sequence_number


@dataclass(frozen=True)
class ExecutionEvent:
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


class ExecutionLedgerPort(Protocol):
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
    ) -> ExecutionEvent: ...

    async def load_events(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> list[ExecutionEvent]: ...

    async def latest_snapshot(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> ExecutionEvent | None: ...


def resolve_test_database_url() -> str | None:
    for key in TEST_DATABASE_URL_ENV_KEYS:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return None


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


def _require_aggregate_type(aggregate_type: str) -> str:
    normalized = str(aggregate_type or "").strip()
    if normalized not in AGGREGATE_TYPES:
        raise ExecutionKernelRepositoryError(
            f"invalid aggregate_type={aggregate_type!r}; allowed={sorted(AGGREGATE_TYPES)}"
        )
    return normalized


def _row_to_event(row: asyncpg.Record, *, replayed: bool = False) -> ExecutionEvent:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return ExecutionEvent(
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


class ExecutionKernelRepository:
    """Postgres append-only access to ``aix_execution_events``."""

    def __init__(self, database_url: str, *, ensure_schema: bool = False, command_timeout_seconds: float = 5.0) -> None:
        if not database_url:
            raise ExecutionKernelRepositoryError("database_url is required")
        self._database_url = database_url
        self._ensure_schema = ensure_schema
        self._command_timeout_seconds = command_timeout_seconds

    async def _connect(self) -> asyncpg.Connection:
        try:
            return await acquire_pooled_connection(
                self._database_url,
                ensure_schema=self._ensure_schema,
                ensure_schema_fn=ensure_execution_kernel_schema,
                command_timeout=self._command_timeout_seconds,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("execution-kernel ledger connection failed") from exc

    async def _connect_scoped(self, tenant_id: str) -> asyncpg.Connection:
        """EX5-S8: connect and set the session-local tenant context so the
        RLS policy on aix_execution_events (once enabled via
        ensure_execution_kernel_schema) actually enforces isolation for
        this connection — not merely relying on this class's own WHERE
        clauses. Every call site already receives tenant_id as an explicit
        parameter; this makes it load-bearing at the database layer too."""
        conn = await self._connect()
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", tenant_id)
        return conn

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
    ) -> ExecutionEvent:
        tenant_id = _require_tenant(tenant_id)
        aggregate_type = _require_aggregate_type(aggregate_type)
        if not str(aggregate_id or "").strip():
            raise ExecutionKernelRepositoryError("aggregate_id is required")
        if not str(command_id or "").strip():
            raise ExecutionKernelRepositoryError("command_id is required")
        if expected_sequence_number < 1:
            raise ExecutionKernelRepositoryError("expected_sequence_number must be >= 1")

        conn = await self._connect_scoped(tenant_id)
        try:
            async with conn.transaction():
                if self._ensure_schema:
                    await ensure_execution_kernel_schema(conn)
                event_id = str(uuid4())
                try:
                    row = await conn.fetchrow(
                        f"""
                        INSERT INTO {EXECUTION_EVENTS_TABLE}
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
                    if constraint == COMMAND_CONSTRAINT_NAME:
                        existing = await conn.fetchrow(
                            f"SELECT * FROM {EXECUTION_EVENTS_TABLE} WHERE tenant_id = $1 AND command_id = $2",
                            tenant_id,
                            command_id,
                        )
                        if existing is None:
                            raise ExecutionKernelRepositoryError(
                                "duplicate command_id reported but existing row not found"
                            ) from exc
                        return _row_to_event(existing, replayed=True)
                    if constraint == SEQUENCE_CONSTRAINT_NAME:
                        raise ConcurrencyConflictError(tenant_id, aggregate_id, expected_sequence_number) from exc
                    raise ExecutionKernelRepositoryError(
                        f"unexpected unique violation on constraint {constraint!r}"
                    ) from exc
                return _row_to_event(row)
        except (ConcurrencyConflictError, ExecutionKernelRepositoryError):
            raise
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("execution-kernel ledger append failed") from exc
        finally:
            await conn.close()

    async def load_events(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> list[ExecutionEvent]:
        tenant_id = _require_tenant(tenant_id)
        aggregate_type = _require_aggregate_type(aggregate_type)
        conn = await self._connect_scoped(tenant_id)
        try:
            if self._ensure_schema:
                await ensure_execution_kernel_schema(conn)
            rows = await conn.fetch(
                f"""
                SELECT * FROM {EXECUTION_EVENTS_TABLE}
                WHERE tenant_id = $1 AND aggregate_type = $2 AND aggregate_id = $3
                ORDER BY sequence_number ASC
                """,
                tenant_id,
                aggregate_type,
                aggregate_id,
            )
            return [_row_to_event(row) for row in rows]
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("execution-kernel ledger read failed") from exc
        finally:
            await conn.close()

    async def latest_snapshot(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> ExecutionEvent | None:
        events = await self.load_events(
            tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id
        )
        return events[-1] if events else None


@dataclass
class InMemoryExecutionKernelRepository:
    """Unit-test ledger — same concurrency/idempotency semantics, no Postgres."""

    _events: list[ExecutionEvent] = field(default_factory=list)

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
    ) -> ExecutionEvent:
        tenant_id = _require_tenant(tenant_id)
        aggregate_type = _require_aggregate_type(aggregate_type)
        if not str(aggregate_id or "").strip():
            raise ExecutionKernelRepositoryError("aggregate_id is required")
        if not str(command_id or "").strip():
            raise ExecutionKernelRepositoryError("command_id is required")
        if expected_sequence_number < 1:
            raise ExecutionKernelRepositoryError("expected_sequence_number must be >= 1")

        for existing in self._events:
            if existing.tenant_id == tenant_id and existing.command_id == command_id:
                return ExecutionEvent(
                    id=existing.id,
                    tenant_id=existing.tenant_id,
                    aggregate_type=existing.aggregate_type,
                    aggregate_id=existing.aggregate_id,
                    sequence_number=existing.sequence_number,
                    event_type=existing.event_type,
                    command_id=existing.command_id,
                    payload=dict(existing.payload),
                    created_at=existing.created_at,
                    replayed=True,
                )
        for existing in self._events:
            if (
                existing.tenant_id == tenant_id
                and existing.aggregate_id == aggregate_id
                and existing.sequence_number == expected_sequence_number
            ):
                raise ConcurrencyConflictError(tenant_id, aggregate_id, expected_sequence_number)

        event = ExecutionEvent(
            id=str(uuid4()),
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            sequence_number=expected_sequence_number,
            event_type=event_type,
            command_id=command_id,
            payload=dict(payload),
            created_at=datetime.now(timezone.utc).isoformat(),
            replayed=False,
        )
        self._events.append(event)
        return event

    async def load_events(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> list[ExecutionEvent]:
        tenant_id = _require_tenant(tenant_id)
        aggregate_type = _require_aggregate_type(aggregate_type)
        matched = [
            e
            for e in self._events
            if e.tenant_id == tenant_id
            and e.aggregate_type == aggregate_type
            and e.aggregate_id == aggregate_id
        ]
        return sorted(matched, key=lambda e: e.sequence_number)

    async def latest_snapshot(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> ExecutionEvent | None:
        events = await self.load_events(
            tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id
        )
        return events[-1] if events else None


__all__ = [
    "TEST_DATABASE_URL_ENV_KEYS",
    "ExecutionKernelRepositoryError",
    "ConcurrencyConflictError",
    "ExecutionEvent",
    "ExecutionLedgerPort",
    "ExecutionKernelRepository",
    "InMemoryExecutionKernelRepository",
    "resolve_test_database_url",
]
