"""Operational surfaces: workers, queues, attempts, evidence (Stage 7).

Pure snapshot builders for the operating guide's status views. Inputs
are the shapes earlier stages already produce (locks, chain records,
verdicts, event effects); no I/O here. A deployment wires these to
live queries; this module fixes the shapes and the math (unit-tested).
"""

from __future__ import annotations

from typing import Any


def worker_snapshot(
    *,
    unit_id: str,
    lane_id: str,
    locks_owned: list[str],
    active_attempt: dict[str, Any] | None,
) -> dict[str, Any]:
    """One worker's live state (pure, unit-tested)."""
    return {
        "unit_id": unit_id,
        "lane_id": lane_id,
        "locks_owned": sorted(locks_owned),
        "active_attempt": active_attempt,
        "idle": active_attempt is None,
    }


def queue_snapshot(*, waiting: list[tuple[str, float]]) -> dict[str, Any]:
    """Queue delays from (unit_id, waited_seconds) pairs (pure)."""
    delays = sorted(waiting, key=lambda item: item[1], reverse=True)
    return {
        "depth": len(delays),
        "longest_waiter": delays[0][0] if delays else None,
        "longest_wait_seconds": delays[0][1] if delays else 0.0,
        "units": [uid for uid, _ in delays],
    }


def attempt_summary(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts by status + revision spread (pure, unit-tested)."""
    by_status: dict[str, int] = {}
    revisions: list[int] = []
    for attempt in attempts:
        status = str(attempt.get("status", "UNKNOWN"))
        by_status[status] = by_status.get(status, 0) + 1
        if isinstance(attempt.get("revision"), int):
            revisions.append(attempt["revision"])
    return {
        "total": len(attempts),
        "by_status": by_status,
        "max_revision": max(revisions) if revisions else 0,
    }
