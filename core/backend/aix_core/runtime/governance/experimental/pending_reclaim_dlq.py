"""AIX-E2E-CYCLE-001K-W06: pending/reclaim/DLQ/replay.

Builds on `memurai_streams.py`'s `xpending`/`xautoclaim` (already present on
`InMemoryMemuraiStreamsTransport`): a message that fails processing repeatedly is not
reclaimed forever. Past `POISON_THRESHOLD` failures it is moved to a dead-letter store
instead, and reclaim stops picking it up. Replay out of the DLQ is a deliberate, explicit
operator action (`replay`) — never automatic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

POISON_THRESHOLD = 3


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ReclaimableTransport(Protocol):
    def xpending(self) -> list[str]: ...
    def xautoclaim(self) -> list[Any]: ...


@dataclass
class DlqEntryV1:
    message_id: str
    envelope: dict[str, Any]
    failure_count: int
    first_failed_at: str
    last_error: str


class PendingReclaimDlqError(ValueError):
    pass


class DeadLetterStore:
    """In-process DLQ, consistent with the in-memory stand-in style already used by
    `memurai_streams.py`/`pg_outbox.py`/`pg_inbox.py` in this package."""

    def __init__(self) -> None:
        self._by_message_id: dict[str, DlqEntryV1] = {}
        self._replayed: set[str] = set()

    def contains(self, message_id: str) -> bool:
        return message_id in self._by_message_id

    def get(self, message_id: str) -> DlqEntryV1 | None:
        return self._by_message_id.get(message_id)

    def put(self, entry: DlqEntryV1) -> None:
        self._by_message_id[entry.message_id] = entry

    def was_replayed(self, message_id: str) -> bool:
        return message_id in self._replayed

    def mark_replayed(self, message_id: str) -> None:
        if message_id not in self._by_message_id:
            raise PendingReclaimDlqError(f"cannot replay unknown DLQ entry {message_id!r}")
        self._replayed.add(message_id)

    def all_entries(self) -> tuple[DlqEntryV1, ...]:
        return tuple(self._by_message_id.values())


class PendingReclaimCoordinator:
    """Tracks per-message failure counts across reclaim attempts and decides, for each
    pending message, whether it is still eligible for another `xautoclaim`-style reclaim
    or must be quarantined into the DLQ instead."""

    def __init__(self, *, dlq: DeadLetterStore, poison_threshold: int = POISON_THRESHOLD) -> None:
        if poison_threshold <= 0:
            raise PendingReclaimDlqError("poison_threshold must be positive")
        self._dlq = dlq
        self._poison_threshold = poison_threshold
        self._failure_counts: dict[str, int] = {}
        self._first_failed_at: dict[str, str] = {}

    @property
    def dlq(self) -> DeadLetterStore:
        return self._dlq

    def record_failure(self, *, message_id: str, envelope: dict[str, Any], error: str) -> DlqEntryV1 | None:
        """Record one more failure for `message_id`. Returns the `DlqEntryV1` if this
        failure pushed the message past `poison_threshold` (it is now quarantined and
        must not be reclaimed again by the caller), or `None` if it is still eligible
        for a normal reclaim."""
        self._failure_counts[message_id] = self._failure_counts.get(message_id, 0) + 1
        self._first_failed_at.setdefault(message_id, _utc_now())
        count = self._failure_counts[message_id]
        if count >= self._poison_threshold:
            entry = DlqEntryV1(
                message_id=message_id,
                envelope=dict(envelope),
                failure_count=count,
                first_failed_at=self._first_failed_at[message_id],
                last_error=error,
            )
            self._dlq.put(entry)
            return entry
        return None

    def is_poisoned(self, message_id: str) -> bool:
        return self._dlq.contains(message_id)

    def eligible_for_reclaim(self, pending_message_ids: list[str]) -> list[str]:
        """Filter a raw `xpending()` list down to messages that are not already
        quarantined in the DLQ — the reclaim loop must skip poisoned messages, not keep
        handing them back out via `xautoclaim`."""
        return [mid for mid in pending_message_ids if not self.is_poisoned(mid)]

    def replay(self, message_id: str, *, transport_reinject: Any) -> None:
        """Deliberately replay a DLQ'd message: re-inject its envelope via
        `transport_reinject(envelope)` (typically the transport's own `xadd`) and mark it
        replayed. Raises if `message_id` is not a known DLQ entry — replay only ever acts
        on an explicitly named, already-quarantined message, never a bulk/implicit retry."""
        entry = self._dlq.get(message_id)
        if entry is None:
            raise PendingReclaimDlqError(f"cannot replay unknown DLQ entry {message_id!r}")
        transport_reinject(entry.envelope)
        self._dlq.mark_replayed(message_id)


__all__ = [
    "POISON_THRESHOLD",
    "PendingReclaimDlqError",
    "DlqEntryV1",
    "DeadLetterStore",
    "PendingReclaimCoordinator",
]
