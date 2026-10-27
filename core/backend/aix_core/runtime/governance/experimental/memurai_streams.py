"""001K-W04: Memurai Streams transport — capability, startup, readiness, required commands.

Wraps the existing Redis Streams transport contract (XADD/XGROUP/XREADGROUP/XACK/
XPENDING/XAUTOCLAIM) with an explicit Memurai capability gate. Does not treat
stream as cycle-state authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

REQUIRED_STREAM_COMMANDS = frozenset(
    {
        "XADD",
        "XGROUP",
        "XREADGROUP",
        "XACK",
        "XPENDING",
        "XAUTOCLAIM",
    }
)

TransportDisposition = Literal["READY", "NOT_READY", "UNSUPPORTED"]


@dataclass(frozen=True)
class MemuraiCapabilityReport:
    disposition: TransportDisposition
    runtime_name: str
    supports_streams: bool
    commands_present: tuple[str, ...]
    commands_missing: tuple[str, ...]
    reason_codes: tuple[str, ...]
    stream_is_cycle_authority: bool = False


class MemuraiStreamsTransportGate:
    """Startup/readiness gate for Memurai (or Redis-compatible Streams runtime)."""

    def probe(
        self,
        *,
        runtime_name: str = "memurai",
        reported_commands: set[str] | None = None,
        streams_enabled: bool = True,
    ) -> MemuraiCapabilityReport:
        commands = {c.upper() for c in (reported_commands or REQUIRED_STREAM_COMMANDS)}
        missing = tuple(sorted(REQUIRED_STREAM_COMMANDS - commands))
        if not streams_enabled:
            return MemuraiCapabilityReport(
                disposition="UNSUPPORTED",
                runtime_name=runtime_name,
                supports_streams=False,
                commands_present=tuple(sorted(commands & REQUIRED_STREAM_COMMANDS)),
                commands_missing=tuple(sorted(REQUIRED_STREAM_COMMANDS)),
                reason_codes=("STREAMS_DISABLED",),
            )
        if missing:
            return MemuraiCapabilityReport(
                disposition="NOT_READY",
                runtime_name=runtime_name,
                supports_streams=True,
                commands_present=tuple(sorted(commands & REQUIRED_STREAM_COMMANDS)),
                commands_missing=missing,
                reason_codes=("REQUIRED_COMMANDS_MISSING",),
            )
        if runtime_name.lower() in {"redis-3.0", "redis3", "legacy_redis_3"}:
            return MemuraiCapabilityReport(
                disposition="UNSUPPORTED",
                runtime_name=runtime_name,
                supports_streams=False,
                commands_present=(),
                commands_missing=tuple(sorted(REQUIRED_STREAM_COMMANDS)),
                reason_codes=("LEGACY_REDIS_3_FORBIDDEN",),
            )
        return MemuraiCapabilityReport(
            disposition="READY",
            runtime_name=runtime_name,
            supports_streams=True,
            commands_present=tuple(sorted(REQUIRED_STREAM_COMMANDS)),
            commands_missing=(),
            reason_codes=("MEMURAI_STREAMS_READY",),
            stream_is_cycle_authority=False,
        )


@dataclass
class InMemoryStreamMessage:
    message_id: str
    envelope: dict[str, Any]


class InMemoryMemuraiStreamsTransport:
    """Deterministic Streams stand-in for unit proof when Memurai is not attached."""

    def __init__(self) -> None:
        self._seq = 0
        self._stream: list[InMemoryStreamMessage] = []
        self._pending: dict[str, InMemoryStreamMessage] = {}
        self._acked: set[str] = set()
        self.gate = MemuraiStreamsTransportGate()

    def readiness(self) -> MemuraiCapabilityReport:
        return self.gate.probe(runtime_name="memurai-inmemory-standin", streams_enabled=True)

    def xadd(self, envelope: dict[str, Any]) -> str:
        self._seq += 1
        message_id = f"1-{self._seq}"
        self._stream.append(InMemoryStreamMessage(message_id, dict(envelope)))
        return message_id

    def xreadgroup(self, *, count: int = 10) -> list[InMemoryStreamMessage]:
        unread = [m for m in self._stream if m.message_id not in self._acked and m.message_id not in self._pending]
        batch = unread[:count]
        for msg in batch:
            self._pending[msg.message_id] = msg
        return batch

    def xack(self, message_id: str) -> None:
        self._pending.pop(message_id, None)
        self._acked.add(message_id)

    def xpending(self) -> list[str]:
        return list(self._pending.keys())

    def xautoclaim(self) -> list[InMemoryStreamMessage]:
        claimed = list(self._pending.values())
        return claimed
