"""C011: event replay + decision-state reconstruction tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.durable_aggregate.replay import (
    DECISION_STATE_SCHEMA,
    REDUCER_INVARIANTS,
    AggregateSnapshot,
    InMemoryProjectionStore,
    ReplayError,
    decision_bytes,
    decision_state_objective,
    decision_state_run,
    fold_events,
    reconstruct_decision_pair,
    validate_snapshot_envelope,
    wrap_objective_snapshot,
    wrap_run_snapshot,
)
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


def test_reducer_invariants_documented() -> None:
    assert len(REDUCER_INVARIANTS) >= 5
    assert any("workspace" in inv.lower() for inv in REDUCER_INVARIANTS)


def test_unknown_schema_fails_closed() -> None:
    with pytest.raises(ReplayError) as exc:
        validate_snapshot_envelope({"schema_version": "v0", "kind": "run", "decision": {}})
    assert exc.value.code == "UNKNOWN_DECISION_SCHEMA"


def test_fold_events_gap_fails_closed() -> None:
    with pytest.raises(ReplayError) as exc:
        fold_events({"phase": "STARTING"}, [{"sequence_number": 2, "payload": {"phase": "PLANNING"}}])
    assert exc.value.code == "CORRUPT_EVENT_STREAM"


def test_fold_events_duplicate_fails_closed() -> None:
    events = [
        {"sequence_number": 1, "payload": {"decision_patch": {"phase": "GROUNDING"}}},
        {"sequence_number": 1, "payload": {"decision_patch": {"phase": "PLANNING"}}},
    ]
    with pytest.raises(ReplayError) as exc:
        fold_events(None, events)
    assert exc.value.code == "CORRUPT_EVENT_STREAM"


def test_fold_events_contiguous_patches() -> None:
    events = [
        {"sequence_number": 1, "payload": {"decision": {"phase": "GROUNDING", "version": 1}}},
        {"sequence_number": 2, "payload": {"decision_patch": {"phase": "WAITING_APPROVAL", "version": 2}}},
    ]
    state = fold_events(None, events)
    assert state["phase"] == "WAITING_APPROVAL"
    assert state["version"] == 2


def test_decision_bytes_stable() -> None:
    a = decision_state_run(
        {
            "cycle_id": "cyc_1",
            "objective_id": "obj_1",
            "tenant_id": "t1",
            "budgets": {"b": 2, "a": 1},
            "phase": "WAITING_APPROVAL",
        }
    )
    b = decision_state_run(
        {
            "cycle_id": "cyc_1",
            "objective_id": "obj_1",
            "tenant_id": "t1",
            "budgets": {"a": 1, "b": 2},
            "phase": "WAITING_APPROVAL",
        }
    )
    assert decision_bytes(a) == decision_bytes(b)


@pytest.mark.asyncio
async def test_fresh_backend_reconstructs_byte_equivalent_decision_state() -> None:
    store = InMemoryProjectionStore()
    backend1 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=store,
    )
    admit = await backend1.command(
        operation="admit_objective",
        tenant_id="tenant-c011",
        actor_id="actor-1",
        resource_id=None,
        body={
            "objective": "Prove C011 restart reconstruction",
            "task_class": "bounded_repair",
            "definition_of_done": ["decision state survives restart"],
            "repository_id": "repo-c011",
            "allowed_files": ["README.md"],
            "workhub_task_id": "wh-c011",
        },
        idempotency_key="admit-c011-1",
        expected_version=None,
    )
    assert admit.get("ok") is True, admit
    objective_id = admit["objective_id"]
    started = await backend1.command(
        operation="start_cycle",
        tenant_id="tenant-c011",
        actor_id="actor-1",
        resource_id=objective_id,
        body={"candidate_sha": "deadbeef"},
        idempotency_key="start-c011-1",
        expected_version=None,
    )
    assert started.get("ok") is True, started
    cycle_id = started["cycle_id"]
    before = backend1.decision_bytes_for_cycle(cycle_id)
    assert before is not None

    # Fresh process: empty serving dicts, shared durable projection store.
    backend2 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=store,
    )
    assert cycle_id not in backend2._cycles  # noqa: SLF001
    loaded = await backend2.rehydrate_cycle(tenant_id="tenant-c011", cycle_id=cycle_id)
    assert loaded is True
    after = backend2.decision_bytes_for_cycle(cycle_id)
    assert after == before
    cycle = backend2._cycles[cycle_id]  # noqa: SLF001
    assert cycle.engine is not None
    assert cycle.phase == backend1._cycles[cycle_id].phase  # noqa: SLF001


@pytest.mark.asyncio
async def test_control_rehydrates_on_cycle_miss() -> None:
    store = InMemoryProjectionStore()
    backend1 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=store,
    )
    admit = await backend1.command(
        operation="admit_objective",
        tenant_id="tenant-c011",
        actor_id="actor-1",
        resource_id=None,
        body={
            "objective": "control load-on-miss",
            "task_class": "bounded_repair",
            "definition_of_done": ["rehydrate"],
            "repository_id": "repo-c011",
            "allowed_files": [],
            "workhub_task_id": "wh-c011b",
        },
        idempotency_key="admit-c011-2",
        expected_version=None,
    )
    assert admit.get("ok") is True, admit
    started = await backend1.command(
        operation="start_cycle",
        tenant_id="tenant-c011",
        actor_id="actor-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "c011sha"},
        idempotency_key="start-c011-2",
        expected_version=None,
    )
    assert started.get("ok") is True, started
    cycle_id = started["cycle_id"]

    backend2 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=store,
    )
    # cancel should rehydrate then proceed (or fail for other reasons, not NOT_FOUND)
    result = await backend2.command(
        operation="cancel_cycle",
        tenant_id="tenant-c011",
        actor_id="actor-1",
        resource_id=cycle_id,
        body={},
        idempotency_key="cancel-c011-1",
        expected_version=None,
    )
    assert result.get("error", {}).get("code") != "CYCLE_NOT_FOUND"
    assert cycle_id in backend2._cycles  # noqa: SLF001


@pytest.mark.asyncio
async def test_cross_tenant_snapshot_rejected() -> None:
    poisoned = AggregateSnapshot(
        tenant_id="tenant-a",
        aggregate_type="run",
        aggregate_id="cyc_x",
        sequence_number=1,
        schema_version=DECISION_STATE_SCHEMA,
        snapshot=wrap_run_snapshot(
            decision=decision_state_run(
                {
                    "cycle_id": "cyc_x",
                    "objective_id": "obj_x",
                    "tenant_id": "tenant-a",
                    "phase": "WAITING_APPROVAL",
                }
            ),
            sequence_number=1,
        ),
    )
    with pytest.raises(ReplayError) as exc:
        reconstruct_decision_pair(objective_snap=None, run_snap=poisoned, tenant_id="tenant-b")
    assert exc.value.code == "CROSS_TENANT_SNAPSHOT"

    # Backend path: poisoned cache entry keyed under the caller's tenant.
    store = InMemoryProjectionStore()
    store._by_key[("tenant-b", "run", "cyc_x")] = [poisoned]  # noqa: SLF001
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        require_workhub_load=False,
        projection_store=store,
    )
    with pytest.raises(ReplayError) as exc2:
        await backend.rehydrate_cycle(tenant_id="tenant-b", cycle_id="cyc_x")
    assert exc2.value.code == "CROSS_TENANT_SNAPSHOT"


def test_wrap_snapshots_round_trip_kinds() -> None:
    obj = wrap_objective_snapshot(
        decision=decision_state_objective(
            {
                "objective_id": "obj_1",
                "tenant_id": "t",
                "workhub_task_id": "w",
                "objective": "o",
                "task_class": "IMPLEMENT",
            }
        ),
        sequence_number=1,
    )
    run = wrap_run_snapshot(
        decision=decision_state_run(
            {"cycle_id": "c", "objective_id": "obj_1", "tenant_id": "t", "phase": "STARTING"}
        ),
        sequence_number=1,
    )
    assert validate_snapshot_envelope(obj)["kind"] == "objective"
    assert validate_snapshot_envelope(run)["kind"] == "run"


@pytest.mark.asyncio
async def test_rehydrate_restores_source_edit_work_order() -> None:
    """Restart must not turn a valid source_edit admission into NO_WORK_ORDER."""
    store = InMemoryProjectionStore()
    backend1 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=store,
    )
    admit = await backend1.command(
        operation="admit_objective",
        tenant_id="tenant-c011",
        actor_id="actor-1",
        resource_id=None,
        body={
            "objective": "Classify mounted routers by production authority",
            "task_class": "source_edit",
            "definition_of_done": ["endpoint authority matrix exists"],
            "repository_id": "aix-backend",
            "allowed_files": [],
            "workhub_task_id": "wh-envelope-restart",
        },
        idempotency_key="admit-envelope-restart",
        expected_version=None,
    )
    assert admit.get("ok") is True, admit
    assert admit.get("envelope_constructed") is True, admit
    objective_id = admit["objective_id"]
    started = await backend1.command(
        operation="start_cycle",
        tenant_id="tenant-c011",
        actor_id="actor-1",
        resource_id=objective_id,
        body={"candidate_sha": "deadbeef"},
        idempotency_key="start-envelope-restart",
        expected_version=None,
    )
    assert started.get("ok") is True, started
    cycle_id = started["cycle_id"]
    assert objective_id in backend1._envelopes  # noqa: SLF001

    backend2 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=store,
    )
    loaded = await backend2.rehydrate_cycle(tenant_id="tenant-c011", cycle_id=cycle_id)
    assert loaded is True
    assert objective_id in backend2._envelopes  # noqa: SLF001
    assert backend2._envelopes[objective_id].task_class == "source_edit"  # noqa: SLF001

