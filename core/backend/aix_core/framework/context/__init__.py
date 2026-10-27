"""F3 Context & Memory Framework — additive (flags default OFF).

``AIX_FRAMEWORK_DURABLE_PROVENANCE`` (default False) — not wired into OC.
Do not import the unwired experimental context capsule package.
"""

from __future__ import annotations

from backend.aix_core.framework.context.assembly import ContextAssemblyPipeline
from backend.aix_core.framework.context.compression import ContextCompressor
from backend.aix_core.framework.context.memory import (
    ContextBundle,
    MemoryClass,
    ProvenanceEvent,
    classify_memory_source,
)
from backend.aix_core.framework.context.provenance_store import DurableProvenanceStore


def durable_provenance_enabled(settings: object | None = None) -> bool:
    if settings is not None and hasattr(settings, "AIX_FRAMEWORK_DURABLE_PROVENANCE"):
        return bool(getattr(settings, "AIX_FRAMEWORK_DURABLE_PROVENANCE"))
    try:
        from backend.hyeve_aix_service.config import settings as _settings

        return bool(getattr(_settings, "AIX_FRAMEWORK_DURABLE_PROVENANCE", False))
    except Exception:
        return False


__all__ = [
    "ContextAssemblyPipeline",
    "ContextBundle",
    "ContextCompressor",
    "DurableProvenanceStore",
    "MemoryClass",
    "ProvenanceEvent",
    "classify_memory_source",
    "durable_provenance_enabled",
]
