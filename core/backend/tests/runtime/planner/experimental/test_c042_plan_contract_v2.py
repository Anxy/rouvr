"""C042 PlanRevision/StepContract V2 binding tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    PlanRevisionV1,
    StepContractV1,
)
from backend.aix_core.runtime.planner.experimental.plan_contract_v2 import (
    PlanContractV2Error,
    StepBindingsV2,
    bind_plan_revision_v2,
    classify_prior_steps_from_v1,
)


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=5, concurrency=1)


def _step(step_index: int, *, purpose: str, scope: tuple[str, ...], status: str = "PENDING") -> StepContractV1:
    return StepContractV1(
        cycle_id="cyc-1",
        tenant_id="t1",
        step_index=step_index,
        purpose=purpose,
        target_scope=scope,
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("ok",),
        validation_commands=("pytest -q",),
        assigned_role="executor",
        step_budget=_budget(),
        status=status,  # type: ignore[arg-type]
    )


def _bindings_for(step: StepContractV1) -> StepBindingsV2:
    return StepBindingsV2(
        step_id=step.id,
        capability_ids=("file.edit",),
        test_refs=("tests/test_mod.py",),
        evidence_refs=("evidence://patch",),
        reversibility="PARTIAL",
        failure_policy="REPAIR",
        effect_bounds=step.target_scope,
        context_manifest_ref="ctx://1",
        repository_snapshot_ref="snap://1",
    )


def test_v2_requires_full_bindings_per_step() -> None:
    s0 = _step(0, purpose="edit helper", scope=("helper.py",))
    plan = PlanRevisionV1(
        objective_id="obj-1",
        cycle_id="cyc-1",
        revision=1,
        steps=(s0,),
    )
    with pytest.raises(PlanContractV2Error) as exc:
        bind_plan_revision_v2(plan=plan, bindings=[])
    assert exc.value.code == "MISSING_STEP_BINDING"

    bad = StepBindingsV2(
        step_id=s0.id,
        capability_ids=(),
        test_refs=("t",),
        evidence_refs=("e",),
        reversibility="NONE",
        failure_policy="HALT",
        effect_bounds=("helper.py",),
    )
    with pytest.raises(PlanContractV2Error) as exc2:
        bind_plan_revision_v2(plan=plan, bindings=[bad])
    assert exc2.value.code == "CAPABILITY_REQUIRED"

    v2 = bind_plan_revision_v2(plan=plan, bindings=[_bindings_for(s0)])
    assert v2.schema == "aix.plan_contract.v2"
    assert len(v2.step_bindings) == 1


def test_new_revision_must_classify_prior_steps() -> None:
    s0 = _step(0, purpose="step0", scope=("a.py",), status="PENDING")
    s1 = _step(1, purpose="step1", scope=("b.py",))
    prior = PlanRevisionV1(objective_id="obj-1", cycle_id="cyc-1", revision=1, steps=(s0,))
    nxt = PlanRevisionV1(
        objective_id="obj-1",
        cycle_id="cyc-1",
        revision=2,
        steps=(s0, s1),
        created_from_plan_revision=prior.plan_id,
    )
    with pytest.raises(PlanContractV2Error) as exc:
        bind_plan_revision_v2(
            plan=nxt,
            bindings=[_bindings_for(s0), _bindings_for(s1)],
            created_from_plan_revision=prior.plan_id,
        )
    assert exc.value.code == "PRIOR_STEP_CLASSIFICATION_REQUIRED"

    classes = classify_prior_steps_from_v1(
        prior_plan=prior,
        completed_step_ids={s0.id},
    )
    assert classes[0].classification == "COMPLETED"
    v2 = bind_plan_revision_v2(
        plan=nxt,
        bindings=[_bindings_for(s0), _bindings_for(s1)],
        prior_step_classifications=classes,
        created_from_plan_revision=prior.plan_id,
        strategy_comparison_ref="strategy://cmp1",
    )
    assert v2.revision == 2
    assert v2.prior_step_classifications[0].classification == "COMPLETED"


def test_effect_bound_outside_scope_rejected() -> None:
    s0 = _step(0, purpose="edit", scope=("a.py",))
    plan = PlanRevisionV1(objective_id="obj-1", cycle_id="cyc-1", revision=1, steps=(s0,))
    b = StepBindingsV2(
        step_id=s0.id,
        capability_ids=("file.edit",),
        test_refs=("t",),
        evidence_refs=("e",),
        reversibility="FULL",
        failure_policy="HALT",
        effect_bounds=("other.py",),
    )
    with pytest.raises(PlanContractV2Error) as exc:
        bind_plan_revision_v2(plan=plan, bindings=[b])
    assert exc.value.code == "EFFECT_BOUND_OUTSIDE_TARGET_SCOPE"
