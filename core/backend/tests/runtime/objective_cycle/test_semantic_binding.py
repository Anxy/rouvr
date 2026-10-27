"""AIX-DoD-002E: semantic binding LEGITIMATE / UNSUBSTANTIATED / SCOPE_MISMATCH."""

from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1
from backend.aix_core.runtime.objective.outcome_contract import (
    ApiFacetV1,
    ApiOperationSpecV1,
    DbFacetV1,
    IntegrationFacetV1,
    IntegrationMappingV1,
    OutcomeContractV1,
    OutcomeFacetsV1,
    UiFacetV1,
    seal_outcome_contract,
)
from backend.aix_core.runtime.objective.outcome_contract_proposer import bind_steps_to_contract
from backend.aix_core.runtime.objective.outcome_obligations import compile_outcome_obligations
from backend.aix_core.runtime.objective.obligation_step_seed import compile_obligation_step_seed
from backend.aix_core.runtime.planner.step_contract_planner import materialize_obligation_seed_steps
from backend.aix_core.runtime.verification.contract_coverage import evaluate_contract_coverage
from backend.aix_core.runtime.verification.semantic_binding import (
    evaluate_semantic_bindings,
    judge_operation_claim,
)


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=10, concurrency=1)


def _contract() -> OutcomeContractV1:
    return seal_outcome_contract(
        OutcomeContractV1(
            objective_id="obj_002e",
            definition_of_done=("Staff register students and manage attendance",),
            facets=OutcomeFacetsV1(
                UI=UiFacetV1(
                    required_operations=("register_student", "view_attendance", "submit_attendance"),
                    required_views=("reg", "view", "submit"),
                    acceptance_criteria=("UI ops present",),
                ),
                API=ApiFacetV1(
                    required_operations=(
                        ApiOperationSpecV1(id="students.create", path_or_action="POST /students"),
                        ApiOperationSpecV1(id="attendance.list", path_or_action="GET /attendance"),
                        ApiOperationSpecV1(id="attendance.create", path_or_action="POST /attendance"),
                    ),
                    acceptance_criteria=("API ops present",),
                ),
                DB=DbFacetV1(
                    entities=("students", "attendance_records"),
                    acceptance_criteria=("DB entities present",),
                ),
                INTEGRATION=IntegrationFacetV1(
                    mappings=(
                        IntegrationMappingV1(
                            id="map-0",
                            from_facet="UI",
                            from_id="register_student",
                            to_facet="API",
                            to_id="students.create",
                        ),
                        IntegrationMappingV1(
                            id="map-1",
                            from_facet="UI",
                            from_id="view_attendance",
                            to_facet="API",
                            to_id="attendance.list",
                        ),
                        IntegrationMappingV1(
                            id="map-2",
                            from_facet="UI",
                            from_id="submit_attendance",
                            to_facet="API",
                            to_id="attendance.create",
                        ),
                        IntegrationMappingV1(
                            id="map-3",
                            from_facet="API",
                            from_id="students.create",
                            to_facet="DB",
                            to_id="students",
                        ),
                        IntegrationMappingV1(
                            id="map-4",
                            from_facet="API",
                            from_id="attendance.list",
                            to_facet="DB",
                            to_id="attendance_records",
                        ),
                        IntegrationMappingV1(
                            id="map-5",
                            from_facet="API",
                            from_id="attendance.create",
                            to_facet="DB",
                            to_id="attendance_records",
                        ),
                    ),
                    acceptance_criteria=("mappings compose",),
                ),
            ),
        )
    )


def _step(**kwargs: object) -> StepContractV1:
    base = dict(
        cycle_id="c",
        tenant_id="t",
        step_index=0,
        purpose="x",
        target_scope=("proposed/x.impl",),
        acceptance_criteria=("y",),
        validation_commands=("echo",),
        expected_output_schema_ref="text",
        assigned_role="executor",
        step_budget=_budget(),
        status="PENDING",
    )
    base.update(kwargs)
    return StepContractV1(**base)  # type: ignore[arg-type]


