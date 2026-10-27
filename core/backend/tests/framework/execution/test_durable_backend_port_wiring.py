from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.framework.execution.port import ExecuteStepRequest
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1, UnderstandingEvidenceV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.hyeve_aix_service.config import FrameworkFlagsSnapshot


class RecordingPort:
    def __init__(self) -> None:
        self.requests: list[ExecuteStepRequest] = []

    async def execute_step(self, request: ExecuteStepRequest) -> RealExecuteResult:
        self.requests.append(request)
        return RealExecuteResult(
            ok=True, code="REAL_EXECUTE_OK", workspace="ws",
            evidence_refs=["file://ws/evidence/report.json"],
            workhub_loaded=False, execution_status="COMPLETED", verification_status="PASSED",
            objective_status="COMPLETED", real_operations=1, target_files_modified=1,
            completion_gates={"diff_exists": True},
        )


def _seed(backend: DurableObjectiveCycleApiBackend, objective_id: str, cycle_id: str) -> None:
    backend._understanding_evidence[objective_id] = UnderstandingEvidenceV1(  # noqa: SLF001
        objective_id=objective_id, cycle_id=cycle_id, files_read=("helper.py",),
        observed_problem="test evidence", understanding_complete=True,
    )
    backend._step_contracts[objective_id] = StepContractV1(  # noqa: SLF001
        cycle_id=cycle_id, tenant_id="tenant-1", step_index=0, purpose="seeded execute",
        target_scope=("helper.py",), expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("helper works",), validation_commands=("python -m py_compile helper.py",),
        assigned_role="executor",
        step_budget=BudgetV1(
            time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1
        ),
        obligation_refs=tuple(o.id for o in backend._obligations.get(objective_id, ())),  # noqa: SLF001
        status="PENDING",
    )


@pytest.mark.asyncio
async def test_enabled_snapshot_dispatches_once_through_injected_port() -> None:
    port = RecordingPort()
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False,
        execution_port=port, framework_flags=FrameworkFlagsSnapshot(execution_port=True),
    )
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={"workhub_task_id": "task-1", "objective": "Add a helper function",
              "task_class": "single_file_helper", "definition_of_done": ("done",),
              "repository_id": "aix-frontend", "allowed_files": (), "project_id": "proj-1"},
        idempotency_key="admit-port", expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1",
        resource_id=admit["objective_id"], body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-port", expected_version=None,
    )
    _seed(backend, admit["objective_id"], started["cycle_id"])
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-port", expected_version=started["version"],
    )
    assert approved["ok"] is True, approved
    assert len(port.requests) == 1
    assert port.requests[0].objective_id == admit["objective_id"]


def test_flag_snapshot_is_captured_at_construction() -> None:
    class Settings:
        AIX_FRAMEWORK_EXECUTION_PORT = True

    settings = Settings()
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", settings=settings)
    settings.AIX_FRAMEWORK_EXECUTION_PORT = False
    assert backend._framework_flags.execution_port is True  # noqa: SLF001
