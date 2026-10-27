from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from backend.hyeve_aix_service.config import settings


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class IdempotencyRecord:
    tenant_id: str
    idempotency_key: str
    first_seen_utc: str
    project_id: str | None
    run_id: str | None
    task_id: str | None
    status: str
    result_payload: dict[str, Any] | None
    updated_at_utc: str


class AixRunIdempotencyStore:
    def __init__(self, *, db_path: str | Path | None = None) -> None:
        resolved_path = db_path if db_path is not None else str(settings.AIX_RUN_IDEMPOTENCY_DB_PATH)
        self._db_path = Path(resolved_path)
        self._lock = Lock()
        self._initialized = False

    def get(self, *, tenant_id: str, idempotency_key: str) -> IdempotencyRecord | None:
        self._ensure_initialized()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT tenant_id,idempotency_key,first_seen_utc,project_id,run_id,task_id,status,result_payload,updated_at_utc
                FROM aix_run_idempotency
                WHERE tenant_id = ? AND idempotency_key = ?
                """,
                (tenant_id, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[7]) if row[7] else None
        return IdempotencyRecord(
            tenant_id=str(row[0]),
            idempotency_key=str(row[1]),
            first_seen_utc=str(row[2]),
            project_id=str(row[3]) if row[3] is not None else None,
            run_id=str(row[4]) if row[4] is not None else None,
            task_id=str(row[5]) if row[5] is not None else None,
            status=str(row[6]),
            result_payload=payload if isinstance(payload, dict) else None,
            updated_at_utc=str(row[8]),
        )

    def claim(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        project_id: str | None,
        task_id: str | None,
    ) -> tuple[bool, IdempotencyRecord]:
        self._ensure_initialized()
        now = _utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO aix_run_idempotency (
                    tenant_id,idempotency_key,first_seen_utc,project_id,run_id,task_id,status,result_payload,updated_at_utc
                ) VALUES (?, ?, ?, ?, NULL, ?, 'in_progress', NULL, ?)
                """,
                (tenant_id, idempotency_key, now, project_id, task_id, now),
            )
            inserted = conn.total_changes > 0
            row = conn.execute(
                """
                SELECT tenant_id,idempotency_key,first_seen_utc,project_id,run_id,task_id,status,result_payload,updated_at_utc
                FROM aix_run_idempotency
                WHERE tenant_id = ? AND idempotency_key = ?
                """,
                (tenant_id, idempotency_key),
            ).fetchone()
        if row is None:
            raise RuntimeError("IDEMPOTENCY_READBACK_FAILED")
        payload = json.loads(row[7]) if row[7] else None
        return inserted, IdempotencyRecord(
            tenant_id=str(row[0]),
            idempotency_key=str(row[1]),
            first_seen_utc=str(row[2]),
            project_id=str(row[3]) if row[3] is not None else None,
            run_id=str(row[4]) if row[4] is not None else None,
            task_id=str(row[5]) if row[5] is not None else None,
            status=str(row[6]),
            result_payload=payload if isinstance(payload, dict) else None,
            updated_at_utc=str(row[8]),
        )

    def mark_completed(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        project_id: str | None,
        run_id: str | None,
        task_id: str | None,
        status: str,
        result_payload: dict[str, Any],
    ) -> None:
        self._ensure_initialized()
        now = _utc_now()
        payload_json = json.dumps(result_payload, separators=(",", ":"), sort_keys=True)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE aix_run_idempotency
                SET project_id = ?,
                    run_id = ?,
                    task_id = ?,
                    status = ?,
                    result_payload = ?,
                    updated_at_utc = ?
                WHERE tenant_id = ? AND idempotency_key = ?
                """,
                (project_id, run_id, task_id, status, payload_json, now, tenant_id, idempotency_key),
            )

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
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
            self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path.as_posix(), timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn


aix_run_idempotency_store = AixRunIdempotencyStore()


__all__ = ["IdempotencyRecord", "AixRunIdempotencyStore", "aix_run_idempotency_store"]
