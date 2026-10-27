"""AIX-MPE-003 Packet MPE-03: plan quality gate rejects incomplete / catch-all plans."""
from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    ObjectiveObligationV1,
    PlanRevisionV1,
    RiskTier,
    StepContractV1,
)
from backend.aix_core.runtime.planner.plan_quality import evaluate_plan_quality

_BUDGET = BudgetV1(time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1)


def _step(suffix: str, *, obligation_refs: tuple[str, ...] = ()) -> StepContractV1:
    return StepContractV1(
        id=f"step-{suffix}",
        cycle_id="cycle-1",
        tenant_id="tenant-1",
        step_index=int(suffix[1:]) - 1,
        purpose=f"do {suffix}",
        target_scope=("file.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("done",),
        validation_commands=("true",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        obligation_refs=obligation_refs,
        status="PENDING",
    )


def test_omitted_required_obligation_is_plan_incomplete() -> None:
    obs = (
        ObjectiveObligationV1(id="ob-1", objective_id="obj-1", description="group methods", required=True),
        ObjectiveObligationV1(id="ob-2", objective_id="obj-1", description="extract helpers", required=True),
        ObjectiveObligationV1(id="ob-3", objective_id="obj-1", description="remove unused imports", required=True),
    )
    plan = PlanRevisionV1(
        objective_id="obj-1",
        cycle_id="cycle-1",
        revision=1,
        steps=(_step("s1", obligation_refs=("ob-1", "ob-2")),),
    )
    result = evaluate_plan_quality(plan=plan, obligations=obs)
    assert result.ok is False
    assert result.code == "OBLIGATION_COVERAGE_INCOMPLETE"
    assert "remove unused imports" in result.detail
    assert result.uncovered_obligation_ids == ("ob-3",)


def test_single_catch_all_step_for_three_obligations_is_quality_rejected() -> None:
    obs = (
        ObjectiveObligationV1(id="ob-1", objective_id="obj-1", description="a", required=True),
        ObjectiveObligationV1(id="ob-2", objective_id="obj-1", description="b", required=True),
        ObjectiveObligationV1(id="ob-3", objective_id="obj-1", description="c", required=True),
    )
    plan = PlanRevisionV1(
        objective_id="obj-1",
        cycle_id="cycle-1",
        revision=1,
        steps=(_step("s1", obligation_refs=("ob-1", "ob-2", "ob-3")),),
    )
    result = evaluate_plan_quality(plan=plan, obligations=obs)
    assert result.ok is False
    assert result.code == "COMPLEX_OBJECTIVE_COLLAPSED"


def test_bounded_multi_step_plan_is_accepted() -> None:
    obs = (
        ObjectiveObligationV1(id="ob-1", objective_id="obj-1", description="a", required=True),
        ObjectiveObligationV1(id="ob-2", objective_id="obj-1", description="b", required=True),
        ObjectiveObligationV1(id="ob-3", objective_id="obj-1", description="c", required=True),
    )
    plan = PlanRevisionV1(
        objective_id="obj-1",
        cycle_id="cycle-1",
        revision=1,
        steps=(
            _step("s1", obligation_refs=("ob-1",)),
            StepContractV1(
                id="step-s2", cycle_id="cycle-1", tenant_id="tenant-1", step_index=1,
                purpose="do s2", depends_on=("step-s1",), target_scope=("file.py",),
                expected_output_schema_ref="aix.step_contract.v1.generic_edit",
                acceptance_criteria=("done",), validation_commands=("true",),
                assigned_role="executor", risk_tier=RiskTier.LOW, step_budget=_BUDGET,
                obligation_refs=("ob-2",), status="PENDING",
            ),
            StepContractV1(
                id="step-s3", cycle_id="cycle-1", tenant_id="tenant-1", step_index=2,
                purpose="do s3", depends_on=("step-s2",), target_scope=("file.py",),
                expected_output_schema_ref="aix.step_contract.v1.generic_edit",
                acceptance_criteria=("done",), validation_commands=("true",),
                assigned_role="executor", risk_tier=RiskTier.LOW, step_budget=_BUDGET,
                obligation_refs=("ob-3",), status="PENDING",
            ),
        ),
    )
    result = evaluate_plan_quality(plan=plan, obligations=obs)
    assert result.ok is True
    assert result.code == "PLAN_ACCEPTED"


def test_generic_acceptance_with_objective_is_rejected() -> None:
    obs = (ObjectiveObligationV1(id="ob-1", objective_id="obj-1", description="change timeout", required=True),)
    plan = PlanRevisionV1(
        objective_id="obj-1",
        cycle_id="cycle-1",
        revision=1,
        steps=(_step("s1", obligation_refs=("ob-1",)),),
    )
    result = evaluate_plan_quality(
        plan=plan,
        obligations=obs,
        objective="Change the existing retry timeout from 30 to 60 while preserving all other configuration.",
    )
    assert result.ok is False
    assert result.code == "GENERIC_ACCEPTANCE_CRITERIA"


def test_legacy_one_step_rejected_for_multi_obligation_objective() -> None:
    obs = (
        ObjectiveObligationV1(id="ob-1", objective_id="obj-1", description="configuration supports retry limit", required=True),
        ObjectiveObligationV1(id="ob-2", objective_id="obj-1", description="service consumes retry limit", required=True),
        ObjectiveObligationV1(id="ob-3", objective_id="obj-1", description="tests prove configured behavior", required=True),
    )
    step = StepContractV1(
        id="step-s1",
        cycle_id="cycle-1",
        tenant_id="tenant-1",
        step_index=0,
        purpose="add retry limit across config service and tests",
        target_scope=("file.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("retry limit is configurable, consumed, and covered by tests",),
        validation_commands=("true",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        obligation_refs=("ob-1", "ob-2", "ob-3"),
        status="PENDING",
    )
    plan = PlanRevisionV1(
        objective_id="obj-1",
        cycle_id="cycle-1",
        revision=1,
        steps=(step,),
    )
    result = evaluate_plan_quality(
        plan=plan,
        obligations=obs,
        objective="Add a configurable retry limit, make the service consume it, and add tests proving the configured behavior.",
        used_legacy_one_step=True,
    )
    assert result.ok is False
    assert result.code in {"COMPLEX_OBJECTIVE_COLLAPSED", "OBJECTIVE_SCOPE_NOT_COVERED"}
