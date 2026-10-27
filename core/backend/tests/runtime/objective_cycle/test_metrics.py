"""AIX-E2E-CYCLE-001M-W02 metrics and traces tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.experimental.metrics import (
    MetricRecorder,
    MetricsError,
    TraceSpanError,
    end_span,
    start_span,
)


class TestMetricRecorder:
    def test_unknown_metric_rejected(self) -> None:
        recorder = MetricRecorder()
        with pytest.raises(MetricsError):
            recorder.increment_counter("made_up_metric")

    def test_counter_accumulates(self) -> None:
        recorder = MetricRecorder()
        recorder.increment_counter("retry_count")
        recorder.increment_counter("retry_count", by=2)
        assert recorder.snapshot()["counters"]["retry_count"] == 3

    def test_negative_counter_increment_rejected(self) -> None:
        recorder = MetricRecorder()
        with pytest.raises(MetricsError):
            recorder.increment_counter("retry_count", by=-1)

    def test_gauge_overwrites(self) -> None:
        recorder = MetricRecorder()
        recorder.set_gauge("queue_depth", 5)
        recorder.set_gauge("queue_depth", 3)
        assert recorder.snapshot()["gauges"]["queue_depth"] == 3

    def test_histogram_accumulates_observations(self) -> None:
        recorder = MetricRecorder()
        recorder.observe_histogram("cycle_latency_seconds", 1.2)
        recorder.observe_histogram("cycle_latency_seconds", 3.4)
        assert recorder.snapshot()["histograms"]["cycle_latency_seconds"] == [1.2, 3.4]

    def test_snapshot_is_a_copy_not_a_live_view(self) -> None:
        recorder = MetricRecorder()
        recorder.increment_counter("retry_count")
        snap = recorder.snapshot()
        recorder.increment_counter("retry_count")
        assert snap["counters"]["retry_count"] == 1


class TestTraceSpans:
    def test_start_span_has_no_duration_until_ended(self) -> None:
        span = start_span(trace_id="trace-1", name="step-execution", started_at_seconds=100.0)
        assert span.duration_seconds is None

    def test_end_span_computes_duration(self) -> None:
        span = start_span(trace_id="trace-1", name="step-execution", started_at_seconds=100.0)
        ended = end_span(span, ended_at_seconds=102.5)
        assert ended.duration_seconds == 2.5

    def test_end_before_start_rejected(self) -> None:
        span = start_span(trace_id="trace-1", name="step-execution", started_at_seconds=100.0)
        with pytest.raises(TraceSpanError):
            end_span(span, ended_at_seconds=99.0)

    def test_span_preserves_parent_relationship(self) -> None:
        parent = start_span(trace_id="trace-1", name="cycle", started_at_seconds=0.0)
        child = start_span(trace_id="trace-1", name="step", started_at_seconds=1.0, parent_span_id=parent.span_id)
        assert child.parent_span_id == parent.span_id
