"""AIX Closed-Loop Execution Plan, Sprint W2: ObjectiveEnvelopeV1 wired into the admit
path, and the "No Work Order -> No mutation" gate on `run_real_execute`.

Three real behaviors under test, all confirmed against this repo's actual fixtures
(not assumed):
1. A supported `task_class` constructs a real, persisted `ObjectiveEnvelopeV1`.
2. `task_class="bounded_repair"` (this repo's dominant real-world value, used across
   `test_mark_workhub_done.py`/`test_exploration_gate_integration.py`/
   `test_cursor_class_acceptance_matrix.py`/`test_ex6_s2_model_enforcement.py`) fails
   envelope construction but does NOT block execution -- a deliberate, documented
   deferral (see the gate's own comment in `durable_api_backend.py`), not a bug.
3. A non-task_class construction failure (simulated directly, since the public admit
   API cannot currently produce one -- every other required field has a safe fallback)
   genuinely blocks `run_real_execute` with `NO_WORK_ORDER`.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1, UnderstandingEvidenceV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult


def _seed_bootstrap_contract(backend: DurableObjectiveCycleApiBackend, objective_id: str, cycle_id: str) -> None:
    backend._understanding_evidence[objective_id] = UnderstandingEvidenceV1(  # noqa: SLF001
        objective_id=objective_id, cycle_id=cycle_id, files_read=("src/login.tsx",),
        observed_problem="test evidence", understanding_complete=True,
    )
    backend._step_contracts[objective_id] = StepContractV1(  # noqa: SLF001
        cycle_id=cycle_id, tenant_id="tenant-1", step_index=0, purpose="seeded execute",
        target_scope=("src/login.tsx",), expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("login page exists without changing unrelated files",),
        validation_commands=("echo ok",),
        assigned_role="executor",
        step_budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1),
        obligation_refs=tuple(o.id for o in backend._obligations.get(objective_id, ())),  # noqa: SLF001
        status="PENDING",
    )


async def _run_real_execute_ok(**kwargs: Any) -> RealExecuteResult:
    return RealExecuteResult(
        ok=True,
        code="REAL_EXECUTE_OK",
        workspace=r"D:\tmp\ws",
        evidence_refs=["file://ws/evidence/execution_report.json"],
        workhub_loaded=True,
        artifacts={},
        execution_status="COMPLETED",
        verification_status="PASSED",
        objective_status="COMPLETED",
        real_operations=1,
        target_files_modified=1,
    )


def _admit_body(*, task_class: str) -> dict[str, Any]:
    return {
        "workhub_task_id": "task-101",
        # "Create login page" classifies to task_family CREATE (deterministic, 0.9
        # confidence -- see task_family.py), which Sprint W1's task_class_mapping.py
        # deliberately leaves unmapped (no defensible existing-class fit). Matches the
        # exact objective text `test_mark_workhub_done.py` uses, which is how this
        # sprint's real regression was originally found.
        "objective": "Create login page",
        "task_class": task_class,
        "definition_of_done": ("done",),
        "repository_id": "aix-frontend",
        "allowed_files": ("src/login.tsx",),
        "project_id": "proj-1",
        "task_number": 101,
    }


async def _admit_and_start(backend: DurableObjectiveCycleApiBackend, *, task_class: str) -> dict[str, Any]:
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body=_admit_body(task_class=task_class),
        idempotency_key="admit-1",
        expected_version=None,
    )
    assert admit["ok"] is True, admit
    return admit


@pytest.mark.asyncio
async def test_supported_task_class_constructs_and_persists_envelope() -> None:
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    admit = await _admit_and_start(backend, task_class="single_file_helper")

    assert admit["envelope_constructed"] is True
    assert admit["envelope_id"] == admit["objective_id"]
    assert admit["envelope_construction_reason"] is None

    envelope = backend._envelopes[admit["objective_id"]]
    assert envelope.task_class == "single_file_helper"
    assert envelope.repository_scope == ("aix-frontend",)
    assert envelope.project_id == "proj-1"
    assert admit["objective_id"] not in backend._envelope_construction_errors


@pytest.mark.asyncio
async def test_unsupported_task_class_fails_construction_but_does_not_block_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    admit = await _admit_and_start(backend, task_class="bounded_repair")

    assert admit["envelope_constructed"] is False
    assert admit["envelope_id"] is None
    assert admit["envelope_construction_reason"] is not None
    assert all("task_class" in reason for reason in admit["envelope_construction_reason"])
    assert admit["objective_id"] not in backend._envelopes
    assert admit["objective_id"] in backend._envelope_construction_errors

    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-1",
        expected_version=None,
    )
    assert started["ok"] is True
    _seed_bootstrap_contract(backend, admit["objective_id"], started["cycle_id"])
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local-test"},
        idempotency_key="approve-1", expected_version=started["version"],
    )
    assert approved["ok"] is True, approved
    assert approved.get("error", {}).get("code") != "NO_WORK_ORDER"


@pytest.mark.asyncio
async def test_non_task_class_construction_failure_genuinely_blocks_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulates a construction failure the public admit API cannot currently produce
    (every other required envelope field has a safe fallback) to prove the gate's
    blocking branch is real, not dead code."""
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    admit = await _admit_and_start(backend, task_class="single_file_helper")
    assert admit["envelope_constructed"] is True

    # Simulate a genuine, non-task_class construction failure discovered after admit.
    del backend._envelopes[admit["objective_id"]]
    backend._envelope_construction_errors[admit["objective_id"]] = ("('repository_scope',):some_other_failure",)

    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-1",
        expected_version=None,
    )
    assert started["ok"] is True
    _seed_bootstrap_contract(backend, admit["objective_id"], started["cycle_id"])
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local-test"},
        idempotency_key="approve-1", expected_version=started["version"],
    )
    assert approved["ok"] is False
    assert approved["error"]["code"] == "NO_WORK_ORDER"
