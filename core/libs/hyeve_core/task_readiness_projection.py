"""Temporary compatibility re-exports for orchestration readiness projection."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from hyeve_task_contract_engine.readiness_projection import (
        TaskReadinessProjection,
        build_task_readiness_projection,
        derive_control_plane_handoff_status,
        sync_task_readiness_metadata,
    )
except ModuleNotFoundError:
    _HYEVE_ROOT = Path(__file__).resolve().parents[1]
    _ENGINE_PACKAGE_ROOT = _HYEVE_ROOT / "libs" / "hyeve_task_contract_engine"
    if str(_ENGINE_PACKAGE_ROOT) not in sys.path:
        sys.path.insert(0, str(_ENGINE_PACKAGE_ROOT))
    from hyeve_task_contract_engine.readiness_projection import (
        TaskReadinessProjection,
        build_task_readiness_projection,
        derive_control_plane_handoff_status,
        sync_task_readiness_metadata,
    )

__all__ = [
    "TaskReadinessProjection",
    "build_task_readiness_projection",
    "derive_control_plane_handoff_status",
    "sync_task_readiness_metadata",
]
