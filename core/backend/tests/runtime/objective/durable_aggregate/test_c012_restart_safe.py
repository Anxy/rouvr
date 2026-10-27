"""C012: restart-safe commands, session locks, kill/resume proof."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.durable_aggregate.replay import InMemoryProjectionStore
from backend.aix_core.runtime.objective.durable_aggregate.session_lock import InMemorySessionLockStore, SessionLockError
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


async def _admit_start(backend: DurableObjectiveCycleApiBackend, *, suffix: str) -> tuple[str, str, int]:
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-c012",
        actor_id="actor-1",
        resource_id=None,
        body={
            "workhub_task_id": f"wh-c012-{suffix}",
            "objective": "C012 restart-safe proof",
            "task_class": "bounded_repair",
            "definition_of_done": ("resume without duplicate",),
            "repository_id": "repo-c012",
            "allowed_files": (),
        },
        idempotency_key=f"admit-{suffix}",
        expected_version=None,
    )
    assert admit.get("ok") is True, admit
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-c012",
        actor_id="actor-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "c012sha1"},
        idempotency_key=f"start-{suffix}",
        expected_version=None,
    )
    assert started.get("ok") is True, started
    return admit["objective_id"], started["cycle_id"], int(started["version"])


def test_session_lock_exclusive() -> None:
    store = InMemorySessionLockStore(default_ttl_seconds=5.0)
    store.acquire(tenant_id="t", run_id="cyc_1", holder="a")
    with pytest.raises(SessionLockError) as exc:
        store.acquire(tenant_id="t", run_id="cyc_1", holder="b")
    assert exc.value.code == "RUN_LOCK_HELD"
    store.release(tenant_id="t", run_id="cyc_1", holder="a")
    store.acquire(tenant_id="t", run_id="cyc_1", holder="b")


@pytest.mark.asyncio
async def test_concurrent_mutate_second_gets_lock_held() -> None:
    locks = InMemorySessionLockStore(default_ttl_seconds=30.0)
    projections = InMemoryProjectionStore()
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=projections,
        session_lock_store=locks,
    )
    _obj, cycle_id, version = await _admit_start(backend, suffix="lock")

    # Hold the lease externally while a command tries to acquire.
    locks.acquire(tenant_id="tenant-c012", run_id=cycle_id, holder="external-holder")
    blocked = await backend.command(
        operation="cancel_cycle",
        tenant_id="tenant-c012",
        actor_id="actor-1",
        resource_id=cycle_id,
        body={},
        idempotency_key="cancel-blocked",
        expected_version=version,
    )
    assert blocked["ok"] is False
    assert blocked["error"]["code"] == "RUN_LOCK_HELD"
    locks.release(tenant_id="tenant-c012", run_id=cycle_id, holder="external-holder")


@pytest.mark.asyncio
async def test_kill_restart_resume_no_duplicate_approval_mutation() -> None:
    """P1-shaped proof: start → kill → restart → approve once; repeat id is no-op."""
    store = InMemoryProjectionStore()
    backend1 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=store,
    )
    _obj, cycle_id, version = await _admit_start(backend1, suffix="kill")
    phase_before = backend1._cycles[cycle_id].phase  # noqa: SLF001
    events_before = backend1._cycles[cycle_id].event_sequence  # noqa: SLF001
    decision_before = backend1.decision_bytes_for_cycle(cycle_id)

    # Process death: drop serving dicts; durable projections remain.
    backend2 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=store,
    )
    assert cycle_id not in backend2._cycles  # noqa: SLF001

    q = await backend2.query(
        operation="get_cycle",
        tenant_id="tenant-c012",
        resource_id=cycle_id,
        child_id=None,
        filters={},
    )
    assert q["ok"] is True, q
    assert q["item"]["phase"] == phase_before
    assert backend2.decision_bytes_for_cycle(cycle_id) == decision_before

    first = await backend2.command(
        operation="validate_approval_reference",
        tenant_id="tenant-c012",
        actor_id="actor-1",
        resource_id=cycle_id,
        body={"approval_reference": "workhub://approvals/c012", "approval_request_id": "req-c012-1"},
        idempotency_key="approve-c012-1",
        expected_version=version,
    )
    assert first.get("ok") is True, first
    assert first["phase"] == "COMPLETED_FOR_REVIEW"
    seq_after_first = backend2._cycles[cycle_id].event_sequence  # noqa: SLF001
    assert seq_after_first >= events_before

    # Same idempotency key: cached response, no second mutation path.
    second = await backend2.command(
        operation="validate_approval_reference",
        tenant_id="tenant-c012",
        actor_id="actor-1",
        resource_id=cycle_id,
        body={"approval_reference": "workhub://approvals/c012", "approval_request_id": "req-c012-1"},
        idempotency_key="approve-c012-1",
        expected_version=version,
    )
    assert second == first
    assert backend2._cycles[cycle_id].event_sequence == seq_after_first  # noqa: SLF001

    # Kill again and rehydrate terminal decision state.
    backend3 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=store,
    )
    loaded = await backend3.rehydrate_cycle(tenant_id="tenant-c012", cycle_id=cycle_id)
    assert loaded is True
    assert backend3._cycles[cycle_id].phase == "COMPLETED_FOR_REVIEW"  # noqa: SLF001


@pytest.mark.asyncio
async def test_get_objective_load_on_command_after_restart() -> None:
    store = InMemoryProjectionStore()
    backend1 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=store,
    )
    objective_id, _cycle_id, _v = await _admit_start(backend1, suffix="objq")
    backend2 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=store,
    )
    q = await backend2.query(
        operation="get_objective",
        tenant_id="tenant-c012",
        resource_id=objective_id,
        child_id=None,
        filters={},
    )
    assert q["ok"] is True
    assert q["item"]["objective_id"] == objective_id
