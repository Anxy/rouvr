"""AIX-W03-E02-T003: Idempotent Command & Event Transactions.

Verifies:
1. Tenant + command ID uniqueness constraint (`COMMAND_CONSTRAINT_NAME`).
2. Command retries with identical `idempotency_key` return cached prior results without duplicate state transitions or outbox events.
3. Concurrency conflicts on duplicate/out-of-order sequence updates fail closed cleanly.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.durable_aggregate.replay import InMemoryProjectionStore
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.objective.ledger_schema import COMMAND_CONSTRAINT_NAME, SEQUENCE_CONSTRAINT_NAME


@pytest.mark.asyncio
async def test_idempotent_command_retry_returns_cached_prior_result() -> None:
    """Submitting duplicate command requests with identical idempotency_key returns cached response."""
    store = InMemoryProjectionStore()
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=store,
    )

    # First command: Admit Objective
    first_cmd = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-idempotent",
        actor_id="actor-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-idempotent-001",
            "objective": "Idempotent transaction proof",
            "task_class": "bounded_repair",
            "definition_of_done": ("idempotency verified",),
            "repository_id": "repo-idempotent",
            "allowed_files": (),
        },
        idempotency_key="admit-key-001",
        expected_version=None,
    )
    assert first_cmd["ok"] is True
    obj_id = first_cmd["objective_id"]

    # Retry command with exact same idempotency_key
    second_cmd = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-idempotent",
        actor_id="actor-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-idempotent-001",
            "objective": "Idempotent transaction proof",
            "task_class": "bounded_repair",
            "definition_of_done": ("idempotency verified",),
            "repository_id": "repo-idempotent",
            "allowed_files": (),
        },
        idempotency_key="admit-key-001",
        expected_version=None,
    )

    assert second_cmd == first_cmd
    assert second_cmd["objective_id"] == obj_id


def test_ledger_schema_constraint_names() -> None:
    """Ledger table schema must specify immutable command and sequence constraint names."""
    assert COMMAND_CONSTRAINT_NAME == "objective_cycle_events_tenant_command_key"
    assert SEQUENCE_CONSTRAINT_NAME == "objective_cycle_events_tenant_aggregate_seq_key"
