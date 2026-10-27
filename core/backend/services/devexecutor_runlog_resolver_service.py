from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Final

from backend.hyeve_aix_service.config import settings

RUNLOG_ROOT_ENV: Final[str] = "AIX_RUNLOG_ROOT"
DEFAULT_LOCAL_RUNLOG_ROOT: Final[Path] = Path("/app/service/_runlogs")
DEFAULT_FALLBACK_RUNLOG_ROOT_NAME: Final[str] = "hyeve-aix-runlogs"


def _absolute_root(root_str: str) -> Path:
    root = Path(root_str).expanduser()
    if not root.is_absolute():
        raise ValueError(f"{RUNLOG_ROOT_ENV} must be an absolute path: {root_str}")
    return root.resolve(strict=False)


def _materialize_root(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return root


def _fallback_runlog_root() -> Path:
    return Path(tempfile.gettempdir()) / DEFAULT_FALLBACK_RUNLOG_ROOT_NAME


def get_runlog_root() -> Path:
    configured = str(getattr(settings, "AIX_RUNLOG_ROOT", "") or "").strip()
    if configured:
        return _materialize_root(_absolute_root(configured))
    default_root = DEFAULT_LOCAL_RUNLOG_ROOT.resolve(strict=False)
    try:
        return _materialize_root(default_root)
    except OSError:
        return _materialize_root(_fallback_runlog_root().resolve(strict=False))


def get_planner_trace_root() -> Path:
    root = get_runlog_root() / "planner-traces"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_run_ledger_root() -> Path:
    root = get_runlog_root() / "aix-run-ledger"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_outcome_policy_trace_root() -> Path:
    root = get_runlog_root() / "aix-outcome-policy-traces"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_execution_phase_ledger_root() -> Path:
    root = get_runlog_root() / "aix-execution-phase-ledger"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_rewrite_trace_root() -> Path:
    root = get_runlog_root() / "rewrite-traces"
    root.mkdir(parents=True, exist_ok=True)
    return root
