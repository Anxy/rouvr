"""Verifier boundary: independent verdicts. Never executes repairs itself."""

from __future__ import annotations

from backend.aix_core.runtime.boundaries.log import verifier_log

_ALLOWED = frozenset({"PASS", "REPAIR", "REPLAN", "HALT", "REUNDERSTAND", "FAIL"})


def normalize_verifier_status(verdict: str) -> str:
    """Preserve engine verdicts on the public log surface."""
    raw = str(verdict or "").upper()
    return raw if raw in _ALLOWED else "FAIL"


def log_verifier_verdict(
    *,
    objective_id: str,
    verdict: str,
    plan_id: str | None = None,
    step_id: str | None = None,
    attempt: int | str | None = None,
    reason: str | None = None,
) -> str:
    status = normalize_verifier_status(verdict)
    verifier_log.emit(
        status,
        objective_id=objective_id,
        plan_id=plan_id,
        step_id=step_id,
        attempt=attempt,
        status=status,
        reason=reason or verdict,
        raw_verdict=verdict,
    )
    return status
