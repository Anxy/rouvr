"""SQLite hardening for the Rouvr ledger (FL-A5).

WAL + busy_timeout(5000ms) applied to every pooled connection, plus a
per-database single-writer queue shared by the API and in-process workers.

Lite runs on one machine with the API as the only path to state, and every
ledger transaction is short, so serializing ``begin()`` transactions through
one lock per DB file removes "database is locked" failures without
measurable latency. WAL keeps concurrent readers (via ``connect()``) from
blocking writers, and busy_timeout covers writers from other processes that
cannot share this lock.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event

BUSY_TIMEOUT_MS = 5000
BUSY_TIMEOUT_S = 5.0

_locks_guard = threading.Lock()
_write_locks: dict[str, threading.Lock] = {}


def db_key_for_url(db_url: str) -> str:
    """Stable per-database key: the SQLite file path, else the full URL."""
    prefix = "sqlite:///"
    if db_url.startswith(prefix):
        return "sqlite:" + db_url[len(prefix):]
    return db_url


def get_write_lock(db_url: str) -> threading.Lock:
    """The single-writer lock for this DB file, shared process-wide."""
    key = db_key_for_url(db_url)
    with _locks_guard:
        existing = _write_locks.get(key)
        if existing is not None:
            return existing
        fresh = threading.Lock()
        _write_locks[key] = fresh
        return fresh


def _apply_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def create_hardened_engine(db_url: str, **kwargs):
    """SQLAlchemy engine with SQLite hardening (WAL + busy_timeout)."""
    if db_url.startswith("sqlite"):
        connect_args = dict(kwargs.pop("connect_args", {}))
        connect_args.setdefault("timeout", BUSY_TIMEOUT_S)
        connect_args.setdefault("check_same_thread", False)
        kwargs["connect_args"] = connect_args
        if ":memory:" in db_url and "poolclass" not in kwargs:
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    engine = create_engine(db_url, **kwargs)
    if db_url.startswith("sqlite"):
        event.listen(engine, "connect", _apply_pragmas)
    return engine


@contextmanager
def begin_write(engine, db_url: str) -> Iterator:
    """Acquire this DB's writer lock, then open a transaction."""
    with get_write_lock(db_url):
        with engine.begin() as connection:
            yield connection


@contextmanager
def _locked_begin(engine, lock: threading.Lock) -> Iterator:
    with lock:
        with engine.begin() as connection:
            yield connection


class SingleWriterEngine:
    """Engine wrapper routing every ``begin()`` through the writer lock.

    ``connect()`` stays unlocked so concurrent readers keep WAL's
    non-blocking reads; all transactions opened via ``begin()`` serialize.
    """

    def __init__(self, engine, db_url: str) -> None:
        self._engine = engine
        self._lock = get_write_lock(db_url)

    def begin(self):
        return _locked_begin(self._engine, self._lock)

    def connect(self):
        return self._engine.connect()

    def __getattr__(self, name: str):
        return getattr(self._engine, name)


def single_writer(engine, db_url: str) -> SingleWriterEngine:
    """Wrap an engine so all ``begin()`` transactions share one writer queue."""
    return SingleWriterEngine(engine, db_url)
