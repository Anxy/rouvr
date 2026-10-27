"""Executor boundary: runs a supplied StepContract / job. Does not plan."""

from __future__ import annotations

from backend.aix_core.runtime.boundaries.log import executor_log


def log_execution_started(
    *,
    objective_id: str,
    plan_id: str | None = None,
    step_id: str | None = None,
    attempt: int | str | None = None,
) -> None:
    executor_log.emit(
        "execution_started",
        objective_id=objective_id,
        plan_id=plan_id,
        step_id=step_id,
        attempt=attempt,
        status="STARTED",
    )


def log_execution_completed(
    *,
    objective_id: str,
    plan_id: str | None = None,
    step_id: str | None = None,
    attempt: int | str | None = None,
    status: str = "COMPLETED",
    reason: str | None = None,
    ok: bool | None = None,
) -> None:
    executor_log.emit(
        "execution_completed",
        objective_id=objective_id,
        plan_id=plan_id,
        step_id=step_id,
        attempt=attempt,
        status=status,
        reason=reason,
        ok=ok,
    )
