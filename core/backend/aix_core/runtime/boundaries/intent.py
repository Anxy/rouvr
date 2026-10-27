"""Intent boundary: objective understanding only. Does not plan or execute."""

from __future__ import annotations

from backend.aix_core.runtime.boundaries.log import intent_log


def log_intent_success(
    *,
    objective_id: str,
    cycle_id: str | None = None,
    evidence_id: str | None = None,
    reason: str = "understanding_complete",
) -> None:
    intent_log.emit(
        "success",
        objective_id=objective_id,
        status="SUCCESS",
        reason=reason,
        cycle_id=cycle_id,
        evidence_id=evidence_id,
    )
