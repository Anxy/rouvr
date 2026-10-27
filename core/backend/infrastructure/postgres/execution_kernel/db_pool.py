"""AIX-EXECUTION-006-S3: shared asyncpg connection pool for the Execution Kernel.

A zero-trust audit found every Postgres-backed store opened a brand-new
`asyncpg.connect()` (fresh TCP + auth handshake) on every single query, and
re-ran the full multi-statement schema DDL on every one of those connections
by default — compounding the N+1 dependency-resolution pattern into a severe
hot-path cost.

This module gives every store a connection drawn from one pool per
database_url (created once, reused for the process lifetime) and ensures the
schema DDL runs at most once per database_url, not once per connection.

`_PooledConnectionProxy` exists so every existing call site — the
`conn = await self._connect(); try: ...; finally: await conn.close()`
pattern used ~60 times across postgres_stores.py / postgres_intelligence_stores.py
/ repository.py — keeps working completely unchanged: `.close()` on the
returned object now releases the connection back to the pool instead of
tearing it down.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import asyncpg  # type: ignore[import-untyped]

_POOLS: dict[str, tuple[asyncpg.Pool, asyncio.AbstractEventLoop]] = {}
_SCHEMA_ENSURED: set[tuple[str, str]] = set()
_POOL_LOCK = asyncio.Lock()
_SCHEMA_LOCK = asyncio.Lock()


class _PooledConnectionProxy:
    """Stands in for an asyncpg.Connection; close() releases to the pool."""

    __slots__ = ("_pool", "_conn", "_released")

    def __init__(self, pool: asyncpg.Pool, conn: asyncpg.Connection) -> None:
        object.__setattr__(self, "_pool", pool)
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_released", False)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    async def close(self) -> None:
        if self._released:
            return
        object.__setattr__(self, "_released", True)
        # AIX-EXECUTION-006-S4: a pooled connection can carry session-local
        # state (e.g. app.current_tenant_id, set by tenant-scoped callers for
        # RLS) across requests. Reset before returning to the pool so a
        # caller that forgets to (re)scope the next use fails LOUD and EMPTY
        # (current_setting returns NULL -> RLS policy matches nothing) rather
        # than silently inheriting a previous request's tenant context.
        try:
            await self._conn.execute("RESET ALL")
        except Exception:  # noqa: BLE001 - connection may already be broken; still release it
            pass
        await self._pool.release(self._conn)


async def get_pool(
    database_url: str,
    *,
    min_size: int = 1,
    max_size: int = 10,
    command_timeout: float = 5.0,
) -> asyncpg.Pool:
    current_loop = asyncio.get_running_loop()
    entry = _POOLS.get(database_url)
    if entry is not None and entry[1] is current_loop:
        return entry[0]
    async with _POOL_LOCK:
        entry = _POOLS.get(database_url)
        if entry is not None and entry[1] is current_loop:
            return entry[0]
        if entry is not None:
            # Stale pool from a previous (now-closed) event loop — e.g. each
            # pytest-asyncio test function gets its own loop. The old pool's
            # connections belong to a dead loop; forget it (best-effort close,
            # the loop it needs may already be gone) and mint a fresh one for
            # the loop we're actually running in now.
            stale_pool, _ = entry
            try:
                await stale_pool.close()
            except Exception:  # noqa: BLE001 - loop may already be closed
                pass
            _SCHEMA_ENSURED.difference_update(
                {key for key in _SCHEMA_ENSURED if key[0] == database_url}
            )
        pool = await asyncpg.create_pool(
            database_url,
            min_size=min_size,
            max_size=max_size,
            command_timeout=command_timeout,
        )
        _POOLS[database_url] = (pool, current_loop)
        return pool


async def acquire_pooled_connection(
    database_url: str,
    *,
    ensure_schema: bool = False,
    ensure_schema_fn: Callable[[asyncpg.Connection], Awaitable[None]] | None = None,
    command_timeout: float = 5.0,
    tenant_id: str | None = None,
) -> asyncpg.Connection:
    """Drop-in replacement for `asyncpg.connect(...)` backed by a shared pool.

    Runs `ensure_schema_fn` at most once per database_url for the life of the
    process, not once per acquired connection.

    When `tenant_id` is given, sets the `app.current_tenant_id` session
    variable on every acquire (not cached — must happen on every use, since a
    pooled connection may have served a different tenant last time) so RLS
    policies keyed on that variable actually enforce isolation, not just
    exist unused.
    """
    pool = await get_pool(database_url, command_timeout=command_timeout)
    if ensure_schema and ensure_schema_fn is not None:
        # Keyed by (url, function identity) — not url alone. Different callers
        # ensure different (nested-superset) schema subsets against the same
        # database_url; caching by url alone would let whichever runs first
        # silently skip every other schema function for that same url.
        schema_key = (database_url, getattr(ensure_schema_fn, "__qualname__", repr(ensure_schema_fn)))
        if schema_key not in _SCHEMA_ENSURED:
            async with _SCHEMA_LOCK:
                if schema_key not in _SCHEMA_ENSURED:
                    async with pool.acquire() as conn:
                        await ensure_schema_fn(conn)
                    _SCHEMA_ENSURED.add(schema_key)
    conn = await pool.acquire()
    if tenant_id:
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", tenant_id)
    return _PooledConnectionProxy(pool, conn)  # type: ignore[return-value]


async def close_all_pools() -> None:
    """Test/shutdown helper — closes and forgets every pool this process opened."""
    entries = list(_POOLS.items())
    _POOLS.clear()
    _SCHEMA_ENSURED.clear()
    for _, (pool, _loop) in entries:
        try:
            await pool.close()
        except Exception:  # noqa: BLE001 - loop may already be closed
            pass
