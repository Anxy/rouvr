import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.session_factory_router import router
from backend.infrastructure.temporal.runtime import TemporalRuntimeManager, TemporalSdk
from backend.session_factory.activation import MuseActivationCoordinator
from backend.session_factory.activation_runtime import (
    ActivationEventConsumer,
    AuthenticatedActivationPrincipal,
    CanonicalTemporalMuseStarter,
    ConfiguredReviewerAuthority,
    CoreActivationAuthenticator,
    PostgresActivationEventBridge,
)
from backend.session_factory.stores import (
    configure_activation_ingress,
    configure_binding_verifier,
)


async def _accept_authority(binding):
    return {"verified": True}


async def _accept_binding(binding):
    return dict(binding)


def binding(**overrides):
    values = dict(
        schema_version=1,
        tenant_id="tenant-1",
        project_id="lane-ops",
        task_id="task-1",
        task_number=23,
        assignment_id="assign-1",
        assignment_revision=2,
        executor_sub="worker-1",
        workhub_attempt_id="attempt-1",
        attempt_version=1,
        controller_attempt_id="canary-23-a1",
        lease_epoch=5,
        lease_holder="worker-1",
        contract_ref="workhub://tasks/23/execution-contracts/1",
        authority_ref="route-1",
        plan_digest="p" * 64,
        route_digest="r" * 64,
        selection={
            "tenant_id": "tenant-1",
            "project_id": "lane-ops",
            "task_id": "task-1",
            "task_number": 23,
            "objective_id": "objective-1",
            "cycle_id": "cycle-1",
            "selection_id": "selection-1",
            "mandate_id": "mandate-1",
            "session_id": "session-1",
            "worker_id": "worker-1",
            "candidate_sha": "0123456789abcdef",
            "plan_sha256": "a" * 64,
            "correlation_id": "correlation-1",
        },
    )
    values.update(overrides)
    return values


def _core(record):
    return {
        key: record.get(key) for key in (
            "tenant_id", "activation_id", "result_id", "worker_id", "status",
            "evidence_ref",
        )
    }


def _round(record):
    value = record.get("repair_round")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


