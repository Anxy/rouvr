"""Shared helper for Postgres schema-ensure DDL run on the hot path by a
runtime role that may not own the tables it operates on.

Live-proof context (2026-08-15): driving a real objective through
`aix-chat-cli.ps1` against the live :8100 service (postgres durability mode)
reproduced hard 500s on `POST /aix/objectives/admit` from
`asyncpg.exceptions.InsufficientPrivilegeError: must be owner of table ...`,
raised from `CREATE INDEX IF NOT EXISTS` statements inside several modules'
`ensure_*_schema()` functions (`objective_cycle/ledger_schema.py` ->
`objective_cycle_events`, `durable_aggregate/__init__.py` -> `aix_runs`). Both
tables already existed; the specific indexes did not.

`CREATE TABLE IF NOT EXISTS` only needs schema `CREATE` privilege and no-ops
past an existing table without any ownership check. `CREATE INDEX` has no
separate privilege in Postgres: it always requires owning the target table,
even with `IF NOT EXISTS`, even to no-op past an index that already exists.
A runtime role with DML-only rights on a table provisioned by a different
(migration/admin) role will hit this on every call to these schema-ensure
functions -- several of which (see `objective_cycle/repository.py`'s
`_ensure_schema`) are not one-shot latches, so this is not a rare edge case.

The index is a query-speed optimization; the table (created above, or by an
earlier migration) is what read/write correctness actually depends on. A
non-owner role cannot provision the index, so that must not fail the caller.
"""

from __future__ import annotations

import logging

import asyncpg  # type: ignore[import-untyped]


async def create_index_tolerating_non_owner(
    conn: "asyncpg.Connection", *, index_name: str, statement: str, logger: logging.Logger
) -> None:
    """Run a `CREATE INDEX IF NOT EXISTS` statement; swallow (with a warning)
    only the specific case where the runtime role is not the table owner.
    Any other failure (broken connection, genuine syntax error, etc.) still
    propagates -- this is not a blanket try/except around DDL."""
    try:
        await conn.execute(statement)
    except asyncpg.InsufficientPrivilegeError:
        logger.warning(
            "%s: skipping (runtime role is not the table owner, cannot CREATE INDEX)",
            index_name,
        )


__all__ = ["create_index_tolerating_non_owner"]
