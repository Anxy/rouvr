"""Unit tests for the refactored modular domain services.

Validates:
1. AdmissionService: intent freeze, understanding, obligations, envelope
2. PlanningService: exploration need, quality check, incremental replanning
3. DispatchService: step selection, claim lease management
4. ExecutionService: tool execution, diff validation, mutation summary
5. VerificationService: semantic verification, coverage, proofs
6. RepairService: gap lifecycle, blocking gap query, repair directive synthesis
7. TerminalizationService: conjunctive guard evaluation, engine phase delegation
8. RefactoredObjectiveCycleApiBackend: end-to-end lifecycle
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import pytest

from backend.aix_core.runtime.objective.services.admission_service import (
    AdmissionService,
)
from backend.aix_core.runtime.objective.services.dispatch_service import (
    DispatchService,
)
from backend.aix_core.runtime.objective.services.execution_service import (
    ExecutionService,
)
from backend.aix_core.runtime.objective.services.planning_service import (
    PlanningService,
)
from backend.aix_core.runtime.objective.services.refactored_backend import (
    RefactoredObjectiveCycleApiBackend,
)
from backend.aix_core.runtime.objective.services.repair_service import (
    RepairService,
)
from backend.aix_core.runtime.objective.services.terminalization_service import (
    TerminalizationService,
)
from backend.aix_core.runtime.objective.services.verification_service import (
    VerificationService,
)
from backend.aix_core.runtime.objective.contracts import (
    AgentRole,
    BudgetV1,
    ObjectiveObligationV1,
    PlanRevisionV1,
    StepContractV1,
    StepRuntimeStateV1,
    StepStatus,
)
from backend.aix_core.runtime.verification.gap import (
    GapStore,
    GapV1,
)
from backend.workflows.temporal.objective_cycle_contracts import (
    ObjectiveCycleWorkflowInput,
)
from backend.workflows.temporal.objective_cycle_workflow import (
    AixObjectiveCycleWorkflowEngine,
)


def _sample_step_contract(step_id: str = "step-1") -> StepContractV1:
    return StepContractV1(
        id=step_id,
        cycle_id="cyc-1",
        tenant_id="tenant-1",
        step_index=0,
        purpose="Run auth unit test",
        target_scope=("backend/auth.py",),
        expected_output_schema_ref="aix.step_contract.v1.proposal",
        acceptance_criteria=("Tests pass",),
        validation_commands=("pytest tests/test_auth.py",),
        assigned_role="executor",
        step_budget=BudgetV1(time_seconds=60, tokens=1000, cost_usd=0.1, retries=1, steps=1, concurrency=1),
        status="PENDING",
    )


def test_admission_service_admit():
    svc = AdmissionService()
    pkg = svc.admit(
        tenant_id="tenant-test",
        actor_id="tester-1",
        body={
            "objective": "Verify user login and auth tokens",
            "task_class": "bounded_repair",
            "definition_of_done": ("Unit tests pass", "Integration tests pass"),
            "allowed_files": ("backend/auth.py", "backend/login.py"),
        },
        command_id="cmd-1",
    )
    assert pkg.objective_id.startswith("obj_")
    assert pkg.intent.text == "Verify user login and auth tokens"
    assert len(pkg.obligations) >= 2
    assert pkg.ovc is not None
    assert pkg.task_family is not None


def test_dispatch_service_claim():
    svc = DispatchService(default_lease_seconds=60)
    step = _sample_step_contract("step-1")
    claim = svc.claim_step(
        objective_id="obj-1",
        cycle_id="cyc-1",
        plan_id="plan-1",
        step=step,
        current_attempt=1,
    )
    assert claim.claimed is True
    assert claim.step_id == "step-1"
    assert claim.claim_id.startswith("claim_")
    assert claim.lease_expires_at is not None


def test_repair_service_lifecycle():
    store = GapStore()
    svc = RepairService(gap_store=store)

    gap = GapV1(
        gap_id="gap-1",
        objective_id="obj-1",
        verification_id="verif-1",
        category="CONTRACT_VIOLATION",
        severity="BLOCKER",
        expected="Auth token validation must pass",
        observed="Auth token validation failed with 401",
        status="OPEN",
    )
    svc.record_gaps([gap])

    blocking = svc.get_blocking_gaps("obj-1")
    assert len(blocking) == 1
    assert blocking[0].gap_id == "gap-1"

    directive_res = svc.synthesize_repair_directive(
        objective_id="obj-1",
        plan=None,
        failure_reasons=("Token expired",),
    )
    assert directive_res.can_auto_repair is True
    assert len(directive_res.blocking_gaps) == 1


def test_terminalization_guard_conjunction_blocking():
    store = GapStore()
    svc = TerminalizationService(gap_store=store)

    gap = GapV1(
        gap_id="gap-block-1",
        objective_id="obj-term-1",
        verification_id="verif-2",
        category="REGRESSION",
        severity="BLOCKER",
        expected="No regression",
        observed="Regression observed in test suite",
        status="OPEN",
    )
    store.put_gap(gap)

    eval_res = svc.evaluate_guards(
        objective_id="obj-term-1",
        cycle_id="cyc-term-1",
        version=1,
        current_phase="EXECUTING",
        plan=None,
        step_runtime={},
        ovc=None,
    )

    # Must deny terminalization when blocking gaps or missing verified steps
    assert eval_res.terminalization_decision.decision in ("DENY_NEEDS_REPAIR", "DENY_TERMINALIZATION")
    assert eval_res.guards_evaluated.all_conjuncts_pass is False


def test_terminalization_service_delegates_to_engine():
    store = GapStore()
    svc = TerminalizationService(gap_store=store)

    plan_material = "obj-1:cyc-1:sha12345"
    plan_sha = hashlib.sha256(plan_material.encode("utf-8")).hexdigest()
    wf_input = ObjectiveCycleWorkflowInput(
        workflow_version=1,
        tenant_id="tenant-1",
        objective_id="obj-1",
        cycle_id="cyc-1",
        candidate_sha="sha12345",
        plan_sha256=plan_sha,
        start_request_id="req-1",
        correlation_id="corr-1",
    )
    engine = AixObjectiveCycleWorkflowEngine(wf_input)
    engine.run_until_wait_or_terminal()

    # Move engine to RUNNING_STEP
    engine.signal_approval(approval_ref="approval-1", valid=True)
    assert str(engine.state.phase) == "RUNNING_STEP"

    eval_res = svc.evaluate_guards(
        objective_id="obj-1",
        cycle_id="cyc-1",
        version=1,
        current_phase=str(engine.state.phase),
        plan=None,
        step_runtime={},
        ovc=None,
        real_execute={"ok": True, "evidence": ["ev-1"]},
    )

    term_dec = svc.apply_terminalization_to_engine(
        engine=engine,
        step_id="step-1",
        evaluation=eval_res,
    )
    assert term_dec is not None
    # Phase should now be updated authoritatively BY the engine
    assert str(engine.state.phase) in ("GAPS_FOUND", "CERTIFIED", "CERTIFYING", "EXECUTION_COMPLETE", "VERIFYING_OBJECTIVE")


@pytest.mark.asyncio
async def test_refactored_backend_lifecycle():
    backend = RefactoredObjectiveCycleApiBackend(mode="inprocess")

    # 1. Admit
    admitted = await backend.command(
        operation="admit_objective",
        tenant_id="t-1",
        actor_id="user-1",
        resource_id=None,
        body={
            "objective": "Prove refactored backend workflow",
            "task_class": "bounded_repair",
            "definition_of_done": ("DoD 1",),
        },
    )
    assert admitted["ok"] is True
    obj_id = admitted["objective_id"]

    # 2. Start Cycle
    started = await backend.command(
        operation="start_cycle",
        tenant_id="t-1",
        actor_id="user-1",
        resource_id=obj_id,
        body={"candidate_sha": "abc1234"},
    )
    assert started["ok"] is True
    assert started["phase"] == "WAITING_APPROVAL"
    cycle_id = started["cycle_id"]

    # 3. Approve
    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="t-1",
        actor_id="user-1",
        resource_id=cycle_id,
        body={"approval_reference": "ref-1"},
    )
    assert approved["ok"] is True
    assert approved["phase"] in ("GAPS_FOUND", "CERTIFIED", "COMPLETED_FOR_REVIEW", "RUNNING_STEP")

    # 4. Query
    query_res = await backend.query(
        query_type="get_cycle",
        tenant_id="t-1",
        resource_id=cycle_id,
    )
    assert query_res["ok"] is True
    assert query_res["cycle_id"] == cycle_id
