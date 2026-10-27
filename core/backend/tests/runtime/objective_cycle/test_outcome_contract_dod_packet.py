"""DoD architecture packet: OutcomeContract, CoverageGate, IntegrationProof, finalize conjuncts."""

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
    compute_outcome_contract_hash,
    seal_outcome_contract,
    validate_outcome_contract_structure,
)
from backend.aix_core.runtime.verification.contract_coverage import evaluate_contract_coverage
from backend.aix_core.runtime.verification.integration_proof import (
    evaluate_integration_proof,
    evaluate_objective_proven,
)


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=5, concurrency=1)


def _attendance_contract() -> OutcomeContractV1:
    m1 = IntegrationMappingV1(
        id="map-ui-reg",
        from_facet="UI",
        from_id="register_student",
        to_facet="API",
        to_id="students.create",
    )
    m2 = IntegrationMappingV1(
        id="map-api-students",
        from_facet="API",
        from_id="students.create",
        to_facet="DB",
        to_id="students",
    )
    m3 = IntegrationMappingV1(
        id="map-ui-view",
        from_facet="UI",
        from_id="view_attendance",
        to_facet="API",
        to_id="attendance.list",
    )
    m4 = IntegrationMappingV1(
        id="map-api-att-list",
        from_facet="API",
        from_id="attendance.list",
        to_facet="DB",
        to_id="attendance",
    )
    m5 = IntegrationMappingV1(
        id="map-ui-submit",
        from_facet="UI",
        from_id="submit_attendance",
        to_facet="API",
        to_id="attendance.create",
    )
    m6 = IntegrationMappingV1(
        id="map-api-att-create",
        from_facet="API",
        from_id="attendance.create",
        to_facet="DB",
        to_id="attendance",
    )
    return seal_outcome_contract(
        OutcomeContractV1(
        objective_id="obj_test",
        definition_of_done=("Students can be registered and attendance submitted via UI/API/DB",),
        facets=OutcomeFacetsV1(
            UI=UiFacetV1(
                required_operations=("register_student", "view_attendance", "submit_attendance"),
                required_views=("register", "attendance"),
                acceptance_criteria=("UI exposes register and attendance flows",),
            ),
            API=ApiFacetV1(
                required_operations=(
                    ApiOperationSpecV1(id="students.create", path_or_action="POST /students"),
                    ApiOperationSpecV1(id="attendance.list", path_or_action="GET /attendance"),
                    ApiOperationSpecV1(id="attendance.create", path_or_action="POST /attendance"),
                ),
                acceptance_criteria=("API implements student and attendance ops",),
            ),
            DB=DbFacetV1(
                entities=("students", "attendance"),
                acceptance_criteria=("Schema has students and attendance",),
            ),
            INTEGRATION=IntegrationFacetV1(
                mappings=(m1, m2, m3, m4, m5, m6),
                acceptance_criteria=("UI ops map to API ops map to DB entities",),
            ),
        ),
        )
    )


