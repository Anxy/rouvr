"""001K-W02: Transactional PostgreSQL outbox — publish-after-commit with lease/retry.

Local programme proof uses an in-process store with the same semantics a PG outbox
table must enforce. Production durability remains 001B/001K staging work.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from backend.aix_core.runtime.objective.ulid_compat import new_ulid
OutboxState = Literal["pending", "leased", "published", "failed"]


def _utc() -> datetime:
    return datetime.now(UTC)


@dataclass
class OutboxRow:
    id: str
    tenant_id: str
    event_id: str
    envelope: dict[str, Any]
    state: OutboxState = "pending"
    attempt_count: int = 0
    lease_owner: str | None = None
    lease_until: datetime | None = None
    created_at: datetime = field(default_factory=_utc)
    published_at: datetime | None = None


class PostgresOutboxError(ValueError):
    pass


class TransactionalPostgresOutbox:
    """Publish-after-commit outbox: rows become visible only after commit()."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._committed: dict[str, OutboxRow] = {}
        self._txn: dict[str, OutboxRow] | None = None

    def begin(self) -> None:
        with self._lock:
            if self._txn is not None:
                raise PostgresOutboxError("TXN_ALREADY_OPEN")
            self._txn = {}

    def enqueue(self, *, tenant_id: str, event_id: str, envelope: dict[str, Any]) -> str:
        with self._lock:
            if self._txn is None:
                raise PostgresOutboxError("TXN_REQUIRED")
            row_id = new_ulid()
            self._txn[row_id] = OutboxRow(
                id=row_id,
                tenant_id=tenant_id,
                event_id=event_id,
                envelope=dict(envelope),
            )
            return row_id

    def commit(self) -> None:
        with self._lock:
            if self._txn is None:
                raise PostgresOutboxError("TXN_REQUIRED")
            self._committed.update(self._txn)
            self._txn = None

    def rollback(self) -> None:
        with self._lock:
            self._txn = None

    def claim_batch(self, *, worker_id: str, limit: int = 10, lease_seconds: int = 30) -> list[OutboxRow]:
        with self._lock:
            now = _utc()
            claimed: list[OutboxRow] = []
            for row in self._committed.values():
                if len(claimed) >= limit:
                    break
                if row.state == "published":
                    continue
                if row.state == "leased" and row.lease_until and row.lease_until > now:
                    continue
                row.state = "leased"
                row.lease_owner = worker_id
                row.lease_until = datetime.fromtimestamp(now.timestamp() + lease_seconds, tz=UTC)
                row.attempt_count += 1
                claimed.append(row)
            return claimed

    def mark_published(self, row_id: str, *, worker_id: str) -> None:
        with self._lock:
            row = self._committed[row_id]
            if row.lease_owner != worker_id:
                raise PostgresOutboxError("LEASE_OWNER_MISMATCH")
            row.state = "published"
            row.published_at = _utc()
            row.lease_owner = None
            row.lease_until = None

    def mark_failed(self, row_id: str, *, worker_id: str) -> None:
        with self._lock:
            row = self._committed[row_id]
            if row.lease_owner != worker_id:
                raise PostgresOutboxError("LEASE_OWNER_MISMATCH")
            row.state = "failed"
            row.lease_owner = None
            row.lease_until = None

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._committed.values() if r.state in {"pending", "leased", "failed"})
