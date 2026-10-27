"""001K-W03: Durable PostgreSQL inbox — unique consumer/event key; commit-before-ack."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from backend.aix_core.runtime.objective.ulid_compat import new_ulid
InboxState = Literal["received", "processing", "processed", "failed"]


def _utc() -> datetime:
    return datetime.now(UTC)


@dataclass
class InboxRow:
    id: str
    event_id: str
    consumer_name: str
    envelope: dict[str, Any]
    state: InboxState = "received"
    attempt_count: int = 0
    lock_token: str | None = None
    created_at: datetime = field(default_factory=_utc)
    processed_at: datetime | None = None


class PostgresInboxError(ValueError):
    pass


class DurablePostgresInbox:
    """Unique (event_id, consumer_name). Ack is only legal after mark_processed (commit-before-ack)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_key: dict[tuple[str, str], InboxRow] = {}
        self._by_id: dict[str, InboxRow] = {}
        self._acked_stream_ids: set[str] = set()

    def receive_or_get(self, *, event_id: str, consumer_name: str, envelope: dict[str, Any]) -> InboxRow:
        with self._lock:
            key = (event_id, consumer_name)
            existing = self._by_key.get(key)
            if existing is not None:
                return existing
            row = InboxRow(id=new_ulid(), event_id=event_id, consumer_name=consumer_name, envelope=dict(envelope))
            self._by_key[key] = row
            self._by_id[row.id] = row
            return row

    def begin_processing(self, row_id: str) -> str:
        with self._lock:
            row = self._by_id[row_id]
            if row.state == "processed":
                raise PostgresInboxError("ALREADY_PROCESSED")
            token = new_ulid()
            row.state = "processing"
            row.lock_token = token
            row.attempt_count += 1
            return token

    def mark_processed(self, row_id: str, *, lock_token: str) -> None:
        with self._lock:
            row = self._by_id[row_id]
            if row.lock_token != lock_token:
                raise PostgresInboxError("LOCK_TOKEN_MISMATCH")
            row.state = "processed"
            row.processed_at = _utc()
            row.lock_token = None

    def mark_failed(self, row_id: str, *, lock_token: str) -> None:
        with self._lock:
            row = self._by_id[row_id]
            if row.lock_token != lock_token:
                raise PostgresInboxError("LOCK_TOKEN_MISMATCH")
            row.state = "failed"
            row.lock_token = None

    def ack_stream(self, *, stream_message_id: str, inbox_row_id: str) -> None:
        """XACK is only allowed after inbox commit (processed)."""
        with self._lock:
            row = self._by_id[inbox_row_id]
            if row.state != "processed":
                raise PostgresInboxError("ACK_BEFORE_INBOX_COMMIT_FORBIDDEN")
            self._acked_stream_ids.add(stream_message_id)

    def was_acked(self, stream_message_id: str) -> bool:
        with self._lock:
            return stream_message_id in self._acked_stream_ids