class Store:
    def __init__(self):
        self.activations, self.verdicts, self.releases = {}, {}, {}
        self.attempt_owners = {}
        self.results = {}
        self.repairs, self.repair_by_verdict = {}, {}

    async def claim_activation(self, key, record):
        if key in self.activations:
            return False, dict(self.activations[key])
        self.activations[key] = dict(record)
        return True, dict(record)

    async def get_activation(self, key):
        return dict(self.activations[key]) if key in self.activations else None

    async def get_repair_count(self, key):
        return int(self.repairs.get(key, 0))

    async def find_attempt_activation(self, tenant_id, project_id, attempt_id):
        return sorted(
            key
            for key, row in self.activations.items()
            if isinstance(row.get("workhub_binding"), dict)
            and row["workhub_binding"].get("tenant_id") == tenant_id
            and row["workhub_binding"].get("project_id") == project_id
            and row["workhub_binding"].get("workhub_attempt_id") == attempt_id
        )

    async def claim_attempt_owner(self, tenant_id, project_id, attempt_id,
                                    activation_id):
        key = (tenant_id, project_id, attempt_id)
        owner = self.attempt_owners.setdefault(key, activation_id)
        return (owner == activation_id, owner)

    async def mark_activation_started(self, key, result):
        self.activations[key].update(status="STARTED", temporal_result=result)

    async def mark_activation_result(self, key, status, repair_round=None):
        if repair_round is None:
            current = self.activations[key].get("status")
            if current == "STARTED":
                self.activations[key]["status"] = status
            elif current != status:
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
            return
        if int(repair_round) != int(self.repairs.get(key, 0)):
            raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
        current = self.activations[key].get("status")
        if current == "STARTED" or current != status:
            self.activations[key]["status"] = status

    async def claim_verdict(self, key, record):
        if key in self.verdicts:
            return False, dict(self.verdicts[key])
        self.verdicts[key] = dict(record)
        return True, dict(record)

    async def claim_result(self, key, record):
        if key in self.results:
            stored = dict(self.results[key])
            if _core(stored) != _core(record):
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
            return False, stored
        current_round = int(self.repairs.get(record.get("activation_id"), 0))
        incoming_round = _round(record)
        if incoming_round is None:
            incoming_round = current_round
            record = {**record, "repair_round": incoming_round}
        if int(incoming_round) != int(current_round):
            raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
        rows = [
            row for row in self.results.values()
            if row.get("activation_id") == record.get("activation_id")
        ]
        legacy = [row for row in rows if _round(row) is None]
        if legacy:
            if (len(legacy) > 1 or any(
                    _core(row) != _core(legacy[0]) for row in legacy)):
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
            if int(incoming_round) == 0:
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
        for row in rows:
            if _round(row) is None:
                continue
            if int(_round(row)) != int(incoming_round):
                continue
            if _core(row) != _core(record):
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
        self.results[key] = dict(record)
        return True, dict(record)

    async def get_result(self, key):
        return dict(self.results[key]) if key in self.results else None

    async def get_result_by_activation(self, activation_id):
        current_round = int(self.repairs.get(activation_id, 0))
        matches = sorted(
            (dict(row) for row in self.results.values()
             if row.get("activation_id") == activation_id),
            key=lambda row: str(row.get("result_id") or ""),
        )
        if not matches:
            return None
        legacy = [row for row in matches if _round(row) is None]
        tagged = [row for row in matches if _round(row) is not None]
        if legacy:
            if (tagged or len(legacy) > 1 or any(
                    _core(row) != _core(legacy[0]) for row in legacy)):
                raise ValueError("ACTIVATION_RESULT_AMBIGUOUS")
            if int(current_round) != 0:
                return None
            return legacy[0]
        by_round: dict = {}
        for row in tagged:
            by_round.setdefault(int(_round(row)), []).append(row)
        for members in by_round.values():
            if any(_core(row) != _core(members[0]) for row in members[1:]):
                raise ValueError("ACTIVATION_RESULT_AMBIGUOUS")
        current = by_round.get(int(current_round))
        if not current:
            return None
        return current[0]

    async def claim_next_repair(self, activation_id, verdict_id):
        binding = (activation_id, verdict_id)
        if binding in self.repair_by_verdict:
            return "allocated", self.repair_by_verdict[binding]
        number = self.repairs.get(activation_id, 0) + 1
        if number > self.activations[activation_id]["max_repairs"]:
            return "exhausted", None
        self.repairs[activation_id] = number
        self.repair_by_verdict[binding] = number
        return "allocated", number

    async def claim_release(self, key, record):
        row = self.releases.get(key)
        if row and row["state"] == "completed":
            return "completed", dict(row), None
        if row and row["state"] == "claimed":
            return "busy", dict(row), None
        self.releases[key] = {**record, "state": "claimed"}
        return "acquired", dict(record), key + ":token"

    async def abandon_release(self, key, token):
        self.releases[key]["state"] = "retryable"

    async def mark_release_completed(self, key, token, result):
        self.releases[key].update(state="completed", release_result=result)


class Bridge:
    def __init__(self):
        self.rows: dict[str, dict[str, Any]] = {}
        self.outbox: dict[str, dict[str, Any]] = {}
        self.fail_ack_once = False
        self.claim_number = 0

    async def enqueue(self, event_id, kind, payload):
        row = self.rows.get(event_id)
        expected = {"event_kind": kind, "payload": payload, "state": "pending"}
        if row and (row["event_kind"] != kind or row["payload"] != payload):
            raise RuntimeError("ACTIVATION_EVENT_REPLAY_CONFLICT")
        if not row:
            self.rows[event_id] = expected

    async def claim(self):
        for event_id, row in self.rows.items():
            if row["state"] == "pending":
                row["state"] = "claimed"
                self.claim_number += 1
                row["lease_token"] = f"lease-{self.claim_number}"
                return {"event_id": event_id, **row}
        return None

    async def acknowledge(self, event_id, lease_token, result):
        if self.rows[event_id]["lease_token"] != lease_token:
            raise RuntimeError("ACTIVATION_EVENT_ACK_LEASE_LOST")
        if self.fail_ack_once:
            self.fail_ack_once = False
            raise RuntimeError("injected ack crash")
        self.rows[event_id].update(state="acked", result=result)

    async def retry(self, event_id, lease_token):
        if self.rows[event_id]["lease_token"] != lease_token:
            raise RuntimeError("ACTIVATION_EVENT_RETRY_LEASE_LOST")
        self.rows[event_id]["state"] = "pending"

    async def release(self, payload):
        key = payload["release_id"]
        if key in self.outbox and self.outbox[key] != payload:
            raise RuntimeError("CONTINUATION_RELEASE_REPLAY_CONFLICT")
        self.outbox[key] = dict(payload)
        return {"release_id": key, "receipt": f"continuation:{key}"}


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FencingConnection:
    def __init__(self):
        self.row = {
            "event_id": "event-1",
            "event_kind": "accepted_selection",
            "payload": {"event_id": "event-1"},
            "state": "pending",
            "lease_owner": None,
            "lease_token": None,
        }
        self.expired = False

    def transaction(self):
        return _Transaction()

    async def close(self):
        pass

    async def fetchrow(self, query, *args):
        if "FROM aix_activation_inbox WHERE state='pending'" in query:
            if self.row["state"] == "pending" or self.expired:
                return dict(self.row)
            return None
        raise AssertionError(query)

    async def execute(self, query, *args):
        if "SET state='claimed'" in query:
            self.row.update(state="claimed", lease_owner=args[1], lease_token=args[2])
            self.expired = False
            return "UPDATE 1"
        raise AssertionError(query)

    async def fetchval(self, query, *args):
        matches = (
            self.row["state"] == "claimed"
            and self.row["lease_owner"] == args[1]
            and self.row["lease_token"] == args[2]
        )
        if not matches:
            return None
        if "SET state='acked'" in query:
            self.row.update(state="acked", lease_owner=None, lease_token=None)
        elif "SET state='pending'" in query:
            self.row.update(state="pending", lease_owner=None, lease_token=None)
        else:
            raise AssertionError(query)
        return True


