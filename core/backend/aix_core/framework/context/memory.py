"""F3-P1 Memory taxonomy — additive classification over live context/provenance assets."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def _uuid_str() -> str:
    return str(uuid.uuid4())


class MemoryClass(str, Enum):
    WORKING = "WORKING"
    EPISODIC = "EPISODIC"
    SEMANTIC = "SEMANTIC"
    PROCEDURAL = "PROCEDURAL"
    DURABLE_PROVENANCE = "DURABLE_PROVENANCE"


# Forward-only ledger event type names (F3-P2).
PROVENANCE_EVENT_INTENT = "provenance.intent_recorded"
PROVENANCE_EVENT_UNDERSTANDING = "provenance.understanding_recorded"
PROVENANCE_EVENT_PLAN = "provenance.plan_revision_recorded"

PROVENANCE_EVENT_TYPES = frozenset(
    {
        PROVENANCE_EVENT_INTENT,
        PROVENANCE_EVENT_UNDERSTANDING,
        PROVENANCE_EVENT_PLAN,
    }
)


@dataclass(frozen=True)
class ProvenanceEvent:
    objective_id: str
    event_type: str
    payload: dict[str, Any]
    event_seq: int
    timestamp: str
    event_id: str = field(default_factory=_uuid_str)
    tenant_id: str = ""
    cycle_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "objective_id": self.objective_id,
            "event_type": self.event_type,
            "payload": dict(self.payload),
            "event_seq": self.event_seq,
            "timestamp": self.timestamp,
            "tenant_id": self.tenant_id,
            "cycle_id": self.cycle_id,
        }


@dataclass(frozen=True)
class ContextBundle:
    """Assembled step context prior to optional compression."""

    step_id: str
    intent_text: str = ""
    target_files: tuple[str, ...] = ()
    symbol_definitions: tuple[str, ...] = ()
    previous_failure: str | None = None
    validation_guidelines: tuple[str, ...] = ()
    continuity_tokens: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    memory_classes: tuple[MemoryClass, ...] = ()
    token_count_estimate: int = 0
    bundle_id: str = field(default_factory=_uuid_str)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "step_id": self.step_id,
            "intent_text": self.intent_text,
            "target_files": list(self.target_files),
            "symbol_definitions": list(self.symbol_definitions),
            "previous_failure": self.previous_failure,
            "validation_guidelines": list(self.validation_guidelines),
            "continuity_tokens": list(self.continuity_tokens),
            "evidence_refs": list(self.evidence_refs),
            "memory_classes": [m.value for m in self.memory_classes],
            "token_count_estimate": self.token_count_estimate,
        }


def classify_memory_source(source: str) -> MemoryClass:
    """Map known live assets onto memory tiers (deterministic)."""
    key = str(source or "").strip().lower()
    mapping = {
        "working": MemoryClass.WORKING,
        "attempt": MemoryClass.EPISODIC,
        "episodic": MemoryClass.EPISODIC,
        "understanding": MemoryClass.SEMANTIC,
        "semantic": MemoryClass.SEMANTIC,
        "symbol": MemoryClass.SEMANTIC,
        "procedure": MemoryClass.PROCEDURAL,
        "procedural": MemoryClass.PROCEDURAL,
        "policy": MemoryClass.PROCEDURAL,
        "provenance": MemoryClass.DURABLE_PROVENANCE,
        "intent": MemoryClass.DURABLE_PROVENANCE,
        "ledger": MemoryClass.DURABLE_PROVENANCE,
    }
    for needle, klass in mapping.items():
        if needle in key:
            return klass
    return MemoryClass.WORKING


__all__ = [
    "ContextBundle",
    "MemoryClass",
    "PROVENANCE_EVENT_INTENT",
    "PROVENANCE_EVENT_PLAN",
    "PROVENANCE_EVENT_TYPES",
    "PROVENANCE_EVENT_UNDERSTANDING",
    "ProvenanceEvent",
    "classify_memory_source",
]
