"""Focused tests for the live-fixture SQLite user store / migrations."""

from __future__ import annotations

from pathlib import Path

from backend.tests.runtime.objective_cycle.live_fixtures.db import migrate, models


def test_bootstrap_creates_users_table(tmp_path: Path) -> None:
    db = tmp_path / "users.sqlite"
    conn = migrate.bootstrap(db)
    try:
        cols = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(fixture_users)")
        }
        assert "email" in cols
        assert "display_name" in cols
    finally:
        conn.close()


def test_insert_and_get_user(tmp_path: Path) -> None:
    db = tmp_path / "users.sqlite"
    conn = migrate.bootstrap(db)
    try:
        created = models.insert_user(conn, email="a@example.com", display_name="Ada")
        loaded = models.get_user(conn, created.id)
        assert loaded is not None
        assert loaded.email == "a@example.com"
        assert loaded.display_name == "Ada"
    finally:
        conn.close()


def test_email_verified_column_exists_and_defaults_false(tmp_path: Path) -> None:
    """S10 acceptance: schema + model expose email_verified defaulting to false."""
    db = tmp_path / "users.sqlite"
    conn = migrate.bootstrap(db)
    try:
        cols = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(fixture_users)")
        }
        assert "email_verified" in cols
        created = models.insert_user(conn, email="b@example.com")
        row = conn.execute(
            "SELECT email_verified FROM fixture_users WHERE id = ?",
            (created.id,),
        ).fetchone()
        assert int(row["email_verified"]) == 0
        loaded = models.get_user(conn, created.id)
        assert loaded is not None
        assert getattr(loaded, "email_verified") is False
    finally:
        conn.close()
