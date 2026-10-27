"""EX2-S002: current-state Projection over the execution ledger (Packet 5 §2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.infrastructure.postgres.execution_kernel.repository import (
    ExecutionEvent,
    ExecutionKernelRepositoryError,
    ExecutionLedgerPort,
)


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


TERMINAL_COMPLETE = frozenset({"VERIFIED", "ARCHIVED"})


@dataclass
class ProjectionRow:
    tenant_id: str
    aggregate_type: str
    aggregate_id: str
    sequence_number: int
    status: str
    payload: dict[str, Any]
    parent_id: str | None = None
    state_current: bool = True


@dataclass
class InMemoryProjectionStore:
    """Denormalized current rows — never a source of truth; rebuilt from ledger.

    EX5-S9: list_children()/watermark() used to do a full O(N) scan of
    every row this store has ever held, across every tenant/aggregate —
    called once per candidate inside next_actionable_work_items(), that
    made large-Problem queries effectively O(N^2) (measured and documented
    in EX5-S4: ~0.3s at N=1000 for a dependency chain). _children_index and
    _tenant_watermark are maintained incrementally here so both queries
    are O(children) / O(1) instead of O(everything this store has ever
    held). No behavior changes — only complexity; see
    test_ex5_s9_performance_scale.py for a byte-for-byte before/after
    correctness comparison, not just a timing check.
    """

    _rows: dict[tuple[str, str, str], ProjectionRow] = field(default_factory=dict)
    _children_index: dict[tuple[str, str, str], set[str]] = field(default_factory=dict)
    _tenant_watermark: dict[str, int] = field(default_factory=dict)

    def apply_event(self, event: ExecutionEvent) -> ProjectionRow:
        tenant_id = _require_tenant(event.tenant_id)
        key = (tenant_id, event.aggregate_type, event.aggregate_id)
        payload = dict(event.payload)
        status = str(payload.get("status") or payload.get("phase") or "").strip() or "UNKNOWN"
        parent_id = payload.get("parent_id") or payload.get("problem_id") or payload.get("capability_id") or payload.get("project_id")
        if isinstance(parent_id, str):
            parent_id = parent_id.strip() or None
        else:
            parent_id = None
        row = ProjectionRow(
            tenant_id=tenant_id,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            sequence_number=event.sequence_number,
            status=status,
            payload=payload,
            parent_id=parent_id,
            state_current=True,
        )

        previous = self._rows.get(key)
        if previous is not None and previous.parent_id != parent_id:
            old_index_key = (tenant_id, event.aggregate_type, previous.parent_id)
            self._children_index.get(old_index_key, set()).discard(event.aggregate_id)
        if parent_id is not None:
            index_key = (tenant_id, event.aggregate_type, parent_id)
            self._children_index.setdefault(index_key, set()).add(event.aggregate_id)

        self._rows[key] = row
        current_watermark = self._tenant_watermark.get(tenant_id, 0)
        if event.sequence_number > current_watermark:
            self._tenant_watermark[tenant_id] = event.sequence_number
        return row

    def get(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> ProjectionRow | None:
        tenant_id = _require_tenant(tenant_id)
        return self._rows.get((tenant_id, aggregate_type, aggregate_id))

    def list_children(
        self, *, tenant_id: str, aggregate_type: str, parent_id: str
    ) -> list[ProjectionRow]:
        tenant_id = _require_tenant(tenant_id)
        child_ids = self._children_index.get((tenant_id, aggregate_type, parent_id), ())
        rows = (self._rows.get((tenant_id, aggregate_type, cid)) for cid in child_ids)
        return [row for row in rows if row is not None and row.state_current]

    def watermark(self, *, tenant_id: str) -> int:
        tenant_id = _require_tenant(tenant_id)
        return self._tenant_watermark.get(tenant_id, 0)

    async def rebuild_from_ledger(
        self,
        ledger: ExecutionLedgerPort,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
    ) -> ProjectionRow | None:
        tenant_id = _require_tenant(tenant_id)
        events = await ledger.load_events(
            tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id
        )
        if not events:
            self._rows.pop((tenant_id, aggregate_type, aggregate_id), None)
            return None
        row = None
        for event in events:
            row = self.apply_event(event)
        return row


__all__ = ["TERMINAL_COMPLETE", "ProjectionRow", "InMemoryProjectionStore"]
