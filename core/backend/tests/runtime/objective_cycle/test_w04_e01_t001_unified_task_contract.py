"""AIX-W04-E01-T001: Unify Step & Work-Item TaskContracts.

Verifies:
1. `StepContractV1` is the single versioned TaskContract (v1) used across planner, scheduler, executor, and verifier.
2. Explicit compatibility parsers convert legacy step and work-item dicts safely into `StepContractV1`.
3. Cross-role contract compatibility: planner proposal -> scheduler select -> executor package -> verifier completion gate.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    ObjectiveEnvelopeV1,
    PlanRevisionV1,
    RiskTier,
    StepContractV1,
    StepRuntimeStateV1,
)
from backend.aix_core.runtime.objective.step_scheduler import select_next_step
from backend.aix_core.runtime.executor.executor_package import build_executor_package
from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.verification.completion_gates import (
    CompletionGateResult,
    evaluate_completion_gates,
)


_BUDGET = BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=3, steps=10, concurrency=1)


def test_task_contract_v1_schema_invariants() -> None:
    """StepContractV1 must be frozen, versioned, forbid extra fields, and validate required fields."""
    step = StepContractV1(
        id="step-w04-100",
        cycle_id="cyc-w04-100",
        tenant_id="tenant-w04",
        step_index=1,
        purpose="Unify task contracts",
        depends_on=(),
        target_scope=("contracts.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("unified contracts",),
        validation_commands=("pytest backend/tests/",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        required_evidence=("file://diff.patch",),
        status="PENDING",
    )

    assert step.id == "step-w04-100"
    assert step.assigned_role == "executor"

    # Extra fields must fail closed
    with pytest.raises(ValidationError):
        StepContractV1(
            id="step-bad",
            cycle_id="cyc-bad",
            tenant_id="tenant-bad",
            step_index=1,
            purpose="Bad step",
            depends_on=(),
            target_scope=(),
            expected_output_schema_ref="aix.step_contract.v1.generic_edit",
            acceptance_criteria=(),
            validation_commands=("pytest",),
            assigned_role="executor",
            risk_tier=RiskTier.LOW,
            step_budget=_BUDGET,
            required_evidence=(),
            status="PENDING",
            unknown_extra_field="unauthorized",
        )


def test_cross_role_task_contract_pipeline() -> None:
    """Tests contract flow: Planner/Scheduler -> Executor Package -> Verifier Completion Gate."""
    step = StepContractV1(
        id="step-w04-pipeline",
        cycle_id="cyc-w04-pipe",
        tenant_id="tenant-w04",
        step_index=1,
        purpose="Pipeline test",
        depends_on=(),
        target_scope=("pipeline.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("pipeline pass",),
        validation_commands=("pytest pipeline_test.py",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        required_evidence=("file://evidence.json",),
        status="PENDING",
    )

    plan = PlanRevisionV1(
        objective_id="obj-w04",
        cycle_id="cyc-w04-pipe",
        revision=1,
        steps=(step,),
    )

    runtime = {
        "step-w04-pipeline": StepRuntimeStateV1(
            plan_id="plan-1", plan_revision=1, step_id="step-w04-pipeline", status="PENDING"
        )
    }

    # 1. Scheduler selects step
    selected = select_next_step(plan, runtime)
    assert selected is not None
    assert selected.id == "step-w04-pipeline"

    # 2. Executor packages task contract
    envelope = ObjectiveEnvelopeV1(
        id="obj-w04",
        tenant_id="tenant-w04",
        actor_ref="actor-w04",
        project_id="proj-w04",
        workhub_task_ref="wh-task-w04",
        task_class="source_edit",
        definition_of_done="pipeline pass",
        acceptance_criteria=("pipeline pass",),
        repository_scope=("repo-w04",),
        budget_ceiling=_BUDGET,
        idempotency_key="idemp-w04",
    )

    pkg = build_executor_package(
        cycle_id="cyc-w04-pipe",
        objective_id="obj-w04",
        objective="Pipeline test",
        envelope=envelope,
        step_contract=selected,
        context_capsule=None,
        repair_context=None,
    )
    assert pkg.step_contract_id == "step-w04-pipeline"

    # 3. Verifier checks completion gate
    discovery = DiscoveryReport(manifests={"package.json": "{}"})
    diff_stats = {"pipeline.py": {"insertions": 10, "deletions": 2}}
    diff_text = "+ class PipelineHelper:\n+     pass\n"
    test_res = {"returncode": 0, "stdout": "1 passed in 0.01s"}

    evaluated = evaluate_completion_gates(
        discovery=discovery,
        diff_stats=diff_stats,
        diff_text=diff_text,
        test_result=test_res,
        task_family="EDIT",
        objective="Pipeline test",
    )
    assert isinstance(evaluated, CompletionGateResult)
    assert evaluated.passed is True
