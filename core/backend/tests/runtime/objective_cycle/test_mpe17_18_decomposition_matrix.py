"""AIX-MPE-003 Packets MPE-17 / MPE-18: mocked decomposition matrix (no live model)."""
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


def _step(step_id: str, index: int, *, obligation_refs: tuple[str, ...] = ()) -> StepContractV1:
    return StepContractV1(
        id=step_id, cycle_id="c1", tenant_id="t1", step_index=index, purpose=step_id,
        target_scope=("CronJobController.php",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("done",), validation_commands=("php -l CronJobController.php",),
        assigned_role="executor", risk_tier=RiskTier.LOW, step_budget=_BUDGET,
        obligation_refs=obligation_refs, status="PENDING",
    )


def test_trivial_one_step_edit_is_accepted() -> None:
    obs = (ObjectiveObligationV1(id="ob-1", objective_id="o", description="rename local var", required=True),)
    plan = PlanRevisionV1(objective_id="o", cycle_id="c1", revision=1, steps=(_step("s1", 0, obligation_refs=("ob-1",)),))
    assert evaluate_plan_quality(plan=plan, obligations=obs).ok is True


def test_cronjob_class_objective_rejects_one_giant_step() -> None:
    obs = tuple(
        ObjectiveObligationV1(id=f"ob-{i}", objective_id="o", description=desc, required=True)
        for i, desc in enumerate(
            (
                "group public methods by domain",
                "extract DB helpers",
                "remove unused imports",
                "preserve public method names",
                "preserve HTTP/middleware behavior",
            ),
            start=1,
        )
    )
    plan = PlanRevisionV1(
        objective_id="o", cycle_id="c1", revision=1,
        steps=(_step("s1", 0, obligation_refs=tuple(o.id for o in obs)),),
    )
    result = evaluate_plan_quality(plan=plan, obligations=obs)
    assert result.ok is False
    assert result.code == "COMPLEX_OBJECTIVE_COLLAPSED"
