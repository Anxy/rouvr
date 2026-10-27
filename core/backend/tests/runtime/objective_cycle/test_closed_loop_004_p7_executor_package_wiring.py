"""AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P7: proves `_maybe_real_execute` actually
records an `ExecutorPackageV1` for every real dispatch, retrievable afterward via
`get_executor_packages` -- "what authoritative package did the executor receive" is
answerable from real state, not reconstructed.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult


async def _run_real_execute_ok(**kwargs: Any) -> RealExecuteResult:
    return RealExecuteResult(
        ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=True,
        artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
        objective_status="COMPLETED", real_operations=1, target_files_modified=1,
    )


@pytest.mark.asyncio
async def test_executor_package_is_recorded_and_retrievable_after_a_real_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={
            "workhub_task_id": "task-801", "objective": "Add SMTP_PASSWORD support",
            "task_class": "bounded_repair", "definition_of_done": ("done",),
            "repository_id": "hyeve-integration-service", "allowed_files": ("backend/config.py",), "project_id": "proj-1",
        },
        idempotency_key="admit-1", expected_version=None,
    )
    objective_id = admit["objective_id"]
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=objective_id,
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-1",
        expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-1", expected_version=started["version"],
    )
    assert approved["ok"] is True, approved

    packages = await backend.query(
        operation="get_executor_packages", tenant_id="tenant-1", resource_id=objective_id, child_id=None,
        filters={},
    )
    assert packages["ok"] is True
    assert packages["count"] == 1
    package = packages["items"][0]
    assert package["objective_boundary"] == "Add SMTP_PASSWORD support"
    assert package["cycle_id"] == started["cycle_id"]
    assert package["objective_id"] == objective_id
    # AIX-PA-002: planner-off execute still carries a seeded bootstrap contract
    # (NO PLAN = NO MUTATION). Boundary is recorded either way.
    assert package["step_contract_id"]


@pytest.mark.asyncio
async def test_executor_package_for_unknown_objective_fails_closed() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=False, require_workhub_load=False)
    result = await backend.query(
        operation="get_executor_packages", tenant_id="tenant-1", resource_id="obj-does-not-exist", child_id=None,
        filters={},
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "OBJECTIVE_NOT_FOUND"