@dataclass
class TemporalSettings:
    TEMPORAL_ENABLED: bool = True
    TEMPORAL_AUTO_START_WORKER: bool = False
    TEMPORAL_ADDRESS: str = "127.0.0.1:7233"
    TEMPORAL_NAMESPACE: str = "hyeve-aix"
    TEMPORAL_TASK_QUEUE_PLAN: str = "aix.plan.current"
    TEMPORAL_TASK_QUEUE_EXECUTION: str = "aix.execution.current"
    TEMPORAL_TASK_QUEUE_VERIFICATION: str = "aix.verification.current"
    TEMPORAL_WORKER_IDENTITY: str = "hyeve-aix-worker"


def canonical_raw():
    return {
        "contract_version": "aix.objective-cycle.temporal-contract.v1",
        "workflow_version": 1,
        "tenant_id": "tenant-test",
        "objective_id": "objective-1",
        "cycle_id": "cycle-1",
        "candidate_sha": "abcdef1",
        "plan_sha256": "0" * 64,
        "start_request_id": "req-1",
        "correlation_id": "corr-1",
    }


def selection(event_id="selection-event-1", **change):
    value = {
        "event_id": event_id,
        "tenant_id": "tenant-1",
        "objective_id": "objective-1",
        "cycle_id": "cycle-1",
        "selection_id": "selection-1",
        "mandate_id": "mandate-1",
        "session_id": "session-1",
        "worker_id": "worker-1",
        "accepted": True,
        "workflow_version": 1,
        "candidate_sha": "0123456789abcdef",
        "plan_sha256": "a" * 64,
        "correlation_id": "correlation-1",
        "model": "Muse",
        "executor": "muse-lane",
        "max_repairs": 1,
        "workhub_binding": binding(),
    }
    value.update(change)
    return value


def verdict(activation_id, event_id, verdict_id, outcome):
    return {
        "event_id": event_id,
        "tenant_id": "tenant-1",
        "activation_id": activation_id,
        "verdict_id": verdict_id,
        "outcome": outcome,
        "worker_id": "worker-1",
        "reviewer_id": "reviewer-1",
        "evidence_ref": f"attempt-result:{event_id}",
        "result_id": f"result:{verdict_id}",
    }


def attempt_result(activation_id, verdict_packet):
    return {
        "event_id": f"result-event:{verdict_packet['verdict_id']}",
        "tenant_id": "tenant-1",
        "activation_id": activation_id,
        "result_id": verdict_packet["result_id"],
        "worker_id": "worker-1",
        "status": "SUCCEEDED",
        "evidence_ref": verdict_packet["evidence_ref"],
    }


