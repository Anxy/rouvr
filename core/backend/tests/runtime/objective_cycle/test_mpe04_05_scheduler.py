"""AIX-MPE-003 Packets MPE-04 / MPE-05: StepRuntimeState and deterministic scheduler."""
from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    ObjectiveObligationV1,
    PlanRevisionV1,
    RiskTier,
    StepContractV1,
)
from backend.aix_core.runtime.objective.step_runtime import init_step_runtime, replace_runtime_status
from backend.aix_core.runtime.objective.step_scheduler import (
    all_required_steps_passed,
    get_ready_steps,
    remaining_required_obligation_ids,
    select_next_step,
)

_BUDGET = BudgetV1(time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1)


def _step(suffix: str, *, depends_on: tuple[str, ...] = (), obligation_refs: tuple[str, ...] = ()) -> StepContractV1:
    return StepContractV1(
        id=f"step-{suffix}",
        cycle_id="cycle-1",
        tenant_id="tenant-1",
        step_index=int(suffix[1:]) - 1,
        purpose=f"do {suffix}",
        depends_on=depends_on,
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


def _four_step_plan() -> PlanRevisionV1:
    s1 = _step("s1", obligation_refs=("ob-1",))
    s2 = _step("s2", depends_on=("step-s1",), obligation_refs=("ob-2",))
    s3 = _step("s3", depends_on=("step-s1",), obligation_refs=("ob-3",))
    s4 = _step("s4", depends_on=("step-s2", "step-s3"), obligation_refs=("ob-4",))
    return PlanRevisionV1(objective_id="obj-1", cycle_id="cycle-1", revision=1, steps=(s1, s2, s3, s4))


def test_runtime_init_does_not_mutate_contracts() -> None:
    plan = _four_step_plan()
    original_status = tuple(s.status for s in plan.steps)
    runtime = init_step_runtime(plan)
    assert all(st.status == "PENDING" for st in runtime.values())
    assert tuple(s.status for s in plan.steps) == original_status


def test_four_step_plan_shows_s1_pass_s2_s3_ready_s4_blocked() -> None:
    plan = _four_step_plan()
    runtime = init_step_runtime(plan)
    runtime["step-s1"] = replace_runtime_status(runtime["step-s1"], status="PASS")
    ready = get_ready_steps(plan, runtime)
    assert [s.id for s in ready] == ["step-s2", "step-s3"]
    nxt = select_next_step(plan, runtime)
    assert nxt is not None
    assert nxt.id == "step-s2"
    assert runtime["step-s4"].status == "PENDING"
    assert all_required_steps_passed(plan, runtime) is False


def test_remaining_obligations_survive_when_a_required_ref_was_never_scheduled() -> None:
    plan = _four_step_plan()
    runtime = init_step_runtime(plan)
    for sid in runtime:
        runtime[sid] = replace_runtime_status(runtime[sid], status="PASS")
    obs = (
        ObjectiveObligationV1(id="ob-1", objective_id="obj-1", description="a", required=True),
        ObjectiveObligationV1(id="ob-2", objective_id="obj-1", description="b", required=True),
        ObjectiveObligationV1(id="ob-3", objective_id="obj-1", description="c", required=True),
        ObjectiveObligationV1(id="ob-4", objective_id="obj-1", description="d", required=True),
        ObjectiveObligationV1(id="ob-5", objective_id="obj-1", description="e", required=True),
    )
    remaining = remaining_required_obligation_ids(plan=plan, runtime=runtime, obligations=obs)
    assert remaining == ("ob-5",)
    assert all_required_steps_passed(plan, runtime) is True
