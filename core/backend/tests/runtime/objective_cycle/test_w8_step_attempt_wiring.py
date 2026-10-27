"""AIX Closed-Loop Execution Plan, Sprint W8: StepAttemptV1 wired into the real
admit -> start_cycle -> approve path, not just unit-tested in isolation."""

from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1, UnderstandingEvidenceV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult


def _seed_bootstrap_contract(backend: DurableObjectiveCycleApiBackend, objective_id: str, cycle_id: str) -> None:
    backend._understanding_evidence[objective_id] = UnderstandingEvidenceV1(  # noqa: SLF001
        objective_id=objective_id, cycle_id=cycle_id, files_read=("helper.py",),
        observed_problem="test evidence", understanding_complete=True,
    )
    backend._step_contracts[objective_id] = StepContractV1(  # noqa: SLF001
        cycle_id=cycle_id, tenant_id="tenant-1", step_index=0, purpose="seeded execute",
        target_scope=("helper.py",), expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("helper exists and existing callers still work",),
        validation_commands=("python -m py_compile helper.py",),
        assigned_role="executor",
        step_budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1),
        obligation_refs=tuple(o.id for o in backend._obligations.get(objective_id, ())),  # noqa: SLF001
        status="PENDING",
    )


async def _run_real_execute_ok(**kwargs: Any) -> RealExecuteResult:
    return RealExecuteResult(
        ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=["file://ws/evidence/report.json"],
        workhub_loaded=True, artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
        objective_status="COMPLETED", real_operations=2, target_files_modified=1,
        completion_gates={"diff_exists": True},
    )


@pytest.mark.asyncio
async def test_successful_cycle_records_a_step_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={
            "workhub_task_id": "task-101", "objective": "Add a helper function",
            "task_class": "single_file_helper", "definition_of_done": ("done",),
            "repository_id": "aix-frontend", "allowed_files": (), "project_id": "proj-1",
        },
        idempotency_key="admit-1", expected_version=None,
    )
    assert admit["ok"] is True
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-1",
        expected_version=None,
    )
    assert started["ok"] is True
    _seed_bootstrap_contract(backend, admit["objective_id"], started["cycle_id"])
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-1", expected_version=started["version"],
    )
    assert approved["ok"] is True, approved

    objective_id = admit["objective_id"]
    attempts = backend._step_attempts.get(objective_id)  # noqa: SLF001
    assert attempts is not None and len(attempts) == 1
    attempt = attempts[0]
    assert attempt.outcome == "SUCCEEDED"
    assert attempt.tenant_id == "tenant-1"
    assert attempt.step_id == backend._step_contracts[objective_id].id  # noqa: SLF001
    assert attempt.artifact_refs == ("file://ws/evidence/report.json",)
