"""
Telemetry utilities for Hyeve Core

Provides metrics and telemetry functionality for services.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_PrometheusCounter: type[Any] | None
try:  # pragma: no cover - optional dependency
    from prometheus_client import Counter as _PrometheusCounter
except Exception:  # pragma: no cover - optional dependency
    _PrometheusCounter = None

CounterType = type[Any]
_counter_cls: CounterType | None = _PrometheusCounter

_COUNTER_CACHE: dict[tuple[str, tuple[str, ...]], Any] = {}


def _normalize_token(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return normalized or "metric"


def _normalize_labels(tags: dict[str, Any] | None) -> tuple[tuple[str, ...], dict[str, Any]]:
    tags = tags or {}
    label_items: list[tuple[str, Any]] = []
    for key, value in tags.items():
        label_items.append((_normalize_token(str(key)), value))
    labelnames = tuple(key for key, _ in label_items)
    labels = {key: value for key, value in label_items}
    return labelnames, labels


def emit_metric(name: str, tags: dict[str, Any] | None = None, value: float = 1.0) -> None:
    """
    Emit a metric for monitoring and observability.

    Args:
        name: Metric name
        tags: Additional tags/labels for the metric
        value: Metric value (default: 1.0)
    """
    normalized_name = _normalize_token(name)
    labelnames, labels = _normalize_labels(tags)

    if _counter_cls is None:
        logger.debug(
            "metric_emitted name=%s value=%s labels=%s",
            normalized_name,
            value,
            labels,
        )
        return

    cache_key = (normalized_name, labelnames)
    counter = _COUNTER_CACHE.get(cache_key)
    if counter is None:
        counter = _counter_cls(normalized_name, "hyeve_metric", labelnames=labelnames)
        _COUNTER_CACHE[cache_key] = counter

    counter.labels(**labels).inc(value)


def increment_counter(name: str, tags: dict[str, Any] | None = None) -> None:
    """
    Increment a counter metric.

    Args:
        name: Counter name
        tags: Additional tags/labels for the counter
    """
    emit_metric(name, tags, 1.0)


def record_histogram(name: str, value: float, tags: dict[str, Any] | None = None) -> None:
    """
    Record a histogram value.

    Args:
        name: Histogram name
        value: Value to record
        tags: Additional tags/labels for the histogram
    """
    emit_metric(name, tags, value)
