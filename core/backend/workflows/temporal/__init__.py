"""Temporal orchestration bootstrap package for AIX."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORT_MAP = {
    "AixTemporalActivities": (".activities", "AixTemporalActivities"),
    "QueueBinding": (".queue_strategy", "QueueBinding"),
    "TemporalRuntimeManager": (".runtime", "TemporalRuntimeManager"),
    "TemporalWorkflowStateMachine": (".workflows", "TemporalWorkflowStateMachine"),
    "select_task_queue": (".queue_strategy", "select_task_queue"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORT_MAP.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


__all__ = list(_EXPORT_MAP)
