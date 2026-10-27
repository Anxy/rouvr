"""Executor vs model resolution (Stage 1 core).

Executor = where work runs (e.g. ``muse-lane``). Model = which weights
(e.g. ``claude``). Capability rows (``aix_execution_capability_registry``
shape) carry ``executor_binding`` and optional ``model``. Supported
model IDs come from config — unknown capability stops with
``needs_classification`` (never a silent default); a model outside the
allowlist stops with ``model_not_allowed``.
"""

from __future__ import annotations

from typing import Any

DEFAULT_SUPPORTED_MODELS = frozenset({"Muse", "spark-1.3", "glimmer"})

EXECUTOR_DEFAULT_MODEL = {
    "muse-lane": "Muse",
    "spark-lane": "spark-1.3",
    "glimmer-lane": "glimmer",
}

_SUPPORTED: frozenset[str] = DEFAULT_SUPPORTED_MODELS


def configure_supported_models(models: set[str] | frozenset[str]) -> None:
    """Override the model allowlist (tests / deployment config)."""
    global _SUPPORTED
    _SUPPORTED = frozenset(models)


def reset_supported_models() -> None:
    global _SUPPORTED
    _SUPPORTED = DEFAULT_SUPPORTED_MODELS


def resolve_assignment(
    capability_rows: list[dict[str, Any]],
    capability_key: str,
) -> tuple[str, str | None, str | None]:
    """Resolve (status, executor, model) for a capability.

    ``"ok"`` with both set; ``"needs_classification"`` for unknown keys;
    ``"model_not_allowed"`` when the row's model is outside config.
    Pure: no I/O, unit-tested.
    """
    row = next(
        (
            r
            for r in capability_rows
            if r.get("capability_key") == capability_key
            and r.get("status", "ACTIVE") == "ACTIVE"
        ),
        None,
    )
    if row is None:
        return ("needs_classification", None, None)
    executor = str(row.get("executor_binding") or "")
    if not executor:
        return ("needs_classification", None, None)
    model = str(row.get("model") or EXECUTOR_DEFAULT_MODEL.get(executor) or "")
    if not model or model not in _SUPPORTED:
        return ("model_not_allowed", executor, None)
    return ("ok", executor, model)
