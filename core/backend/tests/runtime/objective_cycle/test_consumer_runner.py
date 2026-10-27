"""AIX-E2E-CYCLE-001K-W05 consumer runner tests.

Integration-flavored: wired against the real `InMemoryMemuraiStreamsTransport`
(`memurai_streams.py`, 001K-W04) and `DurablePostgresInbox` (`pg_inbox.py`, 001K-W03) —
both built by another agent this session — rather than hand-rolled mocks, to prove this
module actually interoperates with what it was designed against, not just with an
idealized double of it.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.experimental.consumer_runner import (
    ConsumerRunner,
    ConsumerRunnerConfig,
    ConsumerRunnerError,
)
from backend.aix_core.runtime.objective.events import build_event, envelope_as_dict
from backend.aix_core.runtime.governance.experimental.memurai_streams import InMemoryMemuraiStreamsTransport
from backend.aix_core.runtime.governance.experimental.pg_inbox import DurablePostgresInbox


def _config(**overrides: object) -> ConsumerRunnerConfig:
    base = dict(group_name="cycle-events", consumer_id="worker-1")
    base.update(overrides)
    return ConsumerRunnerConfig(**base)


def _envelope(event_id: str = "evt-1") -> dict:
    event = build_event(
        event_id=event_id,
        event_type="step.verified",
        tenant_id="tenant-1",
        objective_id="objective-1",
        cycle_id="cycle-1",
        correlation_id="corr-1",
        causation_id="cause-1",
    )
    return envelope_as_dict(event)


class TestConsumerRunnerConfig:
    def test_missing_group_name_rejected(self) -> None:
        with pytest.raises(ConsumerRunnerError):
            ConsumerRunnerConfig(group_name="", consumer_id="w1")

    def test_non_positive_batch_size_rejected(self) -> None:
        with pytest.raises(ConsumerRunnerError):
            _config(batch_size=0)

    def test_backoff_base_exceeding_max_rejected(self) -> None:
        with pytest.raises(ConsumerRunnerError):
            _config(idle_backoff_base_seconds=10, idle_backoff_max_seconds=5)


class TestPollingAndProcessing:
    def _runner(self, handler) -> tuple[ConsumerRunner, InMemoryMemuraiStreamsTransport, DurablePostgresInbox]:
        transport = InMemoryMemuraiStreamsTransport()
        inbox = DurablePostgresInbox()
        runner = ConsumerRunner(config=_config(), transport=transport, inbox=inbox, handler=handler)
        return runner, transport, inbox

    def test_empty_poll_reports_empty_and_backs_off(self) -> None:
        runner, _transport, _inbox = self._runner(handler=lambda envelope: None)
        result = runner.poll_once()
        assert result.empty is True
        assert result.processed == []
        assert runner.compute_backoff_seconds() > _config().idle_backoff_base_seconds

    def test_successful_processing_marks_processed_and_acks_stream(self) -> None:
        seen: list[dict] = []
        runner, transport, inbox = self._runner(handler=lambda envelope: seen.append(envelope))
        transport.xadd(_envelope())

        result = runner.poll_once()
        assert len(result.processed) == 1
        assert result.processed[0].disposition == "PROCESSED"
        assert len(seen) == 1
        assert transport.xpending() == []  # acked, so no longer pending

    def test_handler_failure_leaves_message_pending_not_acked(self) -> None:
        def failing_handler(envelope: dict) -> None:
            raise RuntimeError("boom")

        runner, transport, _inbox = self._runner(handler=failing_handler)
        transport.xadd(_envelope())

        result = runner.poll_once()
        assert result.processed[0].disposition == "HANDLER_FAILED"
        assert transport.xpending() != []  # still pending for 001K-W06 to reclaim

    def test_duplicate_delivery_of_already_processed_event_does_not_reinvoke_handler(self) -> None:
        call_count = {"n": 0}

        def counting_handler(envelope: dict) -> None:
            call_count["n"] += 1

        runner, transport, inbox = self._runner(handler=counting_handler)
        envelope = _envelope()
        transport.xadd(envelope)
        runner.poll_once()
        assert call_count["n"] == 1

        # Simulate redelivery of the same logical event (same event_id) with a fresh
        # stream message id, as could happen after a crash/redelivery.
        transport.xadd(envelope)
        runner.poll_once()
        assert call_count["n"] == 1  # not re-invoked; inbox dedup caught it

    def test_shutdown_requested_prevents_new_batch(self) -> None:
        runner, transport, _inbox = self._runner(handler=lambda envelope: None)
        transport.xadd(_envelope())
        runner.request_shutdown()
        result = runner.poll_once()
        assert result.processed == []
        assert result.empty is True

    def test_consumer_name_combines_group_and_consumer_id(self) -> None:
        runner, _t, _i = self._runner(handler=lambda envelope: None)
        assert runner.consumer_name == "cycle-events:worker-1"

    def test_heartbeat_updates_timestamp(self) -> None:
        runner, _t, _i = self._runner(handler=lambda envelope: None)
        assert runner.last_heartbeat_at is None
        stamp = runner.heartbeat()
        assert runner.last_heartbeat_at == stamp


class TestBackoff:
    def test_backoff_grows_and_caps(self) -> None:
        runner, _transport, _inbox = TestPollingAndProcessing()._runner(handler=lambda envelope: None)
        seen = []
        for _ in range(10):
            runner.poll_once()  # always empty
            seen.append(runner.compute_backoff_seconds())
        assert seen == sorted(seen)  # monotonically non-decreasing
        assert seen[-1] <= _config().idle_backoff_max_seconds
