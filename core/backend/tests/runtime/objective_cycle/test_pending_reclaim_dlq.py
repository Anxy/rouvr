"""AIX-E2E-CYCLE-001K-W06 pending/reclaim/DLQ/replay tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.experimental.pending_reclaim_dlq import (
    POISON_THRESHOLD,
    DeadLetterStore,
    PendingReclaimCoordinator,
    PendingReclaimDlqError,
)


class TestRecordFailure:
    def test_below_threshold_stays_eligible_for_reclaim(self) -> None:
        coordinator = PendingReclaimCoordinator(dlq=DeadLetterStore())
        for _ in range(POISON_THRESHOLD - 1):
            result = coordinator.record_failure(message_id="m1", envelope={"event_id": "e1"}, error="boom")
            assert result is None
        assert coordinator.is_poisoned("m1") is False

    def test_reaching_threshold_quarantines_into_dlq(self) -> None:
        coordinator = PendingReclaimCoordinator(dlq=DeadLetterStore())
        entry = None
        for _ in range(POISON_THRESHOLD):
            entry = coordinator.record_failure(message_id="m1", envelope={"event_id": "e1"}, error="boom")
        assert entry is not None
        assert entry.failure_count == POISON_THRESHOLD
        assert coordinator.is_poisoned("m1") is True

    def test_custom_threshold_respected(self) -> None:
        coordinator = PendingReclaimCoordinator(dlq=DeadLetterStore(), poison_threshold=1)
        entry = coordinator.record_failure(message_id="m1", envelope={"event_id": "e1"}, error="boom")
        assert entry is not None

    def test_non_positive_threshold_rejected(self) -> None:
        with pytest.raises(PendingReclaimDlqError):
            PendingReclaimCoordinator(dlq=DeadLetterStore(), poison_threshold=0)


class TestEligibleForReclaim:
    def test_poisoned_messages_excluded_from_reclaim_candidates(self) -> None:
        coordinator = PendingReclaimCoordinator(dlq=DeadLetterStore(), poison_threshold=1)
        coordinator.record_failure(message_id="poisoned", envelope={"event_id": "e1"}, error="boom")
        eligible = coordinator.eligible_for_reclaim(["poisoned", "healthy-1", "healthy-2"])
        assert eligible == ["healthy-1", "healthy-2"]


class TestReplay:
    def test_replay_unknown_message_raises(self) -> None:
        coordinator = PendingReclaimCoordinator(dlq=DeadLetterStore())
        with pytest.raises(PendingReclaimDlqError):
            coordinator.replay("never-quarantined", transport_reinject=lambda envelope: None)

    def test_replay_reinjects_and_marks_replayed(self) -> None:
        coordinator = PendingReclaimCoordinator(dlq=DeadLetterStore(), poison_threshold=1)
        coordinator.record_failure(message_id="m1", envelope={"event_id": "e1", "x": 1}, error="boom")

        reinjected: list[dict] = []
        coordinator.replay("m1", transport_reinject=lambda envelope: reinjected.append(envelope))

        assert reinjected == [{"event_id": "e1", "x": 1}]
        assert coordinator.dlq.was_replayed("m1") is True

    def test_replay_is_never_automatic(self) -> None:
        # No method on PendingReclaimCoordinator other than replay() ever calls
        # transport_reinject or mark_replayed — record_failure and eligible_for_reclaim
        # only quarantine/filter, they never re-deliver anything themselves.
        coordinator = PendingReclaimCoordinator(dlq=DeadLetterStore(), poison_threshold=1)
        coordinator.record_failure(message_id="m1", envelope={"event_id": "e1"}, error="boom")
        assert coordinator.dlq.was_replayed("m1") is False


class TestDeadLetterStore:
    def test_all_entries_reflects_puts(self) -> None:
        store = DeadLetterStore()
        coordinator = PendingReclaimCoordinator(dlq=store, poison_threshold=1)
        coordinator.record_failure(message_id="m1", envelope={"event_id": "e1"}, error="boom")
        coordinator.record_failure(message_id="m2", envelope={"event_id": "e2"}, error="boom2")
        assert {entry.message_id for entry in store.all_entries()} == {"m1", "m2"}
