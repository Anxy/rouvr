"""Cancel must clear zombie EXECUTING claims so GET cannot resurrect EXECUTING."""
from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


@pytest.mark.asyncio
async def test_cancel_clears_dispatch_claim_and_get_stays_cancelled() -> None:
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=False, require_workhub_load=False
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="t",
        actor_id="h",
        resource_id=None,
        body={
            "workhub_task_id": "wh-1",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "svc",
            "allowed_files": (),
            "objective": "noop objective",
        },
        idempotency_key="admit-1",
        expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="t",
        actor_id="h",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 5}},
        idempotency_key="start-1",
        expected_version=None,
    )
    cycle = backend._cycles[started["cycle_id"]]
    # Simulate hang: claim still EXECUTING while operator cancels.
    cycle.dispatch_state = "EXECUTING"
    cycle.execution_claim_id = "claim_zombie"
    cycle.execution_claim_step_id = "step-1"
    cycle.execution_claim_attempt = 2
    cycle.step_lease_expires_at = "2099-01-01T00:00:00+00:00"
    cycle.phase = "EXECUTING"

    cancelled = await backend.command(
        operation="cancel_cycle",
        tenant_id="t",
        actor_id="h",
        resource_id=started["cycle_id"],
        body={"reason": "operator_cancel"},
        idempotency_key="cancel-1",
        expected_version=None,
    )
    assert cancelled["ok"] is True
    assert cancelled["phase"] == "CANCELLED"
    assert cycle.execution_claim_id is None
    assert cycle.dispatch_state == "IDLE"

    viewed = await backend.query(
        operation="get_cycle",
        tenant_id="t",
        resource_id=started["cycle_id"],
        child_id=None,
        filters={},
    )
    item = viewed.get("item") or viewed
    assert item["phase"] == "CANCELLED"
    assert not item.get("execution_claim_id")