def _bound_steps(contract: OutcomeContractV1) -> tuple[StepContractV1, ...]:
    budget = _budget()
    common = dict(
        cycle_id="cyc_test",
        tenant_id="tenant_test",
        outcome_contract_id=contract.contract_id,
        outcome_contract_hash=contract.contract_hash,
        validation_commands=("echo ok",),
        expected_output_schema_ref="text",
        assigned_role="executor",
        step_budget=budget,
        status="PENDING",
    )
    # DoD-002E: coherent units with substantiated claims (not one mega UI file step)
    return (
        StepContractV1(
            step_index=0,
            purpose="IMPLEMENT register_student with students.create",
            target_scope=("proposed/obligation_seed/ui_register_student.impl",),
            acceptance_criteria=("UI register_student and API students.create work",),
            facet_refs=("UI", "API", "INTEGRATION"),
            operation_refs=("register_student", "students.create"),
            mapping_refs=("map-ui-reg",),
            obligation_refs=("obl_ui_register_student", "obl_api_students_create"),
            binding_role="IMPLEMENT",
            **common,
        ),
        StepContractV1(
            step_index=1,
            purpose="IMPLEMENT view_attendance with attendance.list",
            target_scope=("proposed/obligation_seed/ui_view_attendance.impl",),
            acceptance_criteria=("UI view_attendance and API attendance.list work",),
            facet_refs=("UI", "API", "INTEGRATION"),
            operation_refs=("view_attendance", "attendance.list"),
            mapping_refs=("map-ui-view",),
            obligation_refs=("obl_ui_view_attendance", "obl_api_attendance_list"),
            binding_role="IMPLEMENT",
            **common,
        ),
        StepContractV1(
            step_index=2,
            purpose="IMPLEMENT submit_attendance with attendance.create",
            target_scope=("proposed/obligation_seed/ui_submit_attendance.impl",),
            acceptance_criteria=("UI submit_attendance and API attendance.create work",),
            facet_refs=("UI", "API", "INTEGRATION"),
            operation_refs=("submit_attendance", "attendance.create"),
            mapping_refs=("map-ui-submit",),
            obligation_refs=("obl_ui_submit_attendance", "obl_api_attendance_create"),
            binding_role="IMPLEMENT",
            **common,
        ),
        StepContractV1(
            step_index=3,
            purpose="IMPLEMENT persistence for students and attendance",
            target_scope=("proposed/obligation_seed/db_persistence.impl",),
            acceptance_criteria=("DB entities students and attendance are persisted",),
            facet_refs=("DB", "INTEGRATION"),
            entity_refs=("students", "attendance"),
            mapping_refs=("map-api-students", "map-api-att-list", "map-api-att-create"),
            obligation_refs=("obl_db_students", "obl_db_attendance"),
            binding_role="IMPLEMENT",
            **common,
        ),
    )


def test_outcome_contract_seals_hash() -> None:
    contract = _attendance_contract()
    assert contract.contract_hash
    assert contract.contract_hash == compute_outcome_contract_hash(contract)
    ok, code, _ = validate_outcome_contract_structure(contract)
    assert ok and code == "CONTRACT_VALID"


def test_contract_required_before_coverage() -> None:
    result = evaluate_contract_coverage(
        outcome_contract=None,
        steps=(),
        require_outcome_contract=True,
    )
    assert not result.ok
    assert result.code == "OUTCOME_CONTRACT_REQUIRED"


def test_unbound_operations_block_plan_coverage() -> None:
    contract = _attendance_contract()
    budget = _budget()
    unbound = StepContractV1(
        cycle_id="cyc_test",
        tenant_id="tenant_test",
        step_index=0,
        purpose="Create some frontend files",
        target_scope=("proposed/app/index.html",),
        acceptance_criteria=("files exist",),
        validation_commands=("echo ok",),
        expected_output_schema_ref="text",
        assigned_role="executor",
        step_budget=budget,
        status="PENDING",
        outcome_contract_id=contract.contract_id,
        outcome_contract_hash=contract.contract_hash,
        facet_refs=("UI",),
        # missing operation_refs
    )
    result = evaluate_contract_coverage(
        outcome_contract=contract,
        steps=(unbound,),
        require_outcome_contract=True,
    )
    assert not result.ok
    assert result.code in {"CONTRACT_OP_UNBOUND", "CONTRACT_FACET_MISSING"}


def test_bound_steps_pass_coverage() -> None:
    contract = _attendance_contract()
    steps = _bound_steps(contract)
    result = evaluate_contract_coverage(
        outcome_contract=contract,
        steps=steps,
        require_outcome_contract=True,
    )
    assert result.ok, result.detail
    assert result.code == "CONTRACT_COVERAGE_OK"


