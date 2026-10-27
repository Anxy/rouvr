"""Regression coverage for `ensure_objective_cycle_schema`'s handling of a runtime
DB role that does not own `objective_cycle_events`.

Live-proof context: driving a real objective through `aix-chat-cli.ps1` against the
live :8100 service (postgres durability mode) on 2026-08-15 reproduced a hard 500 on
every `POST /aix/objectives/admit` -- `asyncpg.exceptions.InsufficientPrivilegeError:
must be owner of table objective_cycle_events`, raised from the `CREATE INDEX IF NOT
EXISTS` statement in `ensure_objective_cycle_schema` (table already existed; that one
index did not). `repository.py`'s `_ensure_schema` re-runs this schema-ensure call on
every read/write, not once, so this was not a rare edge case -- it blocked all new
objective-cycle admissions outright. `CREATE INDEX` has no separate privilege in
Postgres: it always requires table ownership, even with `IF NOT EXISTS`, even when the
index already exists. See the matching fix and comment in ledger_schema.py.
"""

from __future__ import annotations

import asyncio

import asyncpg  # type: ignore[import-untyped]
import pytest

from backend.aix_core.runtime.objective.ledger_schema import (
    OBJECTIVE_CYCLE_EVENTS_TABLE,
    ensure_objective_cycle_schema,
)


class _FakeConnDeniedIndexOwnership:
    """Mimics a runtime DB role that can CREATE TABLE IF NOT EXISTS (no-op, table
    already exists) but is not the table owner, so CREATE INDEX fails."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    async def execute(self, query: str) -> None:
        self.executed.append(query)
        if "CREATE INDEX" in query:
            raise asyncpg.InsufficientPrivilegeError(
                f"must be owner of table {OBJECTIVE_CYCLE_EVENTS_TABLE}"
            )


class _FakeConnFullyPrivileged:
    def __init__(self) -> None:
        self.executed: list[str] = []

    async def execute(self, query: str) -> None:
        self.executed.append(query)


class TestEnsureObjectiveCycleSchemaToleratesNonOwnerRole:
    def test_index_privilege_error_does_not_propagate(self) -> None:
        conn = _FakeConnDeniedIndexOwnership()
        # Must not raise: a non-owner runtime role hitting this on every call
        # (repository.py has no one-shot latch) previously surfaced as a hard
        # 500 all the way up through _persist_event / admit_objective.
        asyncio.run(ensure_objective_cycle_schema(conn))  # type: ignore[arg-type]
        assert any("CREATE TABLE" in q for q in conn.executed)
        assert any("CREATE INDEX" in q for q in conn.executed)

    def test_fully_privileged_role_still_creates_both(self) -> None:
        # The fix must not change behavior for the common/correct-permission
        # case: both statements still run and nothing is silently skipped.
        conn = _FakeConnFullyPrivileged()
        asyncio.run(ensure_objective_cycle_schema(conn))  # type: ignore[arg-type]
        assert len(conn.executed) == 2

    def test_other_postgres_errors_still_propagate(self) -> None:
        # The fix is scoped to InsufficientPrivilegeError specifically -- any
        # other failure mode (e.g. a genuinely broken connection) must still
        # surface, not be silently swallowed alongside it.
        class _FakeConnBroken:
            async def execute(self, query: str) -> None:
                raise asyncpg.ConnectionDoesNotExistError("connection closed")

        with pytest.raises(asyncpg.ConnectionDoesNotExistError):
            asyncio.run(ensure_objective_cycle_schema(_FakeConnBroken()))  # type: ignore[arg-type]
