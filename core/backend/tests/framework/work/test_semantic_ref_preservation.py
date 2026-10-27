"""F1-P4: WorkUnit/WorkContract preserve PD-003B2 semantic refs (F1-AC-04)."""

from __future__ import annotations

from backend.aix_core.framework.work.adapters import (
    WorkContractAdapter,
    WorkGraphAdapter,
    WorkUnitAdapter,
)
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    PlanRevisionV1,
    StepContractV1,
)


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=3, concurrency=1)


def _step(
    *,
    step_id: str,
    index: int,
    depends_on: tuple[str, ...] = (),
) -> StepContractV1:
    return StepContractV1(
        id=step_id,
        cycle_id="cyc-1",
        tenant_id="t1",
        step_index=index,
        purpose=f"purpose-{step_id}",
        depends_on=depends_on,
        target_scope=(f"{step_id}.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("done",),
        validation_commands=("true",),
        assigned_role="executor",
        step_budget=_budget(),
        obligation_refs=("obl-1",),
        outcome_contract_id="oc-1",
        outcome_contract_hash="hash-1",
        facet_refs=("UI", "API"),
        operation_refs=("op.create",),
        entity_refs=("Student",),
        mapping_refs=("map.ui_api",),
        binding_role="IMPLEMENT",
        expected_checks=("presence",),
        status="PENDING",
    )


def test_work_contract_preserves_semantic_refs() -> None:
    step = _step(step_id="s1", index=0)
    contract = WorkContractAdapter.from_step_contract(step)
    assert contract.obligation_refs == ("obl-1",)
    assert contract.outcome_contract_id == "oc-1"
    assert contract.outcome_contract_hash == "hash-1"
    assert contract.facet_refs == ("UI", "API")
    assert contract.operation_refs == ("op.create",)
    assert contract.entity_refs == ("Student",)
    assert contract.mapping_refs == ("map.ui_api",)
    assert contract.binding_role == "IMPLEMENT"
    assert contract.expected_checks == ("presence",)


def test_work_unit_from_step_preserves_scope_and_deps() -> None:
    s1 = _step(step_id="s1", index=0)
    s2 = _step(step_id="s2", index=1, depends_on=("s1",))
    u2 = WorkUnitAdapter.from_step_contract(s2, work_id="obj-1")
    assert u2.unit_id == "s2"
    assert u2.depends_on == ("s1",)
    assert u2.target_scope == ("s2.py",)
    assert u2.contract.facet_refs == ("UI", "API")


def test_work_graph_dependency_edges_match_plan() -> None:
    s1 = _step(step_id="s1", index=0)
    s2 = _step(step_id="s2", index=1, depends_on=("s1",))
    s3 = _step(step_id="s3", index=2, depends_on=("s1", "s2"))
    plan = PlanRevisionV1(
        objective_id="obj-1",
        cycle_id="cyc-1",
        revision=1,
        steps=(s1, s2, s3),
        status="ACCEPTED",
    )
    graph = WorkGraphAdapter.from_plan(plan)
    assert WorkGraphAdapter.dependency_edges_match_plan(plan, graph)
    assert set(graph.dependency_edges()) == {
        ("s2", "s1"),
        ("s3", "s1"),
        ("s3", "s2"),
    }
