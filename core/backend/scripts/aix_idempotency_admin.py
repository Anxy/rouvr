from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from backend.hyeve_aix_service.config import settings


def _default_db_path() -> Path:
    return Path(str(settings.AIX_RUN_IDEMPOTENCY_DB_PATH))


def _default_backup_dir() -> Path:
    return Path(str(settings.AIX_RUN_IDEMPOTENCY_BACKUP_DIR))


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path.as_posix(), timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


def cmd_init(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS aix_run_idempotency (
                tenant_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                first_seen_utc TEXT NOT NULL,
                project_id TEXT NULL,
                run_id TEXT NULL,
                task_id TEXT NULL,
                status TEXT NOT NULL,
                result_payload TEXT NULL,
                updated_at_utc TEXT NOT NULL,
                PRIMARY KEY (tenant_id, idempotency_key)
            )
            """
        )
    print(f"initialized: {db_path}")


def cmd_stats(db_path: Path) -> None:
    if not db_path.exists():
        print(f"missing: {db_path}")
        return
    with _connect(db_path) as conn:
        total = int(conn.execute("SELECT COUNT(1) FROM aix_run_idempotency").fetchone()[0])
        completed = int(
            conn.execute("SELECT COUNT(1) FROM aix_run_idempotency WHERE status IN ('completed','done')").fetchone()[0]
        )
        in_progress = int(
            conn.execute("SELECT COUNT(1) FROM aix_run_idempotency WHERE status='in_progress'").fetchone()[0]
        )
    print(f"db={db_path}")
    print(f"total={total}")
    print(f"completed={completed}")
    print(f"in_progress={in_progress}")


def cmd_backup(db_path: Path, backup_dir: Path) -> Path:
    if not db_path.exists():
        raise FileNotFoundError(f"idempotency db missing: {db_path}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"aix_run_idempotency-{stamp}.sqlite3"
    shutil.copy2(db_path, target)
    print(f"backup={target}")
    return target


def cmd_restore(db_path: Path, from_file: Path) -> None:
    if not from_file.exists():
        raise FileNotFoundError(f"backup file missing: {from_file}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(from_file, db_path)
    print(f"restored: {from_file} -> {db_path}")


def cmd_prune(db_path: Path, keep_days: int) -> None:
    if keep_days < 1:
        raise ValueError("keep_days must be >= 1")
    if not db_path.exists():
        print(f"missing: {db_path}")
        return
    with _connect(db_path) as conn:
        conn.execute(
            """
            DELETE FROM aix_run_idempotency
            WHERE updated_at_utc < datetime('now', ?)
            """,
            (f"-{int(keep_days)} days",),
        )
        deleted = conn.total_changes
    print(f"pruned_rows={deleted} keep_days={keep_days}")


def main() -> int:
    parser = argparse.ArgumentParser(description="AIX run idempotency sqlite admin helper.")
    parser.add_argument("action", choices=["init", "stats", "backup", "restore", "prune"])
    parser.add_argument("--db-path", default=str(_default_db_path()))
    parser.add_argument("--backup-dir", default=str(_default_backup_dir()))
    parser.add_argument("--from-file")
    parser.add_argument("--keep-days", type=int, default=30)
    args = parser.parse_args()

    db_path = Path(args.db_path)
    backup_dir = Path(args.backup_dir)
    if args.action == "init":
        cmd_init(db_path)
    elif args.action == "stats":
        cmd_stats(db_path)
    elif args.action == "backup":
        cmd_backup(db_path, backup_dir)
    elif args.action == "restore":
        if not args.from_file:
            raise ValueError("--from-file is required for restore")
        cmd_restore(db_path, Path(args.from_file))
    elif args.action == "prune":
        cmd_prune(db_path, args.keep_days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
