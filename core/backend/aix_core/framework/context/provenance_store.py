"""F3-P2 Durable provenance projection — additive over ProvenanceHistory.

Default path is an in-process append-only event log that can rebuild intent text
for cold-restart tests. Optional ObjectiveCycleRepository hook is reserved for
future wiring behind ``AIX_FRAMEWORK_DURABLE_PROVENANCE`` (default false).
Does not change Objective Cycle behavior until that flag is wired.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.aix_core.framework.context.memory import (
    PROVENANCE_EVENT_INTENT,
    PROVENANCE_EVENT_PLAN,
    PROVENANCE_EVENT_TYPES,
    PROVENANCE_EVENT_UNDERSTANDING,
    ProvenanceEvent,
)
from backend.aix_core.runtime.objective.provenance.contracts import UserIntent
from backend.aix_core.runtime.objective.provenance.history import ProvenanceHistory


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class DurableProvenanceStore:
    """Append-only provenance events with replay into ``ProvenanceHistory``."""

    def __init__(self, repository: Any = None) -> None:
        self._repo = repository
        self._events: list[ProvenanceEvent] = []
        self._seq_by_objective: dict[str, int] = {}

    def events(self, objective_id: str | None = None) -> tuple[ProvenanceEvent, ...]:
        if objective_id is None:
            return tuple(self._events)
        oid = str(objective_id)
        return tuple(e for e in self._events if e.objective_id == oid)

    async def record_event(self, event: ProvenanceEvent) -> ProvenanceEvent:
        if event.event_type not in PROVENANCE_EVENT_TYPES:
            raise ValueError(f"unsupported provenance event_type: {event.event_type!r}")
        stored = event
        if stored.event_seq <= 0:
            nxt = self._seq_by_objective.get(stored.objective_id, 0) + 1
            stored = ProvenanceEvent(
                event_id=stored.event_id,
                objective_id=stored.objective_id,
                event_type=stored.event_type,
                payload=dict(stored.payload),
                event_seq=nxt,
                timestamp=stored.timestamp or _utc(),
                tenant_id=stored.tenant_id,
                cycle_id=stored.cycle_id,
            )
        self._seq_by_objective[stored.objective_id] = max(
            self._seq_by_objective.get(stored.objective_id, 0), stored.event_seq
        )
        self._events.append(stored)
        # Optional ledger bridge — only when a compatible repository is injected.
        if self._repo is not None and hasattr(self._repo, "append_event"):
            try:
                await self._repo.append_event(
                    tenant_id=stored.tenant_id or "framework",
                    aggregate_type="objective",
                    aggregate_id=stored.objective_id,
                    expected_sequence_number=stored.event_seq,
                    event_type=stored.event_type,
                    command_id=stored.event_id,
                    payload=stored.payload,
                )
            except Exception:
                # Fail closed for production wiring later; in-memory event remains authoritative
                # for the additive store until OC wiring is enabled.
                pass
        return stored

    async def record_intent(
        self,
        *,
        objective_id: str,
        intent_text: str,
        intent_id: str = "",
        tenant_id: str = "",
        cycle_id: str = "",
    ) -> ProvenanceEvent:
        return await self.record_event(
            ProvenanceEvent(
                objective_id=objective_id,
                event_type=PROVENANCE_EVENT_INTENT,
                payload={"intent_id": intent_id, "text": intent_text},
                event_seq=0,
                timestamp=_utc(),
                tenant_id=tenant_id,
                cycle_id=cycle_id,
            )
        )

    def record_intent_sync(
        self,
        *,
        objective_id: str,
        intent_text: str,
        intent_id: str = "",
        tenant_id: str = "",
        cycle_id: str = "",
    ) -> ProvenanceEvent:
        """Sync mirror for OC admit/freeze paths (no event loop required)."""
        event = ProvenanceEvent(
            objective_id=objective_id,
            event_type=PROVENANCE_EVENT_INTENT,
            payload={"intent_id": intent_id, "text": intent_text},
            event_seq=0,
            timestamp=_utc(),
            tenant_id=tenant_id,
            cycle_id=cycle_id,
        )
        if event.event_type not in PROVENANCE_EVENT_TYPES:
            raise ValueError(f"unsupported provenance event_type: {event.event_type!r}")
        stored = event
        if stored.event_seq <= 0:
            nxt = self._seq_by_objective.get(stored.objective_id, 0) + 1
            stored = ProvenanceEvent(
                event_id=stored.event_id,
                objective_id=stored.objective_id,
                event_type=stored.event_type,
                payload=dict(stored.payload),
                event_seq=nxt,
                timestamp=stored.timestamp or _utc(),
                tenant_id=stored.tenant_id,
                cycle_id=stored.cycle_id,
            )
        self._seq_by_objective[stored.objective_id] = max(
            self._seq_by_objective.get(stored.objective_id, 0), stored.event_seq
        )
        self._events.append(stored)
        return stored

    async def replay_history(self, objective_id: str) -> ProvenanceHistory:
        """Rebuild a ProvenanceHistory from recorded events (cold restart simulation)."""
        history = ProvenanceHistory()
        for evt in self.events(objective_id):
            if evt.event_type == PROVENANCE_EVENT_INTENT:
                text = str(evt.payload.get("text") or "")
                intent_id = str(evt.payload.get("intent_id") or evt.event_id)
                history.put_intent(
                    UserIntent(
                        id=intent_id,
                        objective_id=objective_id,
                        text=text,
                    )
                )
            # Understanding / plan payloads are recorded for forward compatibility;
            # full typed rehydration stays on the live OC path until flag wiring.
            elif evt.event_type in {PROVENANCE_EVENT_UNDERSTANDING, PROVENANCE_EVENT_PLAN}:
                continue
        return history


__all__ = ["DurableProvenanceStore"]