def graph():
    store, bridge, starts = Store(), Bridge(), []

    async def canonical_start(*, raw):
        starts.append(raw)
        return {
            "workflow": "AixObjectiveCycleWorkflow",
            "workflow_id": "aix-cycle-tenant-1-cycle-1-d06258ebf3edc8c5",
            "run_id": "run-1",
            "task_queue": "aix.execution.current",
            "namespace": "default",
        }

    coordinator = MuseActivationCoordinator(
        store=store,
        temporal_start=CanonicalTemporalMuseStarter(canonical_start),
        release=bridge.release,
        reviewer_authority=ConfiguredReviewerAuthority(frozenset({"reviewer-1"})),
        verifier=_accept_authority,
    )
    return (
        store,
        bridge,
        starts,
        coordinator,
        ActivationEventConsumer(bridge, coordinator),
    )


@pytest.mark.asyncio
async def test_automatic_selection_crash_replay_and_pass_successor_exactly_once():
    store, bridge, starts, _, consumer = graph()
    packet = selection()
    await bridge.enqueue(packet["event_id"], "accepted_selection", packet)
    await bridge.enqueue(packet["event_id"], "accepted_selection", packet)
    bridge.fail_ack_once = True
    with pytest.raises(RuntimeError, match="ack crash"):
        await consumer.process_once()
    assert len(starts) == 1
    assert await consumer.process_once() is True
    assert len(starts) == 1
    activation_id = next(iter(store.activations))
    assert store.activations[activation_id]["temporal_result"][
        "muse_activation_receipt"
    ]

    result = verdict(activation_id, "verdict-event-1", "verdict-1", "pass")
    sdk_result = attempt_result(activation_id, result)
    await bridge.enqueue(sdk_result["event_id"], "attempt_result", sdk_result)
    await consumer.process_once()
    await bridge.enqueue(result["event_id"], "independent_verdict", result)
    await consumer.process_once()
    assert list(bridge.outbox) == [f"{activation_id}:successor"]
    assert all(row["state"] == "acked" for row in bridge.rows.values())


@pytest.mark.asyncio
async def test_distinct_negative_verdicts_release_one_repair_then_exhaust():
    store, bridge, _, _, consumer = graph()
    packet = selection()
    await bridge.enqueue(packet["event_id"], "accepted_selection", packet)
    await consumer.process_once()
    activation_id = next(iter(store.activations))
    for number in (1, 2):
        packet = verdict(
            activation_id, f"fail-event-{number}", f"fail-{number}", "fail"
        )
        sdk_result = attempt_result(activation_id, packet)
        if number == 2:
            # The second result answers the actual first release
            # (repair 1 from verdict fail-1), not the initial round.
            sdk_result = {
                **sdk_result,
                "repair_number": 1,
                "repair_verdict_id": "fail-1",
            }
        await bridge.enqueue(sdk_result["event_id"], "attempt_result", sdk_result)
        await consumer.process_once()
        await bridge.enqueue(packet["event_id"], "independent_verdict", packet)
        await consumer.process_once()
    assert list(bridge.outbox) == [f"{activation_id}:repair:1"]
    assert bridge.rows["fail-event-2"]["result"]["status"] == "BLOCKED"


@pytest.mark.asyncio
async def test_conflicting_selection_replay_fails_before_second_activation():
    _, bridge, _, _, _ = graph()
    packet = selection()
    await bridge.enqueue(packet["event_id"], "accepted_selection", packet)
    with pytest.raises(RuntimeError, match="REPLAY_CONFLICT"):
        await bridge.enqueue(
            packet["event_id"], "accepted_selection", selection(worker_id="other")
        )


@pytest.mark.asyncio
async def test_inbox_reacquisition_fences_stale_ack_and_retry():
    connection = FencingConnection()

    async def connect():
        return connection

    bridge = PostgresActivationEventBridge(
        "postgresql://example", owner="worker", connect=connect
    )
    first = await bridge.claim()
    connection.expired = True
    second = await bridge.claim()
    assert first["lease_token"] != second["lease_token"]
    with pytest.raises(RuntimeError, match="ACK_LEASE_LOST"):
        await bridge.acknowledge("event-1", first["lease_token"], {})
    with pytest.raises(RuntimeError, match="RETRY_LEASE_LOST"):
        await bridge.retry("event-1", first["lease_token"])
    await bridge.acknowledge("event-1", second["lease_token"], {"ok": True})
    assert connection.row["state"] == "acked"


