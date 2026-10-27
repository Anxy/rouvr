"""AIX Closed-Loop Execution Plan, Sprint W6: bind approval to a StepContractV1 and add
the pre-dispatch scope checks. Three real behaviors:
1. The envelope-vs-repository re-check (always on, not flag-gated) rejects a repository
   that drifted after the envelope was constructed.
2. With the planner flag OFF (the default), no live planner call happens and
   mutation is BLOCKED (`PLANNER_DISABLED`) unless an ACCEPTED PlanRevision already
   exists from an explicit seeded bootstrap. Silent direct execution is forbidden.
3. With the flag ON, a stubbed planner proposal is used, and a target_scope outside
   obj.allowed_files is rejected before run_real_execute is ever called.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import StepContractV1, UnderstandingEvidenceV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.planner.step_contract_planner import StepContractProposalV1


def _seed_understanding(backend: DurableObjectiveCycleApiBackend, objective_id: str) -> None:
    """The target-bound admit fixture below takes the exploration-skip path (Sprint
    W2/W4), so it never populates `_understanding_evidence` for real -- that is a
    separate, already-documented concern (whether the explore loop converges for an
    *unbound* objective, tracked since the original FeeDue POC), orthogonal to what
    Sprint W6 actually tests here (the scope-check logic once a proposal exists).
    Seeding directly isolates the two."""
    backend._understanding_evidence[objective_id] = UnderstandingEvidenceV1(  # noqa: SLF001
        objective_id=objective_id, cycle_id=None, files_read=("FeeDue.php",),
        observed_problem="test evidence", understanding_complete=True,
    )


async def _run_real_execute_ok(**kwargs: Any) -> RealExecuteResult:
    return RealExecuteResult(
        ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=True,
        artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
        objective_status="COMPLETED", real_operations=1, target_files_modified=1,
    )


def _admit_body(*, allowed_files: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "workhub_task_id": "task-101",
        "objective": "Add a helper function",
        "task_class": "single_file_helper",
        "definition_of_done": ("done",),
        "repository_id": "aix-frontend",
        "allowed_files": allowed_files,
        "project_id": "proj-1",
        "task_number": 101,
    }


async def _admit_start_approve(
    backend: DurableObjectiveCycleApiBackend, *, seed_understanding: bool = False, **body_overrides: Any
) -> dict[str, Any]:
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body=_admit_body(**body_overrides), idempotency_key="admit-1", expected_version=None,
    )
    assert admit["ok"] is True, admit
    if seed_understanding:
        _seed_understanding(backend, admit["objective_id"])
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-1",
        expected_version=None,
    )
    assert started["ok"] is True
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-1", expected_version=started["version"],
    )
    return {"admit": admit, "started": started, "approved": approved}


@pytest.mark.no_plan_bootstrap
@pytest.mark.asyncio
async def test_flag_off_default_behavior_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    result = await _admit_start_approve(backend)
    approved = result["approved"]
    assert approved["ok"] is False, approved
    assert (approved.get("error") or {}).get("code") == "PLANNER_DISABLED"
    assert result["admit"]["objective_id"] not in backend._step_contracts  # noqa: SLF001
    assert result["admit"]["objective_id"] not in backend._plan_revisions  # noqa: SLF001


@pytest.mark.asyncio
async def test_planner_transport_failure_parks_running_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """A live planner auth/transport failure is terminally observable, never an
    unleased RUNNING_STEP that the client can only poll forever."""

    async def _planner_error(**kwargs: Any) -> StepContractProposalV1:
        return StepContractProposalV1(
            contract=None,
            reply="",
            reason="PLANNER_MODEL_ERROR:INTEGRATION_CHAT_REJECTED:status=401",
        )

    async def _must_not_execute(**kwargs: Any) -> RealExecuteResult:
        raise AssertionError("executor must not run after planner failure")

    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract",
        _planner_error,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        _must_not_execute,
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False,
    )
    result = await _admit_start_approve(
        backend, seed_understanding=True, allowed_files=("FeeDue.php",),
    )
    approved = result["approved"]
    assert approved["ok"] is False
    assert approved["phase"] == "BLOCKED"
    assert approved["terminal_reason"] == "PLANNER_MODEL_ERROR"
    assert approved["error"]["code"] == "PLANNER_MODEL_ERROR"


@pytest.mark.asyncio
async def test_repository_scope_drift_after_envelope_blocks_with_replan_required(
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
        body=_admit_body(), idempotency_key="admit-1", expected_version=None,
    )
    assert admit["ok"] is True
    assert admit["envelope_constructed"] is True

    # Simulate a post-admission repository rebind (e.g. a real `clarify` call) that the
    # frozen envelope, built at admit time, cannot retroactively know about.
    backend._objectives[admit["objective_id"]].repository_id = "a-different-repo"  # noqa: SLF001

    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-1",
        expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-1", expected_version=started["version"],
    )
    assert approved["ok"] is False
    assert approved["error"]["code"] == "REPLAN_REQUIRED"


@pytest.mark.asyncio
async def test_flag_on_out_of_scope_step_contract_blocks_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED", True
    )

    async def _fake_propose(**kwargs: Any) -> StepContractProposalV1:
        contract = StepContractV1(
            cycle_id=kwargs["cycle_id"], tenant_id=kwargs["tenant_id"], step_index=0,
            purpose="edit a file outside scope", target_scope=("NotInAllowedFiles.php",),
            expected_output_schema_ref="aix.step_contract.v1.generic_edit",
            acceptance_criteria=("x",), validation_commands=("php -l x",),
            assigned_role=kwargs["assigned_role"], step_budget=kwargs["step_budget"],
            # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P3: cover every real obligation
            # so this test still exercises the scope check (its actual subject), not
            # the coverage gate that now runs before it.
            obligation_refs=tuple(o.id for o in kwargs["obligations"]),
            status="PENDING",
        )
        return StepContractProposalV1(contract=contract, reply="proposed", reason=None)

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract", _fake_propose
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    result = await _admit_start_approve(backend, seed_understanding=True, allowed_files=("FeeDue.php",))
    approved = result["approved"]
    assert approved["ok"] is False, approved
    assert approved["error"]["code"] == "REPLAN_REQUIRED"
    assert "NotInAllowedFiles.php" in approved["error"]["detail"]


@pytest.mark.asyncio
async def test_flag_on_in_scope_step_contract_proceeds_to_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED", True
    )

    async def _fake_propose(**kwargs: Any) -> StepContractProposalV1:
        contract = StepContractV1(
            cycle_id=kwargs["cycle_id"], tenant_id=kwargs["tenant_id"], step_index=0,
            purpose="edit the allowed file", target_scope=("FeeDue.php",),
            expected_output_schema_ref="aix.step_contract.v1.generic_edit",
            acceptance_criteria=("x",), validation_commands=("php -l FeeDue.php",),
            assigned_role=kwargs["assigned_role"], step_budget=kwargs["step_budget"],
            # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P3: cover every real obligation
            # so this test reaches COMPLETED_FOR_REVIEW as intended, not PLAN_INCOMPLETE.
            obligation_refs=tuple(o.id for o in kwargs["obligations"]),
            status="PENDING",
        )
        return StepContractProposalV1(contract=contract, reply="proposed", reason=None)

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract", _fake_propose
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    result = await _admit_start_approve(backend, seed_understanding=True, allowed_files=("FeeDue.php",))
    approved = result["approved"]
    assert approved["ok"] is True, approved
    assert approved["phase"] == "COMPLETED_FOR_REVIEW"
    objective_id = result["admit"]["objective_id"]
    assert objective_id in backend._step_contracts  # noqa: SLF001
    assert backend._step_contracts[objective_id].target_scope == ("FeeDue.php",)  # noqa: SLF001
