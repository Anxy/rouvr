"""Wave 5: harness-memory promotion ledger.

Memories graduate proposed -> accepted | deprecated, never edited in place.
Promotion (proposed -> accepted) requires a Lane approval id — the same rule
as ContractAmendment: AIX records, Lane approves. Deprecation requires a
reason. The log is append-only JSONL; readers reconstruct state by replay.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

PromotionState = Literal["proposed", "accepted", "deprecated"]
PromotionEvent = Literal["propose", "promote", "deprecate"]


class PromotionRejected(ValueError):
    """Raised when a promotion/deprecation lacks authority or basis."""


class MemoryPromotionRecord(BaseModel):
    record_id: str
    entry_id: str
    tenant_id: str
    event: PromotionEvent
    state: PromotionState
    evidence_refs: list[str] = Field(default_factory=list)
    lane_approval_id: str = ""
    reason: str = ""
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def propose_memory(
    *,
    entry_id: str,
    tenant_id: str,
    evidence_refs: list[str],
    reason: str = "",
) -> MemoryPromotionRecord:
    """Stage a memory for promotion. Proposal needs evidence, not approval."""
    if not str(entry_id or "").strip():
        raise PromotionRejected("entry_id is required")
    if not evidence_refs:
        raise PromotionRejected("promotion proposal requires at least one evidence ref")
    return MemoryPromotionRecord(
        record_id=f"promo-{entry_id}-{_utc_now()}",
        entry_id=str(entry_id).strip(),
        tenant_id=str(tenant_id or "").strip(),
        event="propose",
        state="proposed",
        evidence_refs=[str(ref) for ref in evidence_refs],
        reason=str(reason or ""),
    )


def promote_memory(
    *,
    record: MemoryPromotionRecord,
    lane_approval_id: str,
    reason: str = "",
) -> MemoryPromotionRecord:
    """Accept a proposed memory. Lane approval is mandatory — AIX cannot
    self-promote."""
    approval = str(lane_approval_id or "").strip()
    if not approval:
        raise PromotionRejected(
            "promotion requires a recorded lane_approval_id (AIX cannot self-promote)"
        )
    if record.state != "proposed":
        raise PromotionRejected(f"only proposed memories promote (state={record.state})")
    return MemoryPromotionRecord(
        record_id=f"{record.record_id}:promoted",
        entry_id=record.entry_id,
        tenant_id=record.tenant_id,
        event="promote",
        state="accepted",
        evidence_refs=list(record.evidence_refs),
        lane_approval_id=approval,
        reason=str(reason or ""),
    )


def deprecate_memory(
    *,
    record: MemoryPromotionRecord,
    reason: str,
) -> MemoryPromotionRecord:
    """Retire a memory with a reason. Never deletes history."""
    if not str(reason or "").strip():
        raise PromotionRejected("deprecation requires a reason")
    return MemoryPromotionRecord(
        record_id=f"{record.record_id}:deprecated",
        entry_id=record.entry_id,
        tenant_id=record.tenant_id,
        event="deprecate",
        state="deprecated",
        evidence_refs=list(record.evidence_refs),
        lane_approval_id=record.lane_approval_id,
        reason=str(reason).strip(),
    )


def append_promotion_record(*, log_path: str | Path, record: MemoryPromotionRecord) -> None:
    """Append-only JSONL writer. Only appends; never rewrites or removes."""
    path = Path(str(log_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.model_dump(mode="json")) + "\n")


def replay_promotion_state(*, log_path: str | Path) -> dict[str, MemoryPromotionRecord]:
    """Reconstruct latest state per entry_id by replaying the log in order."""
    path = Path(str(log_path))
    if not path.is_file():
        return {}
    state: dict[str, MemoryPromotionRecord] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or "entry_id" not in payload:
                continue
            try:
                state[str(payload["entry_id"])] = MemoryPromotionRecord(**payload)
            except ValueError:
                continue
    return state