def _reuse_sdk(*, status, decoded="raw", events="present", payload_count=1,
               workflow_type="AixObjectiveCycleWorkflow",
               original_run_id="run-existing",
               describe_run_id="run-existing", exc_run_id="run-existing",
               starts=None, handles=None, raise_error="typed",
               describe_error=None, no_events_method=False):
    """Faithful already-started double: real typed error, run pinning,
    real history protobufs, and the REAL data converter.

    Transport is faked (describe/history iterators) but the decoder is
    real: history carries genuine Payload protos decoded by
    DataConverter.default, so converter-shape assumptions are proven,
    not mirrored.
    """
    from temporalio.api.common.v1 import Payload, Payloads
    from temporalio.api.enums.v1 import EventType
    from temporalio.api.history.v1 import (
        HistoryEvent, WorkflowExecutionStartedEventAttributes,
    )
    from temporalio.converter import DataConverter
    from temporalio.exceptions import WorkflowAlreadyStartedError
    from backend.workflows.temporal.objective_cycle_contracts import (
        objective_cycle_workflow_id,
    )

    raw = canonical_raw()
    workflow_id = objective_cycle_workflow_id(
        tenant_id=raw["tenant_id"],
        objective_id=raw["objective_id"],
        cycle_id=raw["cycle_id"],
    )
    if decoded == "raw":
        decoded = raw
    starts = starts if starts is not None else []
    handles = handles if handles is not None else []
    consumed = []

    payload = Payload(metadata={"encoding": b"json/plain"},
                      data=json.dumps(dict(decoded)).encode())

    def _first(run_marker, event_id=1, started=True):
        return HistoryEvent(
            event_id=event_id,
            event_type=(EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED
                        if started else EventType.EVENT_TYPE_UNSPECIFIED),
            workflow_execution_started_event_attributes=(
                WorkflowExecutionStartedEventAttributes(
                    original_execution_run_id=run_marker,
                    input=Payloads(payloads=[payload] * payload_count),
                )
            ),
        )

    if events == "present":
        page = [_first(original_run_id)]
    elif events == "many":
        page = [_first(original_run_id)] + [
            HistoryEvent(event_id=number) for number in (2, 3, 4, 5)]
    elif events == "missing":
        page = []
    elif events == "bad_id":
        page = [_first(original_run_id, event_id=2)]
    else:
        page = [HistoryEvent(event_id=1)]

    class _Handle:
        async def describe(self):
            if describe_error is not None:
                raise describe_error
            return SimpleNamespace(
                id=workflow_id, run_id=describe_run_id,
                workflow_type=workflow_type,
                task_queue="aix.execution.current", status=status)

    if not no_events_method:
        def _fetch_history_events(handle_self, *, page_size=None, **kwargs):
            assert page_size == 1
            seen = consumed

            async def _gen():
                for event in page:
                    seen.append(event.event_id)
                    yield event

            return _gen()

        _Handle.fetch_history_events = _fetch_history_events

    class _Client:
        data_converter = DataConverter.default

        @classmethod
        async def connect(cls, _address, namespace):
            assert namespace == "hyeve-aix"
            return cls()

        async def start_workflow(self, *args, **kwargs):
            starts.append((args, kwargs))
            if raise_error == "typed":
                if exc_run_id is None:
                    raise WorkflowAlreadyStartedError(
                        workflow_id, "AixObjectiveCycleWorkflow")
                raise WorkflowAlreadyStartedError(
                    workflow_id, "AixObjectiveCycleWorkflow",
                    run_id=exc_run_id)
            raise raise_error

        def get_workflow_handle(self, wid, *, run_id=None):
            assert wid == workflow_id
            handles.append((wid, run_id))
            return _Handle()

    sdk = TemporalSdk(client_cls=_Client, worker_cls=object)
    return sdk, workflow_id, starts, handles, consumed


def _reuse_manager(sdk):
    return TemporalRuntimeManager(
        settings=TemporalSettings(), sdk_loader=lambda: sdk)


@pytest.mark.asyncio
async def test_temporal_already_started_recovers_with_verified_readback():
    from temporalio.api.enums.v1 import WorkflowExecutionStatus
    sdk, workflow_id, starts, handles, consumed = _reuse_sdk(
        status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING)
    result = await _reuse_manager(sdk).canonical_start(raw=canonical_raw())
    assert result["already_started"] is True
    assert result["run_id"] == "run-existing"
    assert result["workflow"] == "AixObjectiveCycleWorkflow"
    assert result["workflow_id"] == workflow_id
    assert len(starts) == 1
    assert handles == [(workflow_id, "run-existing")]
    assert consumed == [1]


