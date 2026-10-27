"""Regression: SQLite boolean-column fallback + syntactic validation rejection."""

from __future__ import annotations

from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.executor.multi_step_execute import _is_syntactic_only_validation
from backend.aix_core.runtime.executor.source_patch_codegen import (
    local_fallback_sqlite_boolean_column,
)


def test_git_diff_check_is_syntactic_only() -> None:
    assert _is_syntactic_only_validation(
        ["git", "diff", "--check", "--", "backend/tests/.../db/schema.sql"]
    )
    assert not _is_syntactic_only_validation(
        ["python", "-m", "pytest", "-q", "backend/tests/.../db/test_db_users.py"]
    )


def test_sqlite_boolean_column_fallback_handles_crlf_and_full_model() -> None:
    models_crlf = (
        '"""Application model for the live-fixture user store."""\r\n'
        "\r\n"
        "from dataclasses import dataclass\r\n"
        "\r\n"
        "\r\n"
        "@dataclass\r\n"
        "class FixtureUser:\r\n"
        "    id: int\r\n"
        "    email: str\r\n"
        "    display_name: str\r\n"
        "\r\n"
        "    @classmethod\r\n"
        '    def from_row(cls, row):\r\n'
        "        return cls(\r\n"
        '            id=int(row["id"]),\r\n'
        '            email=str(row["email"]),\r\n'
        '            display_name=str(row["display_name"] or ""),\r\n'
        "        )\r\n"
        "\r\n"
        "\r\n"
        "def insert_user(conn, *, email: str, display_name: str = \"\"):\r\n"
        "    cur = conn.execute(\r\n"
        '        "INSERT INTO fixture_users(email, display_name) VALUES (?, ?)",\r\n'
        "        (email, display_name),\r\n"
        "    )\r\n"
        "    conn.commit()\r\n"
        "    row = conn.execute(\r\n"
        '        "SELECT id, email, display_name FROM fixture_users WHERE id = ?",\r\n'
        "        (int(cur.lastrowid),),\r\n"
        "    ).fetchone()\r\n"
        "    return FixtureUser.from_row(row)\r\n"
    )
    report = DiscoveryReport()
    report.config_files = {
        "backend/tests/.../db/schema.sql": "CREATE TABLE fixture_users (id INTEGER);\n",
        "backend/tests/.../db/migrate.py": "def bootstrap(p): ...\n",
        "backend/tests/.../db/models.py": models_crlf,
    }
    result = local_fallback_sqlite_boolean_column(
        report,
        "Add email verification support with a forward migration and update the model.",
    )
    assert result is not None
    paths = {e.path for e in result.edits}
    assert any(p.endswith("migrations/001_add_email_verified.sql") for p in paths)
    models_edit = next(e for e in result.edits if e.path.endswith("models.py"))
    assert "email_verified: bool = False" in (models_edit.new_content or "")
    assert 'email_verified=bool(int(row["email_verified"]' in (models_edit.new_content or "")
    assert "SELECT id, email, display_name, email_verified FROM fixture_users" in (
        models_edit.new_content or ""
    )


def test_sqlite_fallback_preferred_over_snippet_for_email_verification() -> None:
    """Regression: LLM schema hunks must not win over additive migration fallback."""
    report = DiscoveryReport()
    report.config_files = {
        "backend/tests/.../db/schema.sql": (
            "-- Controlled live-fixture schema (SQLite).\n"
            "CREATE TABLE IF NOT EXISTS fixture_users (\n"
            "    id INTEGER PRIMARY KEY,\n"
            "    email TEXT NOT NULL UNIQUE,\n"
            "    display_name TEXT NOT NULL DEFAULT ''\n"
            ");\n"
        ),
        "backend/tests/.../db/migrate.py": "def bootstrap(p): ...\n",
        "backend/tests/.../db/models.py": (
            "@dataclass\nclass FixtureUser:\n"
            "    id: int\n    email: str\n    display_name: str\n"
        ),
    }
    result = local_fallback_sqlite_boolean_column(
        report,
        "Add email verification support with a forward migration and update the model.",
    )
    assert result is not None
    assert any("/migrations/" in e.path.replace("\\", "/") for e in result.edits)
    assert not any(e.path.endswith("schema.sql") for e in result.edits)


def test_sqlite_boolean_column_fallback_repairs_partial_select_only_models() -> None:
    partial = (
        "@dataclass\n"
        "class FixtureUser:\n"
        "    id: int\n"
        "    email: str\n"
        "    display_name: str\n"
        "\n"
        "    @classmethod\n"
        "    def from_row(cls, row):\n"
        "        return cls(\n"
        '            id=int(row["id"]),\n'
        '            email=str(row["email"]),\n'
        '            display_name=str(row["display_name"] or ""),\n'
        "        )\n"
        "\n"
        "def get_user(conn, user_id):\n"
        "    row = conn.execute(\n"
        '        "SELECT id, email, display_name, email_verified FROM fixture_users WHERE id = ?",\n'
        "        (int(user_id),),\n"
        "    ).fetchone()\n"
        "    return FixtureUser.from_row(row)\n"
    )
    report = DiscoveryReport()
    report.config_files = {
        "backend/tests/.../db/migrate.py": "def bootstrap(p): ...\n",
        "backend/tests/.../db/models.py": partial,
    }
    result = local_fallback_sqlite_boolean_column(
        report,
        "Add email verification column via migration; existing users remain loadable.",
    )
    assert result is not None
    models_edit = next(e for e in result.edits if e.path.endswith("models.py"))
    assert "email_verified: bool = False" in (models_edit.new_content or "")
