from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.scripts.aix_idempotency_admin import cmd_backup, cmd_init, cmd_prune, cmd_restore


def _insert_row(db_path: Path, key: str) -> None:
    with sqlite3.connect(db_path.as_posix()) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO aix_run_idempotency
            (tenant_id,idempotency_key,first_seen_utc,project_id,run_id,task_id,status,result_payload,updated_at_utc)
            VALUES ('default', ?, datetime('now'), 'proj', 'run-1', 'task-1', 'completed', '{}', datetime('now'))
            """,
            (key,),
        )


def test_admin_init_backup_restore_and_prune(tmp_path: Path) -> None:
    db_path = tmp_path / "idempotency.sqlite3"
    backup_dir = tmp_path / "backups"
    cmd_init(db_path)
    _insert_row(db_path, "idem-1")

    backup = cmd_backup(db_path, backup_dir)
    assert backup.exists()

    with sqlite3.connect(db_path.as_posix()) as conn:
        conn.execute("DELETE FROM aix_run_idempotency")
    cmd_restore(db_path, backup)

    with sqlite3.connect(db_path.as_posix()) as conn:
        total = int(conn.execute("SELECT COUNT(1) FROM aix_run_idempotency").fetchone()[0])
    assert total == 1

    cmd_prune(db_path, keep_days=30)
