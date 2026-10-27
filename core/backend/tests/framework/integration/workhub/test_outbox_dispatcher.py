"""Tests for F8-P4 WorkHub Outbox Dispatcher."""

from __future__ import annotations

import pytest

from backend.aix_core.framework.integration.workhub.dispatcher import (
    DispatchReceipt,
    NoOpOutboxDispatcher,
    OutboxEvent,
    WorkHubOutboxDispatcher,
)


@pytest.mark.asyncio
async def test_noop_outbox_dispatcher():
    dispatcher = NoOpOutboxDispatcher()
    event = OutboxEvent(
        event_id="evt-100",
        topic="workhub.task.evidence_attached",
        payload={"task_number": 42},
        correlation_id="cid-100",
    )

    receipt = await dispatcher.dispatch(event)
    assert receipt.status == "NOOP_IGNORED"
    assert "disabled" in receipt.details["message"]
    assert receipt.event_id == "evt-100"


@pytest.mark.asyncio
async def test_workhub_outbox_dispatcher_deduplication():
    dispatcher = WorkHubOutboxDispatcher()
    event = OutboxEvent(
        event_id="evt-200",
        topic="workhub.task.verification_recorded",
        payload={"verdict": "PASSED"},
        correlation_id="cid-200",
    )

    receipt1 = await dispatcher.dispatch(event)
    assert receipt1.status == "DISPATCHED"
    assert receipt1.details["topic"] == "workhub.task.verification_recorded"

    # Repeated dispatch returns identical cached receipt
    receipt2 = await dispatcher.dispatch(event)
    assert receipt2 == receipt1
    assert receipt2.receipt_id == receipt1.receipt_id


def test_outbox_event_serialization():
    event = OutboxEvent(
        event_id="evt-300",
        topic="workhub.task.cycle_completed",
        payload={"status": "DONE"},
        correlation_id="cid-300",
    )
    data = event.to_dict()
    assert data["event_id"] == "evt-300"
    assert data["topic"] == "workhub.task.cycle_completed"
    assert data["correlation_id"] == "cid-300"
