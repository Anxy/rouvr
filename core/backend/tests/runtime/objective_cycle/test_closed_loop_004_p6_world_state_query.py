"""AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P6: `get_world_state` query operation,
proven through the real `DurableObjectiveCycleApiBackend.query()` path.
"""
from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


@pytest.mark.asyncio
async def test_world_state_reflects_real_admitted_obligations() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=False, require_workhub_load=False)
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={
            "workhub_task_id": "task-701", "objective": "Refactor CronJobController.php",
            "task_class": "bounded_repair",
            "definition_of_done": ("Group public methods by domain", "Remove unused imports"),
            "repository_id": "hyeve-integration-service", "allowed_files": (), "project_id": "proj-1",
        },
        idempotency_key="admit-1", expected_version=None,
    )
    assert admit["ok"] is True

    world_state = await backend.query(
        operation="get_world_state", tenant_id="tenant-1", resource_id=admit["objective_id"], child_id=None,
        filters={},
    )
    assert world_state["ok"] is True
    item = world_state["item"]
    assert item["objective_id"] == admit["objective_id"]
    assert len(item["obligations"]) == 2
    # Nothing has been planned yet -- every obligation is UNPLANNED and therefore
    # remaining.
    assert all(o["status"] == "UNPLANNED" for o in item["obligations"])
    assert len(item["remaining_obligation_ids"]) == 2
    assert item["verification_state"] is None


@pytest.mark.asyncio
async def test_world_state_reflects_coverage_and_verification_once_planned() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=False, require_workhub_load=False)
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={
            "workhub_task_id": "task-702", "objective": "Refactor CronJobController.php",
            "task_class": "bounded_repair", "definition_of_done": ("Group public methods by domain",),
            "repository_id": "hyeve-integration-service", "allowed_files": (), "project_id": "proj-1",
        },
        idempotency_key="admit-1", expected_version=None,
    )
    objective_id = admit["objective_id"]
    obligation_id = admit["obligation_ids"][0]

    from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1
    from backend.aix_core.runtime.verification.semantic_verifier import SemanticVerificationOutcome

    backend._step_contracts[objective_id] = StepContractV1(  # noqa: SLF001
        cycle_id="cyc-1", tenant_id="tenant-1", step_index=0, purpose="group methods",
        target_scope=("CronJobController.php",), expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("grouped",), validation_commands=("php -l x",), assigned_role="executor",
        step_budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1),
        obligation_refs=(obligation_id,), status="PENDING",
    )
    backend._verification_verdicts[objective_id] = [  # noqa: SLF001
        SemanticVerificationOutcome(verdict="PASS", reasons=(), mechanical_passed=True, semantic_checked=True)
    ]

    world_state = await backend.query(
        operation="get_world_state", tenant_id="tenant-1", resource_id=objective_id, child_id=None, filters={},
    )
    item = world_state["item"]
    assert item["obligations"][0]["status"] == "SATISFIED"
    assert item["remaining_obligation_ids"] == []
    assert item["verification_state"] == "PASS"


@pytest.mark.asyncio
async def test_world_state_for_unknown_objective_fails_closed() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=False, require_workhub_load=False)
    result = await backend.query(
        operation="get_world_state", tenant_id="tenant-1", resource_id="obj-does-not-exist", child_id=None,
        filters={},
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "OBJECTIVE_NOT_FOUND"
