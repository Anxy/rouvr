from __future__ import annotations

from importlib import metadata
from importlib.util import find_spec
from typing import Any


def _probe_sdk(*, import_name: str, package_name: str) -> dict[str, Any]:
    installed = find_spec(import_name) is not None
    version: str | None = None
    if installed:
        try:
            version = metadata.version(package_name)
        except Exception:
            version = None
    return {
        "import_name": import_name,
        "package_name": package_name,
        "installed": installed,
        "version": version,
    }


def collect_orchestration_dependency_status(settings: object) -> dict[str, Any]:
    temporal = _probe_sdk(import_name="temporalio", package_name="temporalio")
    langgraph = _probe_sdk(import_name="langgraph", package_name="langgraph")
    haystack = _probe_sdk(import_name="haystack", package_name="haystack-ai")
    return {
        "strict_dependency_check": bool(getattr(settings, "ORCHESTRATION_STRICT_DEPENDENCY_CHECK", True)),
        "temporal": {
            "enabled": bool(getattr(settings, "TEMPORAL_ENABLED", False)),
            **temporal,
        },
        "langgraph": {
            "enabled": bool(getattr(settings, "LANGGRAPH_ENABLED", False)),
            **langgraph,
        },
        "haystack": {
            "enabled": bool(getattr(settings, "HAYSTACK_RETRIEVAL_ENABLED", False)),
            **haystack,
        },
    }


def enforce_orchestration_dependencies(*, settings: object, status: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = status or collect_orchestration_dependency_status(settings)
    strict = bool(resolved.get("strict_dependency_check", True))
    if not strict:
        return resolved

    missing: list[str] = []
    for key in ("temporal", "langgraph", "haystack"):
        detail = resolved.get(key, {})
        if bool(detail.get("enabled")) and not bool(detail.get("installed")):
            missing.append(str(detail.get("package_name") or key))
    if missing:
        missing_csv = ",".join(sorted(set(missing)))
        raise RuntimeError(
            f"ORCHESTRATION_DEPENDENCY_MISSING:{missing_csv}. "
            "Install pinned dependencies from backend/requirements.orchestration.txt"
        )
    return resolved


__all__ = ["collect_orchestration_dependency_status", "enforce_orchestration_dependencies"]
