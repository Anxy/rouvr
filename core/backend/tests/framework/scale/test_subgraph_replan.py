"""F11-AC-04: Partial failure → quarantine + canonical incremental_replan."""

from __future__ import annotations

from backend.aix_core.framework.scale.failure_domain import FailureDomainManager
from backend.aix_core.framework.scale.work_graph_store import WorkGraphStore
from backend.aix_core.framework.work.models import UnitStatus, WorkContract, WorkGraph, WorkUnit
from backend.aix_core.runtime.objective.contracts import BudgetV1, PlanRevisionV1, StepContractV1


def _budget() -> BudgetV1:
    return BudgetV1(
        time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=10, concurrency=1
    )


def test_subgraph_replan_uses_incremental_replan():
    budget = _budget()
    # Branch A: a0 -> a1 ; Branch B: b0 -> b1  (independent)
    steps = (
        StepContractV1(
            id="a0",
            cycle_id="c",
            tenant_id="t",
            step_index=0,
            purpose="a0",
            target_scope=("a0.py",),
            expected_output_schema_ref="aix.step_contract.v1.generic_edit",
            acceptance_criteria=("ok",),
            validation_commands=("true",),
            assigned_role="executor",
            step_budget=budget,
            status="PENDING",
        ),
        StepContractV1(
            id="a1",
            cycle_id="c",
            tenant_id="t",
            step_index=1,
            purpose="a1",
            depends_on=("a0",),
            target_scope=("a1.py",),
            expected_output_schema_ref="aix.step_contract.v1.generic_edit",
            acceptance_criteria=("ok",),
            validation_commands=("true",),
            assigned_role="executor",
            step_budget=budget,
            status="PENDING",
        ),
        StepContractV1(
            id="b0",
            cycle_id="c",
            tenant_id="t",
            step_index=2,
            purpose="b0",
            target_scope=("b0.py",),
            expected_output_schema_ref="aix.step_contract.v1.generic_edit",
            acceptance_criteria=("ok",),
            validation_commands=("true",),
            assigned_role="executor",
            step_budget=budget,
            status="PENDING",
        ),
        StepContractV1(
            id="b1",
            cycle_id="c",
            tenant_id="t",
            step_index=3,
            purpose="b1",
            depends_on=("b0",),
            target_scope=("b1.py",),
            expected_output_schema_ref="aix.step_contract.v1.generic_edit",
            acceptance_criteria=("ok",),
            validation_commands=("true",),
            assigned_role="executor",
            step_budget=budget,
            status="PENDING",
        ),
    )
    plan = PlanRevisionV1(
        plan_id="p_fail",
        objective_id="obj_fail",
        cycle_id="c",
        revision=1,
        status="ACCEPTED",
        steps=steps,
    )
    units = tuple(
        WorkUnit(
            unit_id=s.id,
            work_id="obj_fail",
            unit_index=s.step_index,
            purpose=s.purpose,
            depends_on=tuple(s.depends_on),
            target_scope=tuple(s.target_scope),
            contract=WorkContract(unit_id=s.id),
        )
        for s in steps
    )
    graph = WorkGraph(graph_id="g_fail", work_id="obj_fail", units=units)
    store = WorkGraphStore()
    store.save_work_graph(graph)

    replacement = StepContractV1(
        id="a0_retry",
        cycle_id="c",
        tenant_id="t",
        step_index=10,
        purpose="a0 replacement",
        target_scope=("a0.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("ok",),
        validation_commands=("true",),
        assigned_role="executor",
        step_budget=budget,
        status="PENDING",
    )

    mgr = FailureDomainManager(store)
    domain = mgr.quarantine_and_replan_subgraph(
        "g_fail",
        "a0",
        plan=plan,
        replacement_steps=(replacement,),
    )
    assert domain.replan is not None
    assert "a0" in domain.replan.superseded_step_ids
    assert "a1" in domain.replan.superseded_step_ids
    assert "a0_retry" in domain.replan.inserted_step_ids
    # Unaffected branch preserved
    assert "b0" in domain.unaffected_unit_ids
    assert "b1" in domain.unaffected_unit_ids
    assert store.get_unit("g_fail", "a0").status == UnitStatus.FAILED
    assert store.get_unit("g_fail", "a1").status == UnitStatus.SKIPPED
    assert store.get_unit("g_fail", "b0").status == UnitStatus.PENDING
    # Replacement bound into graph
    assert store.get_unit("g_fail", "a0_retry") is not None
    assert store.get_unit("g_fail", "a0_retry").status == UnitStatus.PENDING
