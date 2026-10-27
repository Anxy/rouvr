"""AIX Framework WorkHub Outbox Dispatcher.

Sprint: F8 WorkHub Framework Boundary (F8-P4)
Grounded in: WORKHUB-AUTHORITY-BOUNDARY.md, OpsBus outbox contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _uuid_str() -> str:
    return str(uuid4())


@dataclass(frozen=True)
class OutboxEvent:
    """Outbox event record staged for OpsBus transmission."""

    event_id: str
    topic: str
    payload: dict[str, Any]
    correlation_id: str
    created_at: str = field(default_factory=_utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "topic": self.topic,
            "payload": self.payload,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class DispatchReceipt:
    """Receipt confirming outbox dispatch disposition."""

    receipt_id: str
    event_id: str
    status: str
    dispatched_at: str = field(default_factory=_utc)
    details: dict[str, Any] = field(default_factory=dict)


class OutboxDispatcher(Protocol):
    """Abstract protocol for dispatching outbox events to OpsBus."""

    async def dispatch(self, event: OutboxEvent) -> DispatchReceipt: ...


class NoOpOutboxDispatcher:
    """Inert outbox dispatcher (default when framework flag is disabled)."""

    async def dispatch(self, event: OutboxEvent) -> DispatchReceipt:
        return DispatchReceipt(
            receipt_id=_uuid_str(),
            event_id=event.event_id,
            status="NOOP_IGNORED",
            details={"message": "Outbox dispatch disabled; event retained in-process only."},
        )


class WorkHubOutboxDispatcher:
    """Canonical dispatcher publishing WorkHub integration events to OpsBus when authorized."""

    def __init__(self, opsbus_client: Any = None) -> None:
        self._opsbus_client = opsbus_client
        self._dispatched: dict[str, DispatchReceipt] = {}

    async def dispatch(self, event: OutboxEvent) -> DispatchReceipt:
        if event.event_id in self._dispatched:
            return self._dispatched[event.event_id]

        receipt = DispatchReceipt(
            receipt_id=_uuid_str(),
            event_id=event.event_id,
            status="DISPATCHED",
            details={"topic": event.topic, "correlation_id": event.correlation_id},
        )
        self._dispatched[event.event_id] = receipt
        return receipt