@pytest.mark.asyncio
async def test_temporal_already_started_wrong_workflow_fails_closed():
    from temporalio.api.enums.v1 import WorkflowExecutionStatus
    sdk, _, starts, _, _ = _reuse_sdk(
        status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING,
        workflow_type="RetiredWorkflow")
    with pytest.raises(RuntimeError, match="IDENTITY_MISMATCH"):
        await _reuse_manager(sdk).canonical_start(raw=canonical_raw())
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_temporal_changed_input_refuses_adoption():
    from temporalio.api.enums.v1 import WorkflowExecutionStatus
    sdk, _, starts, handles, _ = _reuse_sdk(
        status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING)
    altered = dict(canonical_raw(), candidate_sha="c" * 40)
    with pytest.raises(RuntimeError, match="INPUT_CONFLICT"):
        await _reuse_manager(sdk).canonical_start(raw=altered)
    assert len(starts) == 1
    assert len(handles) == 1


@pytest.mark.asyncio
async def test_temporal_closed_exact_replay_refuses_terminal_without_new_start():
    from temporalio.api.enums.v1 import WorkflowExecutionStatus
    sdk, _, starts, handles, _ = _reuse_sdk(
        status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_COMPLETED)
    with pytest.raises(RuntimeError, match="ALREADY_TERMINAL"):
        await _reuse_manager(sdk).canonical_start(raw=canonical_raw())
    assert len(starts) == 1
    assert len(handles) == 1


@pytest.mark.asyncio
async def test_temporal_missing_history_refuses_without_start():
    from temporalio.api.enums.v1 import WorkflowExecutionStatus
    sdk, _, starts, _, _ = _reuse_sdk(
        status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING,
        events="missing")
    with pytest.raises(RuntimeError, match="HISTORY_UNREADABLE"):
        await _reuse_manager(sdk).canonical_start(raw=canonical_raw())
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_temporal_wrong_first_event_refuses_without_start():
    from temporalio.api.enums.v1 import WorkflowExecutionStatus
    sdk, _, starts, _, _ = _reuse_sdk(
        status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING,
        events="wrong_first")
    with pytest.raises(RuntimeError, match="HISTORY_UNREADABLE"):
        await _reuse_manager(sdk).canonical_start(raw=canonical_raw())
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_temporal_ambiguous_payloads_refuse_without_start():
    from temporalio.api.enums.v1 import WorkflowExecutionStatus
    sdk, _, starts, _, _ = _reuse_sdk(
        status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING,
        payload_count=2)
    with pytest.raises(RuntimeError, match="HISTORY_UNREADABLE"):
        await _reuse_manager(sdk).canonical_start(raw=canonical_raw())
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_temporal_undecodable_history_input_refuses_without_start():
    from temporalio.api.enums.v1 import WorkflowExecutionStatus
    sdk, _, starts, _, _ = _reuse_sdk(
        status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING,
        decoded={"not": "a workflow input"})
    with pytest.raises(RuntimeError, match="HISTORY_UNREADABLE"):
        await _reuse_manager(sdk).canonical_start(raw=canonical_raw())
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_temporal_unreadable_describe_refuses_without_start():
    from temporalio.api.enums.v1 import WorkflowExecutionStatus
    sdk, _, starts, _, _ = _reuse_sdk(
        status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING,
        describe_error=RuntimeError("transport down"))
    with pytest.raises(RuntimeError, match="UNREADABLE"):
        await _reuse_manager(sdk).canonical_start(raw=canonical_raw())
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_temporal_unrelated_same_name_error_propagates():
    from temporalio.api.enums.v1 import WorkflowExecutionStatus

    class WorkflowAlreadyStartedError(Exception):
        pass

    sdk, _, starts, handles, _ = _reuse_sdk(
        status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING,
        raise_error=WorkflowAlreadyStartedError())
    with pytest.raises(WorkflowAlreadyStartedError):
        await _reuse_manager(sdk).canonical_start(raw=canonical_raw())
    assert len(starts) == 1
    assert handles == []


