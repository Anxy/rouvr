"""Tests for Cursor #81–90 — Temporal objective-cycle workflow + outbox/inbox/Memurai."""
from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.events import EVENT_SCHEMA_VERSION, EventSchemaError, build_event
from backend.aix_core.runtime.governance.experimental.memurai_streams import (
    InMemoryMemuraiStreamsTransport,
    MemuraiStreamsTransportGate,
)
from backend.aix_core.runtime.governance.experimental.pg_inbox import DurablePostgresInbox, PostgresInboxError
from backend.aix_core.runtime.governance.experimental.pg_outbox import TransactionalPostgresOutbox
from backend.workflows.temporal.objective_cycle_contracts import ObjectiveCycleWorkflowInput
from backend.workflows.temporal.objective_cycle_workflow import (
    AixObjectiveCycleWorkflowEngine,
    migrate_workflow_version,
    replay_history,
)


def _input(**overrides):
    base = dict(
        workflow_version=1,
        tenant_id="t1",
        objective_id="obj-1",
        cycle_id="cyc-1",
        candidate_sha="abcdef1",
        plan_sha256="a" * 64,
        start_request_id="req-1",
        correlation_id="corr-1",
    )
    base.update(overrides)
    return ObjectiveCycleWorkflowInput(**base)


def test_workflow_progresses_to_waiting_approval() -> None:
    engine = AixObjectiveCycleWorkflowEngine(_input())
    engine.run_until_wait_or_terminal()
    assert engine.state.phase == "WAITING_APPROVAL"
    status = engine.query_status()
    assert status["workhub_lifecycle_authority"] is False
    assert status["search_attributes"]["Phase"] == "WAITING_APPROVAL"


def test_approval_and_blocker_signals_resume_without_polling() -> None:
    engine = AixObjectiveCycleWorkflowEngine(_input())
    engine.run_until_wait_or_terminal()
    engine.signal_approval(approval_ref="apr-1", valid=True)
    assert engine.state.phase == "RUNNING_STEP"
    engine.wait_blocker()
    assert engine.state.phase == "WAITING_BLOCKER"
    engine.signal_blocker_resolved(blocker_ref="blk-1", resolved=True)
    assert engine.state.phase == "RUNNING_STEP"
    engine.continue_after_approval()
    assert engine.state.phase == "COMPLETED_FOR_REVIEW"


def test_invalid_approval_blocks() -> None:
    engine = AixObjectiveCycleWorkflowEngine(_input())
    engine.run_until_wait_or_terminal()
    engine.signal_approval(approval_ref="", valid=False)
    assert engine.state.phase == "BLOCKED"


def test_cancellation_blocks_further_dispatch() -> None:
    engine = AixObjectiveCycleWorkflowEngine(_input())
    engine.run_until_wait_or_terminal()
    engine.signal_cancel(reason="STOP")
    assert engine.state.phase == "CANCELLED"
    ok, reason = engine.may_dispatch_activity("dispatch_step")
    assert ok is False
    assert "CANCEL" in reason


def test_cancel_during_running_prevents_continue() -> None:
    engine = AixObjectiveCycleWorkflowEngine(_input())
    engine.run_until_wait_or_terminal()
    engine.signal_approval(approval_ref="apr-1")
    engine.signal_cancel(reason="MID")
    assert engine.state.phase == "CANCELLED"
    ok, _ = engine.may_dispatch_activity("verify_step")
    assert ok is False


def test_timeout_retry_heartbeat_policy() -> None:
    engine = AixObjectiveCycleWorkflowEngine(_input())
    dispatch = engine.activity_policy("dispatch_step")
    assert dispatch["heartbeat_timeout_seconds"] == 30
    assert dispatch["maximum_attempts"] == 1
    assert engine.classify_timeout("dispatch_step") == "FAIL_CLOSED_NO_RETRY"
    load = engine.activity_policy("load_cycle")
    assert engine.classify_timeout("load_cycle") == "RETRY_ALLOWED_WITHIN_MAX_ATTEMPTS"
    engine.heartbeat("ground_objective", "tick")
    assert engine.state.heartbeats[-1].activity_name == "ground_objective"
    assert dispatch["may_mutate_workhub_lifecycle"] is False


