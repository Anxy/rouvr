"""
Telemetry helpers consumed by generated services.

The generators emit lightweight instrumentation calls (`emit_metric`) so that
services can forward baseline metrics to whatever back-end is configured in the
environment.  The helper below purposely keeps the implementation dependency-
free and only upgrades to Prometheus counters when the optional dependency is
available.  Otherwise it simply logs the metric payload which keeps runtime
codepaths side-effect free.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping
from time import time
from typing import Any

_PrometheusCounter: type[Any] | None

try:  # pragma: no cover - optional dependency
    from prometheus_client import Counter as _PrometheusCounter  # type: ignore[import-untyped]
except Exception:  # pragma: no cover - optional dependency
    _PrometheusCounter = None

CounterType = type[Any]

LOGGER = logging.getLogger("hyeve_core.telemetry")
_counter_cls: CounterType | None = _PrometheusCounter

_COUNTER_CACHE: dict[tuple[str, tuple[str, ...]], Any] = {}
_CACHE_LOCK = threading.RLock()

__all__ = ["emit_metric"]


def emit_metric(
    name: str,
    tags: Mapping[str, Any] | None = None,
    *,
    value: float = 1.0,
) -> None:
    """
    Emit a counter metric for the given name/tags.

    The helper normalises names so that generated services can pass any metric
    label without worrying about Prometheus constraints.  When Prometheus is not
    installed (e.g., in unit tests), the metric is logged instead so calls never
    fail.
    """

    if not name:
        raise ValueError("Metric name must be provided")

    if os.getenv("HYEVE_TELEMETRY_DISABLED") == "1":
        return

    normalised_name = _normalise_metric_name(name)
    label_values = _normalise_tags(tags)

    if _counter_cls is None:
        LOGGER.debug(
            "metric_emitted",
            extra={
                "metric": normalised_name,
                "value": value,
                "tags": label_values,
                "timestamp": time(),
            },
        )
        return

    label_names = tuple(label_values.keys())
    cache_key = (normalised_name, label_names)

    with _CACHE_LOCK:
        counter = _COUNTER_CACHE.get(cache_key)
        if counter is None:
            counter = _counter_cls(
                normalised_name,
                f"Generated metric {normalised_name}",
                labelnames=label_names,
            )
            _COUNTER_CACHE[cache_key] = counter

    counter.labels(**label_values).inc(value)


def _normalise_metric_name(name: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name.strip())
    if not clean:
        clean = "metric"
    if clean[0].isdigit():
        clean = f"m_{clean}"
    return clean.lower()


def _normalise_tags(tags: Mapping[str, Any] | None) -> dict[str, str]:
    if not tags:
        return {}

    normalised: dict[str, str] = {}
    for raw_key, raw_value in tags.items():
        key = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(raw_key).strip())
        if not key:
            continue
        if key[0].isdigit():
            key = f"t_{key}"
        normalised[key.lower()] = "" if raw_value is None else str(raw_value)
    return normalised