@pytest.mark.asyncio
async def test_temporal_no_unsafe_type_error_fallback():
    from temporalio.api.enums.v1 import WorkflowExecutionStatus
    sdk, _, starts, handles, _ = _reuse_sdk(
        status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING,
        raise_error=TypeError("unexpected keyword argument 'id_reuse_policy'"))
    with pytest.raises(TypeError, match="id_reuse_policy"):
        await _reuse_manager(sdk).canonical_start(raw=canonical_raw())
    assert len(starts) == 1
    assert handles == []


@pytest.mark.asyncio
async def test_temporal_missing_history_events_surface_refuses():
    from temporalio.api.enums.v1 import WorkflowExecutionStatus
    sdk, _, starts, _ = _reuse_sdk(
        status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING,
        no_events_method=True)[:4]
    with pytest.raises(RuntimeError, match="SURFACE_UNSUPPORTED"):
        await _reuse_manager(sdk).canonical_start(raw=canonical_raw())
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_temporal_run_mismatch_refuses_without_combining_runs():
    from temporalio.api.enums.v1 import WorkflowExecutionStatus
    sdk, workflow_id, starts, handles, _ = _reuse_sdk(
        status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING,
        describe_run_id="run-other")
    with pytest.raises(RuntimeError, match="IDENTITY_MISMATCH"):
        await _reuse_manager(sdk).canonical_start(raw=canonical_raw())
    assert len(starts) == 1
    assert handles == [(workflow_id, "run-existing")]


@pytest.mark.asyncio
async def test_temporal_collision_without_run_id_resolves_then_pins():
    from temporalio.api.enums.v1 import WorkflowExecutionStatus
    sdk, workflow_id, starts, handles, consumed = _reuse_sdk(
        status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING,
        exc_run_id=None)
    result = await _reuse_manager(sdk).canonical_start(raw=canonical_raw())
    assert result["already_started"] is True
    assert result["run_id"] == "run-existing"
    assert handles == [(workflow_id, None), (workflow_id, "run-existing")]
    assert consumed == [1]
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_temporal_first_page_bounded_read_ignores_later_events():
    from temporalio.api.enums.v1 import WorkflowExecutionStatus
    sdk, _, starts, _, consumed = _reuse_sdk(
        status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING,
        events="many")
    result = await _reuse_manager(sdk).canonical_start(raw=canonical_raw())
    assert result["already_started"] is True
    assert result["run_id"] == "run-existing"
    assert consumed == [1]
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_temporal_wrong_event_id_refuses_without_start():
    from temporalio.api.enums.v1 import WorkflowExecutionStatus
    sdk, _, starts, _, _ = _reuse_sdk(
        status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING,
        events="bad_id")
    with pytest.raises(RuntimeError, match="HISTORY_UNREADABLE"):
        await _reuse_manager(sdk).canonical_start(raw=canonical_raw())
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_temporal_empty_start_receipt_refuses_ambiguous():
    from temporalio.common import (
        WorkflowIDConflictPolicy, WorkflowIDReusePolicy,
    )

    class _Handle:
        result_run_id = ""
        run_id = ""

    class _Client:
        @classmethod
        async def connect(cls, _address, namespace):
            return cls()

        async def start_workflow(self, *args, **kwargs):
            assert kwargs["id_reuse_policy"] is (
                WorkflowIDReusePolicy.REJECT_DUPLICATE)
            assert kwargs["id_conflict_policy"] is (
                WorkflowIDConflictPolicy.FAIL)
            return _Handle()

    sdk = TemporalSdk(client_cls=_Client, worker_cls=object)
    with pytest.raises(RuntimeError, match="RECEIPT_AMBIGUOUS"):
        await _reuse_manager(sdk).canonical_start(raw=canonical_raw())


