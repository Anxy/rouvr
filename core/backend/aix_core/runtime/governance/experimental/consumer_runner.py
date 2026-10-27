"""AIX-E2E-CYCLE-001K-W05: consumer runner.

Ties the existing `memurai_streams.py` transport and `pg_inbox.py` durable inbox
together: group ownership (one `ConsumerRunner` instance = one named consumer within a
group), heartbeats, exponential backoff on empty polls, and graceful shutdown. Enforces
commit-before-ack end to end: a message is only `xack`'d after the inbox row is marked
`processed`, never before — matching `DurablePostgresInbox.ack_stream`'s own
`ACK_BEFORE_INBOX_COMMIT_FORBIDDEN` guard, which this module must obey rather than route
around.

Duck-typed against `InMemoryMemuraiStreamsTransport` and `DurablePostgresInbox`'s public
methods rather than importing concrete classes, so a later real-Memurai/real-Postgres
implementation satisfying the same method shapes drops in without changing this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class StreamMessage(Protocol):
    message_id: str
    envelope: dict[str, Any]


class StreamsTransport(Protocol):
    def xreadgroup(self, *, count: int = 10) -> list[Any]: ...
    def xack(self, message_id: str) -> None: ...


class DurableInbox(Protocol):
    def receive_or_get(self, *, event_id: str, consumer_name: str, envelope: dict[str, Any]) -> Any: ...
    def begin_processing(self, row_id: str) -> str: ...
    def mark_processed(self, row_id: str, *, lock_token: str) -> None: ...
    def mark_failed(self, row_id: str, *, lock_token: str) -> None: ...
    def ack_stream(self, *, stream_message_id: str, inbox_row_id: str) -> None: ...


class ConsumerRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConsumerRunnerConfig:
    group_name: str
    consumer_id: str
    batch_size: int = 10
    idle_backoff_base_seconds: float = 0.5
    idle_backoff_max_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.group_name or not self.consumer_id:
            raise ConsumerRunnerError("group_name and consumer_id are required")
        if self.batch_size <= 0:
            raise ConsumerRunnerError("batch_size must be positive")
        if self.idle_backoff_base_seconds <= 0 or self.idle_backoff_max_seconds <= 0:
            raise ConsumerRunnerError("backoff seconds must be positive")
        if self.idle_backoff_base_seconds > self.idle_backoff_max_seconds:
            raise ConsumerRunnerError("idle_backoff_base_seconds must not exceed idle_backoff_max_seconds")


@dataclass(frozen=True)
class ProcessedMessageResult:
    message_id: str
    disposition: str  # "PROCESSED" | "HANDLER_FAILED" | "ALREADY_PROCESSED"


@dataclass
class PollResult:
    processed: list[ProcessedMessageResult] = field(default_factory=list)
    empty: bool = False


class ConsumerRunner:
    def __init__(
        self,
        *,
        config: ConsumerRunnerConfig,
        transport: StreamsTransport,
        inbox: DurableInbox,
        handler: Any,
    ) -> None:
        self._config = config
        self._transport = transport
        self._inbox = inbox
        self._handler = handler
        self._last_heartbeat_at: str | None = None
        self._shutdown_requested = False
        self._consecutive_empty_polls = 0

    @property
    def consumer_name(self) -> str:
        """Composite name used as the inbox dedup key's consumer component — one
        (event_id, consumer_name) pair may only be processed once, and group+consumer_id
        together identify which logical worker within the group this is."""
        return f"{self._config.group_name}:{self._config.consumer_id}"

    def heartbeat(self) -> str:
        self._last_heartbeat_at = _utc_now()
        return self._last_heartbeat_at

    @property
    def last_heartbeat_at(self) -> str | None:
        return self._last_heartbeat_at

    def request_shutdown(self) -> None:
        """Signal graceful shutdown. `poll_once` still finishes any batch already in
        flight; it just won't start a new one after this is set."""
        self._shutdown_requested = True

    @property
    def is_shutdown_requested(self) -> bool:
        return self._shutdown_requested

    def compute_backoff_seconds(self) -> float:
        """Exponential backoff on consecutive empty polls, capped at
        `idle_backoff_max_seconds`. Resets to base the moment a poll finds work."""
        backoff = self._config.idle_backoff_base_seconds * (2**self._consecutive_empty_polls)
        return min(backoff, self._config.idle_backoff_max_seconds)

    def poll_once(self) -> PollResult:
        if self._shutdown_requested:
            return PollResult(processed=[], empty=True)

        messages = self._transport.xreadgroup(count=self._config.batch_size)
        if not messages:
            self._consecutive_empty_polls += 1
            return PollResult(processed=[], empty=True)

        self._consecutive_empty_polls = 0
        results: list[ProcessedMessageResult] = []
        for message in messages:
            results.append(self._process_one(message))
        return PollResult(processed=results, empty=False)

    def _process_one(self, message: Any) -> ProcessedMessageResult:
        envelope = message.envelope
        event_id = envelope.get("event_id")
        if not event_id:
            raise ConsumerRunnerError(f"message {message.message_id} envelope missing event_id")

        inbox_row = self._inbox.receive_or_get(
            event_id=event_id, consumer_name=self.consumer_name, envelope=envelope
        )
        if getattr(inbox_row, "state", None) == "processed":
            # Already durably processed in a prior run (e.g. crash after mark_processed
            # but before xack) — ack the stream now and move on without re-invoking the
            # handler a second time.
            self._inbox.ack_stream(stream_message_id=message.message_id, inbox_row_id=inbox_row.id)
            self._transport.xack(message.message_id)
            return ProcessedMessageResult(message.message_id, "ALREADY_PROCESSED")

        lock_token = self._inbox.begin_processing(inbox_row.id)
        try:
            self._handler(envelope)
        except Exception:
            self._inbox.mark_failed(inbox_row.id, lock_token=lock_token)
            # Deliberately do not xack on handler failure: the message stays pending in
            # the stream so 001K-W06's reclaim/DLQ logic can pick it up.
            return ProcessedMessageResult(message.message_id, "HANDLER_FAILED")

        self._inbox.mark_processed(inbox_row.id, lock_token=lock_token)
        # commit-before-ack: xack only after mark_processed succeeded.
        self._inbox.ack_stream(stream_message_id=message.message_id, inbox_row_id=inbox_row.id)
        self._transport.xack(message.message_id)
        return ProcessedMessageResult(message.message_id, "PROCESSED")


__all__ = [
    "ConsumerRunnerError",
    "ConsumerRunnerConfig",
    "ProcessedMessageResult",
    "PollResult",
    "ConsumerRunner",
]
