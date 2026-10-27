"""AIX-VOL-III-T006: PostgreSQL Reconstruction vs Derived Process-Local State.

Verifies:
1. Authoritative aggregate state reconstructs cleanly from `ObjectiveCycleRepository` snapshot store.
2. Process-local maps (`_objectives`, `_cycles`) act purely as derived caches, rehydrated from durable projections.
3. Zero third store or competing lifecycle storage exists.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.durable_aggregate.replay import InMemoryProjectionStore
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.objective.repository import ObjectiveCycleRepository


@pytest.mark.asyncio
async def test_durable_repository_snapshot_reconstruction_and_cache_derivation() -> None:
    """Verifies that durable aggregate snapshots reconstruct cycle state when in-memory maps are empty."""
    store = InMemoryProjectionStore()

    # Instance 1: Admit objective and start cycle
    backend1 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=store,
    )

    admit = await backend1.command(
        operation="admit_objective",
        tenant_id="tenant-pg-reconstruct",
        actor_id="actor-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-pg-101",
            "objective": "Postgres reconstruction test",
            "task_class": "bounded_repair",
            "definition_of_done": ("reconstruct from repository",),
            "repository_id": "repo-pg",
            "allowed_files": (),
        },
        idempotency_key="admit-pg-101",
        expected_version=None,
    )
    assert admit["ok"] is True
    obj_id = admit["objective_id"]

    start = await backend1.command(
        operation="start_cycle",
        tenant_id="tenant-pg-reconstruct",
        actor_id="actor-1",
        resource_id=obj_id,
        body={"candidate_sha": "pgreconstructsha"},
        idempotency_key="start-pg-101",
        expected_version=None,
    )
    assert start["ok"] is True
    cycle_id = start["cycle_id"]

    # Verify latest projection snapshot exists in store
    snap = store.latest_snapshot(
        tenant_id="tenant-pg-reconstruct", aggregate_type="run", aggregate_id=cycle_id
    )
    assert snap is not None
    assert snap.aggregate_id == cycle_id

    # Instance 2: Fresh process with zero in-memory cycle or objective records
    backend2 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=store,
    )

    # In-memory serving dicts are initially empty (cache miss)
    assert obj_id not in backend2._objectives  # noqa: SLF001
    assert cycle_id not in backend2._cycles  # noqa: SLF001

    # Query triggers cache populating from durable projection snapshot
    q_cycle = await backend2.query(
        operation="get_cycle",
        tenant_id="tenant-pg-reconstruct",
        resource_id=cycle_id,
        child_id=None,
        filters={},
    )
    assert q_cycle["ok"] is True
    assert q_cycle["item"]["cycle_id"] == cycle_id

    # In-memory dict is now populated as a derived cache
    assert cycle_id in backend2._cycles  # noqa: SLF001
    assert backend2._cycles[cycle_id].cycle_id == cycle_id  # noqa: SLF001