def test_integration_proof_static_pass() -> None:
    contract = _attendance_contract()
    steps = _bound_steps(contract)
    proof = evaluate_integration_proof(
        outcome_contract=contract,
        steps=steps,
        evidence_refs=("file://evidence/step1",),
        require_outcome_contract=True,
    )
    assert proof.ok
    assert proof.result == "INTEGRATION_PROVEN_STATIC"
    assert all(m.status == "OK" for m in proof.ui_api_mapping)
    assert all(m.status == "OK" for m in proof.api_schema_mapping)


def test_integration_proof_fails_without_ui_api_mapping() -> None:
    contract = seal_outcome_contract(
        OutcomeContractV1(
        objective_id="obj_bad",
        definition_of_done=("broken",),
        facets=OutcomeFacetsV1(
            UI=UiFacetV1(required_operations=("register_student",)),
            API=ApiFacetV1(
                required_operations=(
                    ApiOperationSpecV1(id="students.create", path_or_action="POST /students"),
                ),
            ),
            DB=DbFacetV1(entities=("students",)),
            INTEGRATION=IntegrationFacetV1(
                mappings=(
                    IntegrationMappingV1(
                        id="only-api-db",
                        from_facet="API",
                        from_id="students.create",
                        to_facet="DB",
                        to_id="students",
                    ),
                ),
            ),
        ),
        )
    )
    budget = _budget()
    steps = (
        StepContractV1(
            cycle_id="c",
            tenant_id="t",
            step_index=0,
            purpose="ui",
            target_scope=("a.html",),
            acceptance_criteria=("ui op",),
            validation_commands=("echo",),
            expected_output_schema_ref="text",
            assigned_role="executor",
            step_budget=budget,
            status="PENDING",
            outcome_contract_id=contract.contract_id,
            outcome_contract_hash=contract.contract_hash,
            facet_refs=("UI", "API", "DB"),
            operation_refs=("register_student", "students.create"),
            entity_refs=("students",),
            mapping_refs=("only-api-db",),
        ),
    )
    proof = evaluate_integration_proof(
        outcome_contract=contract,
        steps=steps,
        require_outcome_contract=True,
    )
    assert not proof.ok
    assert proof.result == "INTEGRATION_FAILED"
    assert any(m.code == "UI_OP_NO_API" for m in proof.mismatches)


def test_objective_proven_requires_five_conjuncts() -> None:
    blocked = evaluate_objective_proven(
        contract_covered=True,
        all_required_steps_passed=True,
        integration_proven=False,
        objective_acceptance_proven=True,
        evidence_complete=True,
    )
    assert not blocked.ok
    assert blocked.result == "BLOCKED"
    assert "INTEGRATION_NOT_PROVEN" in blocked.blocking_reasons

    # Last-step style flags alone are not enough — missing contract/integration/evidence
    last_step_only = evaluate_objective_proven(
        contract_covered=False,
        all_required_steps_passed=True,
        integration_proven=False,
        objective_acceptance_proven=True,
        evidence_complete=False,
    )
    assert not last_step_only.ok
    assert "CONTRACT_NOT_COVERED" in last_step_only.blocking_reasons

    ok = evaluate_objective_proven(
        contract_covered=True,
        all_required_steps_passed=True,
        integration_proven=True,
        objective_acceptance_proven=True,
        evidence_complete=True,
    )
    assert ok.ok
    assert ok.result == "COMPLETED_FOR_REVIEW"


def test_steps_passed_alone_not_objective_proven() -> None:
    """Critical invariant: all_required_steps_passed ≠ objective_proven."""
    eval_result = evaluate_objective_proven(
        contract_covered=False,
        all_required_steps_passed=True,
        integration_proven=False,
        objective_acceptance_proven=False,
        evidence_complete=False,
    )
    assert eval_result.ok is False
    assert eval_result.all_required_steps_passed is True
    assert "CONTRACT_NOT_COVERED" in eval_result.blocking_reasons
    assert "INTEGRATION_NOT_PROVEN" in eval_result.blocking_reasons