@pytest.mark.asyncio
async def test_core_authenticator_uses_validated_claims(monkeypatch):
    observed = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"active": True, "tenant_id": "tenant-1", "sub": "worker-1"}

    class Client:
        def __init__(self, *, timeout):
            observed["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, json, headers=None):
            observed.update(url=url, json=json, headers=headers)
            return Response()

    monkeypatch.setattr("httpx.AsyncClient", Client)
    authenticate = CoreActivationAuthenticator(
        "https://auth.internal", timeout_seconds=3
    )
    principal = await authenticate("Bearer signed-token")
    assert principal == AuthenticatedActivationPrincipal("tenant-1", "worker-1")
    assert observed == {
        "timeout": 3,
        "url": "https://auth.internal/auth/validate",
        "json": {"token": "signed-token"},
        "headers": {},
    }


@pytest.mark.asyncio
async def test_core_authenticator_sends_tenant_hint(monkeypatch):
    import base64
    import json as _json

    observed = {}

    def _seg(obj):
        raw = _json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    hinted = _seg({"alg": "RS256"}) + "." + _seg({"tenant_id": "tenant-9"}) + ".sig"

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"active": True, "tenant_id": "tenant-9", "sub": "worker-9"}

    class Client:
        def __init__(self, *, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, json, headers=None):
            observed.update(headers=headers)
            return Response()

    monkeypatch.setattr("httpx.AsyncClient", Client)
    authenticate = CoreActivationAuthenticator("https://auth.internal")
    principal = await authenticate("Bearer " + hinted)
    assert principal == AuthenticatedActivationPrincipal("tenant-9", "worker-9")
    assert observed == {"headers": {"X-Hyeve-Tenant-Id": "tenant-9"}}
    with pytest.raises(ValueError, match="BEARER_REQUIRED"):
        await authenticate("signed-token")


def test_production_composition_serves_activation_route_instead_of_501():
    _, bridge, _, _, _ = graph()

    async def authenticate(_authorization):
        return AuthenticatedActivationPrincipal("tenant-1", "worker-1")

    app = FastAPI()
    app.include_router(router)
    configure_activation_ingress(app, bridge, authenticate)
    configure_binding_verifier(app, _accept_binding)
    body = selection()
    body.pop("event_id")
    response = TestClient(app).post(
        "/v1/aix/activation/selection",
        json=body,
        headers={"Authorization": "Bearer test"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "QUEUED"
    queued = next(iter(bridge.rows.values()))
    assert queued["event_kind"] == "accepted_selection"


def test_attempt_result_is_enqueued_and_identity_is_authenticated():
    _, bridge, _, _, _ = graph()

    async def authenticate(_authorization):
        return AuthenticatedActivationPrincipal("tenant-1", "worker-1")

    app = FastAPI()
    app.include_router(router)
    configure_activation_ingress(app, bridge, authenticate)
    configure_binding_verifier(app, _accept_binding)
    response = TestClient(app).post(
        "/v1/aix/activation/result",
        json={
            "tenant_id": "tenant-1",
            "activation_id": "activation-1",
            "result_id": "result-1",
            "worker_id": "worker-1",
            "status": "SUCCEEDED",
            "evidence_ref": "evidence:1",
        },
        headers={"Authorization": "Bearer signed"},
    )
    assert response.status_code == 202
    assert next(iter(bridge.rows.values()))["event_kind"] == "attempt_result"


def test_activation_ingress_rejects_unverified_and_body_identity_substitution():
    _, bridge, _, _, _ = graph()

    async def authenticate(authorization):
        if authorization == "Bearer forged":
            raise ValueError("ACTIVATION_AUTH_INVALID")
        return AuthenticatedActivationPrincipal("tenant-1", "worker-1")

    app = FastAPI()
    app.include_router(router)
    configure_activation_ingress(app, bridge, authenticate)
    configure_binding_verifier(app, _accept_binding)
    body = selection()
    body.pop("event_id")
    forged = TestClient(app).post(
        "/v1/aix/activation/selection",
        json=body,
        headers={"Authorization": "Bearer forged"},
    )
    body["tenant_id"] = "other-tenant"
    substituted = TestClient(app).post(
        "/v1/aix/activation/selection",
        json=body,
        headers={"Authorization": "Bearer signed"},
    )
    body["tenant_id"] = "tenant-1"
    body["worker_id"] = "body-selected-worker"
    actor_substituted = TestClient(app).post(
        "/v1/aix/activation/selection",
        json=body,
        headers={"Authorization": "Bearer signed"},
    )
    reviewer_substituted = TestClient(app).post(
        "/v1/aix/activation/verdict",
        json={
            "tenant_id": "tenant-1",
            "activation_id": "activation-1",
            "verdict_id": "verdict-1",
            "outcome": "pass",
            "worker_id": "worker-1",
            "reviewer_id": "body-selected-reviewer",
            "evidence_ref": "evidence:1",
            "result_id": "result-1",
        },
        headers={"Authorization": "Bearer signed"},
    )
    assert forged.status_code == 401
    assert substituted.status_code == 403
    assert actor_substituted.status_code == 403
    assert reviewer_substituted.status_code == 403
    assert bridge.rows == {}
