"""Scheduling gates: capacity admission + dependency wait/block (Stage 3).

- ``admit`` bounds concurrency by capacity and model quotas (pure).
- ``gate_status`` classifies one unit: ready (parents all SUCCEEDED),
  waiting (a parent still active), blocked (a parent terminally failed
  or cancelled — explicit failure gating, never silent).
  The production wait loop (poll/timeout/cancel) belongs to Temporal;
  this module gives it the pure decision function (unit-tested).
"""

from __future__ import annotations

ACTIVE_STATUSES = frozenset({"PENDING", "RUNNING", "ADMITTED"})
FAILED_STATUSES = frozenset({"FAILED", "CANCELLED", "TIMED_OUT"})


def admit(*, active_count: int, capacity: int, quota_left: int) -> bool:
    """True iff another assignment may start (pure, unit-tested)."""
    return active_count < capacity and quota_left > 0


def gate_status(
    units: dict[str, str],
    dependencies: list[tuple[str, str]],
    unit_id: str,
    *,
    ok_status: str = "SUCCEEDED",
) -> str:
    """'ready' | 'waiting' | 'blocked' | 'unknown-unit' (pure)."""
    if unit_id not in units:
        return "unknown-unit"
    parents = [p for child, p in dependencies if child == unit_id]
    if any(units.get(p) in FAILED_STATUSES for p in parents):
        return "blocked"
    if all(units.get(p) == ok_status for p in parents):
        return "ready"
    return "waiting"