def test_legitimate_register_binding() -> None:
    step = _step(
        purpose="IMPLEMENT register_student with students.create",
        acceptance_criteria=("register_student form posts to students.create",),
        target_scope=("proposed/obligation_seed/ui_register_student.impl",),
        facet_refs=("UI", "API"),
        operation_refs=("register_student", "students.create"),
    )
    j = judge_operation_claim(step=step, operation="register_student", facet="UI")
    assert j.verdict == "LEGITIMATE"
    j2 = judge_operation_claim(step=step, operation="students.create", facet="API")
    assert j2.verdict == "LEGITIMATE"


def test_fake_binding_unsubstantiated() -> None:
    """Step claims submit_attendance but only talks about inventory."""
    step = _step(
        purpose="Build inventory dashboard widgets",
        acceptance_criteria=("inventory kpi cards render",),
        target_scope=("index.html",),
        facet_refs=("UI",),
        operation_refs=("submit_attendance",),
    )
    j = judge_operation_claim(step=step, operation="submit_attendance", facet="UI")
    assert j.verdict == "UNSUBSTANTIATED"


def test_scope_mismatch() -> None:
    step = _step(
        purpose="tune SQL indexes only",
        acceptance_criteria=("index exists",),
        target_scope=("database.sql",),
        facet_refs=("UI",),
        operation_refs=("register_student",),
    )
    j = judge_operation_claim(step=step, operation="register_student", facet="UI")
    assert j.verdict in {"SCOPE_MISMATCH", "UNSUBSTANTIATED"}


def test_smoking_gun_mega_step_fails_coverage() -> None:
    contract = _contract()
    mega = _step(
        purpose="Create the complete frontend, backend, database schema, and documentation",
        acceptance_criteria=("entire attendance product works",),
        target_scope=(
            "proposed/attendance-system/index.html",
            "proposed/attendance-system/api.php",
            "proposed/attendance-system/database.sql",
        ),
        outcome_contract_id=contract.contract_id,
        outcome_contract_hash=contract.contract_hash,
        facet_refs=("UI", "API", "DB"),
        operation_refs=(
            "register_student",
            "view_attendance",
            "submit_attendance",
            "students.create",
            "attendance.list",
            "attendance.create",
        ),
        entity_refs=("students", "attendance_records"),
        mapping_refs=("map-0", "map-1", "map-2", "map-3", "map-4", "map-5"),
        binding_role="IMPLEMENT",
    )
    binding = evaluate_semantic_bindings(outcome_contract=contract, steps=(mega,))
    assert binding.ok is False
    assert binding.code == "CONTRACT_BINDING_UNSUBSTANTIATED"

    cov = evaluate_contract_coverage(
        outcome_contract=contract,
        steps=(mega,),
        require_outcome_contract=True,
    )
    assert cov.ok is False
    assert cov.code == "CONTRACT_BINDING_UNSUBSTANTIATED"


def test_bind_does_not_stamp_all_ui_ops_onto_html() -> None:
    contract = _contract()
    step = _step(
        purpose="build frontend UI",
        target_scope=("index.html",),
        acceptance_criteria=("page exists",),
    )
    bound = bind_steps_to_contract(steps=(step,), contract=contract)[0]
    ui_ops = set(contract.facets.UI.required_operations)
    assert not ui_ops.issubset(set(bound.operation_refs))
    assert bound.outcome_contract_id == contract.contract_id


def test_seed_materialized_plan_passes_semantic_binding() -> None:
    contract = _contract()
    compiled = compile_outcome_obligations(contract)
    assert compiled.ok
    seed = compile_obligation_step_seed(compiled.obligations)
    assert seed.ok, seed.detail
    steps = materialize_obligation_seed_steps(
        seed,
        tenant_id="t",
        cycle_id="c",
        assigned_role="executor",
        step_budget=_budget(),
        outcome_contract_id=contract.contract_id,
        outcome_contract_hash=contract.contract_hash,
    )
    bound = bind_steps_to_contract(steps=tuple(steps), contract=contract)
    binding = evaluate_semantic_bindings(outcome_contract=contract, steps=bound)
    assert binding.ok, binding.detail
    cov = evaluate_contract_coverage(
        outcome_contract=contract,
        steps=bound,
        require_outcome_contract=True,
    )
    assert cov.ok, cov.detail
    assert cov.code == "CONTRACT_COVERAGE_OK"
