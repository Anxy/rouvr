"""F1 Work Framework package — additive adapters over runtime contracts.

Feature flag: ``AIX_FRAMEWORK_WORK_ADAPTERS`` (default False).
When disabled, consumers must not change Objective Cycle behavior.
"""

from __future__ import annotations

from backend.aix_core.framework.work.models import (
    ExternalWorkRef,
    UnitStatus,
    Work,
    WorkContract,
    WorkDefinition,
    WorkGraph,
    WorkStatus,
    WorkType,
    WorkUnit,
)


def work_adapters_enabled(settings: object | None = None) -> bool:
    """Return True only when AIX_FRAMEWORK_WORK_ADAPTERS is explicitly enabled."""
    if settings is not None and hasattr(settings, "AIX_FRAMEWORK_WORK_ADAPTERS"):
        return bool(getattr(settings, "AIX_FRAMEWORK_WORK_ADAPTERS"))
    try:
        from backend.hyeve_aix_service.config import settings as _settings

        return bool(getattr(_settings, "AIX_FRAMEWORK_WORK_ADAPTERS", False))
    except Exception:
        return False


__all__ = [
    "Work",
    "WorkType",
    "WorkDefinition",
    "WorkUnit",
    "WorkContract",
    "WorkGraph",
    "ExternalWorkRef",
    "WorkStatus",
    "UnitStatus",
    "work_adapters_enabled",
]
