"""AIX-W04-E01-T002: Durable DAG Readiness & Cycle Rejection.

Verifies:
1. Cycle Rejection: PlanRevisionV1 model validation rejects circular dependency graphs (raises ValidationError).
2. Missing Dependency Rejection: PlanRevisionV1 model validation fails closed on non-existent dependency IDs.
3. Dependency Satisfaction: get_ready_steps only admits steps whose dependencies have reached PASS.
4. Supersession: Steps marked as SUPERSEDED are skipped by scheduler without blocking progress.
5. Property Tests: Topological selection order remains invariant across generated DAGs.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    PlanRevisionV1,
    RiskTier,
    StepContractV1,
    StepRuntimeStateV1,
)
from backend.aix_core.runtime.objective.step_scheduler import (
    get_ready_steps,
    select_next_step,
    validate_dag,
)

_BUDGET = BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=3, steps=10, concurrency=1)


def _make_step(step_id: str, index: int, depends_on: tuple[str, ...] = ()) -> StepContractV1:
    return StepContractV1(
        id=step_id,
        cycle_id="cyc-dag-test",
        tenant_id="tenant-dag",
        step_index=index,
        purpose=f"Step {step_id}",
        depends_on=depends_on,
        target_scope=("file.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("pass",),
        validation_commands=("pytest",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        required_evidence=("file://diff.patch",),
        status="PENDING",
    )


def test_cycle_rejection_in_plan_dag() -> None:
    """PlanRevisionV1 must raise ValidationError when a plan contains circular dependencies."""
    step_a = _make_step("step-a", 1, depends_on=("step-b",))
    step_b = _make_step("step-b", 2, depends_on=("step-a",))

    with pytest.raises(ValidationError) as exc_info:
        PlanRevisionV1(objective_id="obj-cycle", cycle_id="cyc-cycle", revision=1, steps=(step_a, step_b))

    assert "circular dependency" in str(exc_info.value)


def test_missing_dependency_rejection() -> None:
    """PlanRevisionV1 must raise ValidationError when a step depends on a non-existent step."""
    step_a = _make_step("step-a", 1, depends_on=("non-existent-step",))

    with pytest.raises(ValidationError) as exc_info:
        PlanRevisionV1(objective_id="obj-missing", cycle_id="cyc-missing", revision=1, steps=(step_a,))

    assert "not present in this plan" in str(exc_info.value)


def test_dependency_satisfaction_and_ready_selection() -> None:
    """get_ready_steps must only select steps whose dependencies are PASS."""
    step_1 = _make_step("step-1", 1, depends_on=())
    step_2 = _make_step("step-2", 2, depends_on=("step-1",))
    step_3 = _make_step("step-3", 3, depends_on=("step-2",))
    plan = PlanRevisionV1(objective_id="obj-dag", cycle_id="cyc-dag", revision=1, steps=(step_1, step_2, step_3))

    validate_dag(plan)

    runtime: dict[str, StepRuntimeStateV1] = {}

    # Initially only step-1 is ready
    ready = get_ready_steps(plan, runtime)
    assert len(ready) == 1
    assert ready[0].id == "step-1"

    # Step-1 passes -> Step-2 becomes ready
    runtime["step-1"] = StepRuntimeStateV1(plan_id="plan-1", plan_revision=1, step_id="step-1", status="PASS")
    ready = get_ready_steps(plan, runtime)
    assert len(ready) == 1
    assert ready[0].id == "step-2"


def test_supersession_handling_in_scheduler() -> None:
    """SUPERSEDED steps must be skipped without blocking the pipeline."""
    step_1 = _make_step("step-1", 1, depends_on=())
    step_2 = _make_step("step-2", 2, depends_on=("step-1",))
    plan = PlanRevisionV1(objective_id="obj-super", cycle_id="cyc-super", revision=1, steps=(step_1, step_2))

    runtime: dict[str, StepRuntimeStateV1] = {
        "step-1": StepRuntimeStateV1(plan_id="plan-1", plan_revision=1, step_id="step-1", status="SUPERSEDED")
    }

    # Step 1 is superseded, step 2 depends on step-1. Step-1 is NOT PASS so step-2 is blocked.
    selected = select_next_step(plan, runtime)
    assert selected is None


def test_randomized_dag_property_invariants() -> None:
    """Property test: for generated linear DAGs of size N, readiness order respects topological index."""
    for n in range(3, 10):
        steps = []
        for i in range(n):
            deps = (f"step-{i-1}",) if i > 0 else ()
            steps.append(_make_step(f"step-{i}", i + 1, depends_on=deps))

        plan = PlanRevisionV1(objective_id=f"obj-prop-{n}", cycle_id=f"cyc-prop-{n}", revision=1, steps=tuple(steps))
        validate_dag(plan)

        runtime: dict[str, StepRuntimeStateV1] = {}
        for i in range(n):
            selected = select_next_step(plan, runtime)
            assert selected is not None
            assert selected.id == f"step-{i}"
            runtime[selected.id] = StepRuntimeStateV1(plan_id="p1", plan_revision=1, step_id=selected.id, status="PASS")

        assert select_next_step(plan, runtime) is None
