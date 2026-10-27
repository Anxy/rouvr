"""Structured logs for the five AIX internal boundaries.

Emits to:
1. Python logger ``aix.boundary.<name>`` (normal service logging)
2. stdout (so console / process tees always see the line)
3. a dedicated UTF-8 file (so boundary watcher terminals stay reliable)

Watcher terminals must read the dedicated file — PowerShell ``Tee-Object`` often
writes UTF-16, which breaks ``Get-Content -Wait`` filters for ``[aix.*]``.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_PREFIX = {
    "intent": "[aix.intent]",
    "planner": "[aix.planner]",
    "dispatch": "[aix.dispatch]",
    "executor": "[aix.executor]",
    "verifier": "[aix.verifier]",
}

_FILE_LOCK = threading.Lock()
_DEFAULT_BOUNDARY_LOG = Path(r"D:\v2.1\AIX\.tmp\aix-boundaries.log")


def boundary_log_path() -> Path:
    raw = str(os.environ.get("HYEVE_AIX_BOUNDARY_LOG") or "").strip()
    if raw:
        return Path(raw)
    return _DEFAULT_BOUNDARY_LOG


def _format_line(
    boundary: str,
    *,
    event: str,
    objective_id: str | None = None,
    plan_id: str | None = None,
    step_id: str | None = None,
    attempt: int | str | None = None,
    status: str | None = None,
    reason: str | None = None,
    **extra: Any,
) -> str:
    prefix = _PREFIX.get(boundary, f"[aix.{boundary}]")
    parts = [prefix, f"event={event}"]
    if objective_id:
        parts.append(f"objective_id={objective_id}")
    if plan_id:
        parts.append(f"plan_id={plan_id}")
    if step_id:
        parts.append(f"step_id={step_id}")
    if attempt is not None and attempt != "":
        parts.append(f"attempt={attempt}")
    if status:
        parts.append(f"status={status}")
    if reason:
        parts.append(f"reason={reason}")
    for key, value in extra.items():
        if value is None or value == "":
            continue
        parts.append(f"{key}={value}")
    return " ".join(parts)


def _append_utf8(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _FILE_LOCK:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()


def boundary_log(
    boundary: str,
    *,
    event: str,
    objective_id: str | None = None,
    plan_id: str | None = None,
    step_id: str | None = None,
    attempt: int | str | None = None,
    status: str | None = None,
    reason: str | None = None,
    level: int = logging.INFO,
    **extra: Any,
) -> None:
    """Emit one structured boundary line. Missing ids are omitted, not invented."""
    message = _format_line(
        boundary,
        event=event,
        objective_id=objective_id,
        plan_id=plan_id,
        step_id=step_id,
        attempt=attempt,
        status=status,
        reason=reason,
        **extra,
    )
    logging.getLogger(f"aix.boundary.{boundary}").log(level, message)
    # Always visible on the AIX console / process tee.
    try:
        print(message, flush=True, file=sys.stderr)
    except Exception:
        pass
    # Dedicated UTF-8 file for the 6 watcher windows.
    try:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        _append_utf8(boundary_log_path(), f"{ts} {message}")
    except Exception:
        pass


class BoundaryLogger:
    """Thin typed helper so call sites stay short."""

    def __init__(self, boundary: str) -> None:
        self.boundary = boundary

    def emit(self, event: str, **fields: Any) -> None:
        boundary_log(self.boundary, event=event, **fields)


intent_log = BoundaryLogger("intent")
planner_log = BoundaryLogger("planner")
dispatch_log = BoundaryLogger("dispatch")
executor_log = BoundaryLogger("executor")
verifier_log = BoundaryLogger("verifier")
