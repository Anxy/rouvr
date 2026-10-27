"""P24: Learning & Optimization Store.

Uses VERIFIED P23 evaluation outcomes to improve planner strategies,
model routing, decomposition, and repair choices.

Critical constraints:
- Learning MUST have provenance (evaluation run_id)
- Learning MUST NOT modify authorization policy or grant capabilities
- Learning has confidence, scope, and rollback support
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal


@dataclass(frozen=True)
class LearningEntryV1:
    entry_id: str
    category: Literal["planner_strategy", "model_routing", "decomposition", "repair_choice"]
    insight: str
    provenance_run_id: str
    provenance_case_ids: tuple[str, ...] = ()
    confidence: float = 0.5
    scope: str = "global"
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )
    superseded_by: str | None = None


class LearningStore:
    """Stores verified learning entries. Cannot modify policy or grant capabilities."""

    def __init__(self) -> None:
        self._entries: dict[str, LearningEntryV1] = {}
        self._rolled_back: set[str] = set()

    def record(self, entry: LearningEntryV1) -> LearningEntryV1:
        if not entry.provenance_run_id:
            raise ValueError("learning entry must have provenance (evaluation run_id)")
        self._entries[entry.entry_id] = entry
        return entry

    def get(self, entry_id: str) -> LearningEntryV1 | None:
        if entry_id in self._rolled_back:
            return None
        return self._entries.get(entry_id)

    def query(self, *, category: str | None = None, min_confidence: float = 0.0) -> list[LearningEntryV1]:
        results = []
        for e in self._entries.values():
            if e.entry_id in self._rolled_back:
                continue
            if e.superseded_by is not None:
                continue
            if category and e.category != category:
                continue
            if e.confidence < min_confidence:
                continue
            results.append(e)
        return results

    def rollback(self, entry_id: str) -> bool:
        if entry_id not in self._entries:
            return False
        self._rolled_back.add(entry_id)
        return True

    def supersede(self, old_id: str, new_entry: LearningEntryV1) -> None:
        old = self._entries.get(old_id)
        if old is not None:
            self._entries[old_id] = LearningEntryV1(
                entry_id=old.entry_id, category=old.category,
                insight=old.insight, provenance_run_id=old.provenance_run_id,
                provenance_case_ids=old.provenance_case_ids,
                confidence=old.confidence, scope=old.scope,
                created_at=old.created_at, superseded_by=new_entry.entry_id,
            )
        self._entries[new_entry.entry_id] = new_entry


__all__ = ["LearningEntryV1", "LearningStore"]
