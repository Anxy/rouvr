"""AIX-W03-E01-T002: Process Restart Reconstruction & Cache Derivation.

Verifies:
1. In-memory `_objectives` and `_cycles` maps are derived caches; authoritative state is stored in `InMemoryProjectionStore` / `ObjectiveCycleRepository`.
2. Process death & restart: A fresh backend instance with empty memory maps reconstructs objective and cycle state cleanly.
3. Multi-step restart campaign: admit -> start -> kill process -> restart & query -> approve & complete -> verify zero lost or duplicate effects.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.durable_aggregate.replay import InMemoryProjectionStore
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


@pytest.mark.asyncio
async def test_process_restart_reconstructs_objective_and_cycle_state() -> None:
    """Verifies that a fresh backend instance with empty serving dicts reconstructs state from projections."""
    shared_store = InMemoryProjectionStore()

    # Step 1: Process Instance 1 admits and starts objective
    backend1 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=shared_store,
    )

    admit1 = await backend1.command(
        operation="admit_objective",
        tenant_id="tenant-restart-1",
        actor_id="actor-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-restart-001",
            "objective": "Restart reconstruction proof",
            "task_class": "bounded_repair",
            "definition_of_done": ("reconstruct state cleanly",),
            "repository_id": "repo-restart",
            "allowed_files": (),
        },
        idempotency_key="admit-restart-1",
        expected_version=None,
    )
    assert admit1["ok"] is True
    obj_id = admit1["objective_id"]

    start1 = await backend1.command(
        operation="start_cycle",
        tenant_id="tenant-restart-1",
        actor_id="actor-1",
        resource_id=obj_id,
        body={"candidate_sha": "restartsha1234"},
        idempotency_key="start-restart-1",
        expected_version=None,
    )
    assert start1["ok"] is True
    cycle_id = start1["cycle_id"]
    version_at_start = int(start1["version"])
    phase_at_start = start1["phase"]

    # Step 2: Simulated Process Death
    # Instantiate backend2 with empty in-memory dicts, using shared_store
    backend2 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=shared_store,
    )

    assert obj_id not in backend2._objectives  # noqa: SLF001
    assert cycle_id not in backend2._cycles  # noqa: SLF001

    # Step 3: Reconstruct Objective from Projection Store
    obj_query = await backend2.query(
        operation="get_objective",
        tenant_id="tenant-restart-1",
        resource_id=obj_id,
        child_id=None,
        filters={},
    )
    assert obj_query["ok"] is True
    assert obj_query["item"]["objective_id"] == obj_id
    assert obj_query["item"]["workhub_task_id"] == "wh-restart-001"

    # Step 4: Reconstruct Cycle from Projection Store
    cycle_query = await backend2.query(
        operation="get_cycle",
        tenant_id="tenant-restart-1",
        resource_id=cycle_id,
        child_id=None,
        filters={},
    )
    assert cycle_query["ok"] is True
    assert cycle_query["item"]["cycle_id"] == cycle_id
    assert cycle_query["item"]["phase"] == phase_at_start

    # Step 5: Resume Execution on Reconstructed Backend
    approved = await backend2.command(
        operation="validate_approval_reference",
        tenant_id="tenant-restart-1",
        actor_id="actor-1",
        resource_id=cycle_id,
        body={"approval_reference": "workhub://approvals/restart-proof"},
        idempotency_key="approve-restart-1",
        expected_version=version_at_start,
    )
    assert approved["ok"] is True
    assert approved["phase"] == "COMPLETED_FOR_REVIEW"

    # Step 6: Verify Rehydration of Terminal State on third fresh instance
    backend3 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=shared_store,
    )
    rehydrated = await backend3.rehydrate_cycle(tenant_id="tenant-restart-1", cycle_id=cycle_id)
    assert rehydrated is True
    assert backend3._cycles[cycle_id].phase == "COMPLETED_FOR_REVIEW"  # noqa: SLF001