def test_query_search_visibility_omits_lifecycle() -> None:
    engine = AixObjectiveCycleWorkflowEngine(_input())
    engine.run_until_wait_or_terminal()
    q = engine.query_status()
    assert "workhub_status" not in q
    assert q["search_attributes"]["TenantId"] == "t1"
    assert q["search_attributes"]["CycleId"] == "cyc-1"


def test_replay_and_version_migration() -> None:
    engine = AixObjectiveCycleWorkflowEngine(_input(workflow_version=1))
    engine.run_until_wait_or_terminal()
    history = list(engine.state.history)
    replayed = replay_history(_input(workflow_version=1), history, worker_version=1)
    assert replayed.state.phase == "WAITING_APPROVAL"
    assert replayed.state.search_attributes["Replayed"] == "true"
    with pytest.raises(ValueError, match="INCOMPATIBLE"):
        replay_history(_input(workflow_version=2), history, worker_version=1)
    migrated = migrate_workflow_version(engine.state, to_version=2)
    assert migrated.workflow_version == 2
    with pytest.raises(ValueError, match="DOWNGRADE"):
        migrate_workflow_version(engine.state, to_version=1)


def test_event_taxonomy_registry() -> None:
    ev = build_event(
        event_id="e1",
        event_type="cycle.waiting_approval",
        tenant_id="t1",
        objective_id="o1",
        cycle_id="c1",
        correlation_id="corr",
        causation_id="cause",
        payload_refs={"ledger_ref": "pg://c1"},
    )
    assert ev.schema_version == EVENT_SCHEMA_VERSION
    assert ev.family == "cycle"
    with pytest.raises(EventSchemaError):
        build_event(
            event_id="e2",
            event_type="cycle.nope",
            tenant_id="t1",
            objective_id="o1",
            cycle_id="c1",
            correlation_id="c",
            causation_id="c",
        )
    with pytest.raises(EventSchemaError):
        build_event(
            event_id="e3",
            event_type="cycle.started",
            tenant_id="t1",
            objective_id="o1",
            cycle_id="c1",
            correlation_id="c",
            causation_id="c",
            payload_refs={"lifecycle_state": "accepted"},
        )


def test_postgres_outbox_publish_after_commit_lease_retry() -> None:
    outbox = TransactionalPostgresOutbox()
    outbox.begin()
    outbox.enqueue(tenant_id="t1", event_id="e1", envelope={"event_type": "cycle.started"})
    assert outbox.pending_count() == 0  # not visible until commit
    outbox.commit()
    assert outbox.pending_count() == 1
    claimed = outbox.claim_batch(worker_id="w1", limit=5)
    assert len(claimed) == 1
    outbox.mark_published(claimed[0].id, worker_id="w1")
    assert outbox.pending_count() == 0


def test_postgres_inbox_commit_before_ack() -> None:
    inbox = DurablePostgresInbox()
    row = inbox.receive_or_get(event_id="e1", consumer_name="c1", envelope={"x": 1})
    dup = inbox.receive_or_get(event_id="e1", consumer_name="c1", envelope={"x": 1})
    assert row.id == dup.id
    token = inbox.begin_processing(row.id)
    with pytest.raises(PostgresInboxError, match="ACK_BEFORE"):
        inbox.ack_stream(stream_message_id="1-1", inbox_row_id=row.id)
    inbox.mark_processed(row.id, lock_token=token)
    inbox.ack_stream(stream_message_id="1-1", inbox_row_id=row.id)
    assert inbox.was_acked("1-1")


def test_memurai_streams_capability_and_commands() -> None:
    gate = MemuraiStreamsTransportGate()
    ready = gate.probe(runtime_name="memurai", streams_enabled=True)
    assert ready.disposition == "READY"
    assert ready.stream_is_cycle_authority is False
    legacy = gate.probe(runtime_name="redis-3.0", streams_enabled=True)
    assert legacy.disposition == "UNSUPPORTED"
    missing = gate.probe(runtime_name="memurai", reported_commands={"XADD"}, streams_enabled=True)
    assert missing.disposition == "NOT_READY"
    transport = InMemoryMemuraiStreamsTransport()
    assert transport.readiness().disposition == "READY"
    mid = transport.xadd({"event_type": "cycle.started"})
    batch = transport.xreadgroup()
    assert batch[0].message_id == mid
    assert transport.xpending() == [mid]
    transport.xack(mid)
    assert transport.xpending() == []
