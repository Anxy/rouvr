"""AIX-E2E-CYCLE-001M-W02: metrics and traces.

No real Prometheus/OpenTelemetry client is wired into this codebase's objective-cycle
path yet — this is an in-process recorder with the same shape (counters, gauges,
histograms, spans) a real exporter would sit behind, consistent with this package's
existing bounded-proof modules. `snapshot()` is the seam a later real exporter would
read from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from backend.aix_core.runtime.objective.ulid_compat import new_ulid
MetricKind = Literal["counter", "gauge", "histogram"]

# The telemetry axes named in the 001M-W02 outcome: latency, queue, budget, retry,
# failure, dependency, resource.
KNOWN_METRICS: frozenset[str] = frozenset(
    {
        "cycle_latency_seconds",
        "queue_depth",
        "budget_consumed_ratio",
        "retry_count",
        "failure_count",
        "dependency_latency_seconds",
        "resource_utilization_ratio",
    }
)


class MetricsError(ValueError):
    pass


class MetricRecorder:
    def __init__(self) -> None:
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}

    def _check_known(self, name: str) -> None:
        if name not in KNOWN_METRICS:
            raise MetricsError(f"unknown metric name: {name!r}; known: {sorted(KNOWN_METRICS)}")

    def increment_counter(self, name: str, *, by: float = 1.0) -> None:
        self._check_known(name)
        if by < 0:
            raise MetricsError("counter increments must be non-negative")
        self._counters[name] = self._counters.get(name, 0.0) + by

    def set_gauge(self, name: str, value: float) -> None:
        self._check_known(name)
        self._gauges[name] = value

    def observe_histogram(self, name: str, value: float) -> None:
        self._check_known(name)
        self._histograms.setdefault(name, []).append(value)

    def snapshot(self) -> dict[str, dict]:
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {name: list(values) for name, values in self._histograms.items()},
        }


@dataclass(frozen=True)
class TraceSpanV1:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    started_at_seconds: float
    ended_at_seconds: float | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self.ended_at_seconds is None:
            return None
        return self.ended_at_seconds - self.started_at_seconds


class TraceSpanError(ValueError):
    pass


def start_span(*, trace_id: str, name: str, started_at_seconds: float, parent_span_id: str | None = None) -> TraceSpanV1:
    return TraceSpanV1(
        trace_id=trace_id,
        span_id=new_ulid(),
        parent_span_id=parent_span_id,
        name=name,
        started_at_seconds=started_at_seconds,
    )


def end_span(span: TraceSpanV1, *, ended_at_seconds: float) -> TraceSpanV1:
    if ended_at_seconds < span.started_at_seconds:
        raise TraceSpanError("ended_at_seconds must not be before started_at_seconds")
    return TraceSpanV1(
        trace_id=span.trace_id,
        span_id=span.span_id,
        parent_span_id=span.parent_span_id,
        name=span.name,
        started_at_seconds=span.started_at_seconds,
        ended_at_seconds=ended_at_seconds,
    )


__all__ = [
    "MetricKind",
    "KNOWN_METRICS",
    "MetricsError",
    "MetricRecorder",
    "TraceSpanV1",
    "TraceSpanError",
    "start_span",
    "end_span",
]
