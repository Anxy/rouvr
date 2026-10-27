"""Tiny migration runner for the live-fixture SQLite user store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schema.sql"
MIGRATIONS_DIR = HERE / "migrations"


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def list_migrations() -> list[Path]:
    if not MIGRATIONS_DIR.is_dir():
        return []
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def apply_migrations(conn: sqlite3.Connection) -> list[str]:
    applied: list[str] = []
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fixture_schema_migrations ("
        "filename TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    done = {
        str(row["filename"])
        for row in conn.execute("SELECT filename FROM fixture_schema_migrations")
    }
    for path in list_migrations():
        name = path.name
        if name in done:
            continue
        conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO fixture_schema_migrations(filename) VALUES (?)",
            (name,),
        )
        applied.append(name)
    conn.commit()
    return applied


def bootstrap(db_path: str | Path) -> sqlite3.Connection:
    conn = connect(db_path)
    apply_schema(conn)
    apply_migrations(conn)
    return conn
