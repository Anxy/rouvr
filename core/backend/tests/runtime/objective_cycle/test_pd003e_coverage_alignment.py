"""AIX-PD-003E — Coverage alignment (E1–E3) + hostile regressions."""

from __future__ import annotations

import copy

from backend.aix_core.runtime.objective.flow_coherence import validate_product_structure_flow_coherence
from backend.aix_core.runtime.objective.outcome_contract_proposer import propose_outcome_contract_heuristic
from backend.aix_core.runtime.objective.product_structure import compile_product_structure
from backend.aix_core.runtime.objective.product_structure_plan import compile_product_structure_plan
from backend.aix_core.runtime.objective.product_structure_step_binding import (
    StepSemanticBindingV1,
    compile_product_structure_step_bindings,
)
from backend.aix_core.runtime.verification.coverage_alignment import (
    validate_e1_obligation_coverage,
    validate_e2_facet_parity,
    validate_e3_plan_union_coverage,
    validate_product_structure_coverage_alignment,
)

_INTENT = (
    "Create greenfield under proposed/erp-system/. Governance create_root = proposed/. "
    "CREATE index.html, styles.css, app.js, api.php, config.php, database.sql, and README.md. "
    "PHP backend and MySQL database. Include authentication, customers, products, orders, inventory."
)


def _erp_stack():
    proposal = propose_outcome_contract_heuristic(
        objective_id="obj_pd003e",
        objective=_INTENT,
        definition_of_done=(_INTENT,),
    )
    assert proposal.ok and proposal.contract is not None
    contract = proposal.contract
    compiled = compile_product_structure(contract)
    assert compiled.ok and compiled.structure is not None
    structure = compiled.structure
    plan = compile_product_structure_plan(
        contract=contract,
        structure=structure,
        intent_text=_INTENT,
        create_roots=("proposed/",),
        create_capability=("CREATE_FILE", "CREATE_DIRECTORY"),
        outcome_claim_ids=("outcome_1",),
    )
    assert plan.ok, plan.detail
    binding = compile_product_structure_step_bindings(
        contract=contract,
        structure=structure,
        step_scopes=plan.step_scopes,
        traces=plan.traces,
    )
    assert binding.ok, binding.detail
    return contract, structure, plan, binding


def test_pd003e_erp_fixture_passes_full_alignment() -> None:
    contract, structure, plan, binding = _erp_stack()
    flow = validate_product_structure_flow_coherence(
        contract=contract,
        structure=structure,
        bindings=binding.bindings,
        step_scopes=plan.step_scopes,
        depends_on=plan.depends_on,
    )
    assert flow.ok, flow.violations
    result = validate_product_structure_coverage_alignment(
        contract=contract,
        structure=structure,
        bindings=binding.bindings,
    )
    assert result.ok, f"{result.code}: {result.detail} {result.violations}"


def test_pd003e_e1_rejects_orphan_obligations() -> None:
    contract, structure, plan, binding = _erp_stack()
    stripped = tuple(
        StepSemanticBindingV1(
            binding_id=b.binding_id,
            step_index=b.step_index,
            role=b.role,
            facet_refs=b.facet_refs,
            operation_refs=b.operation_refs,
            entity_refs=b.entity_refs,
            mapping_refs=b.mapping_refs,
            obligation_refs=(),
            structure_refs=b.structure_refs,
        )
        for b in binding.bindings
    )
    result = validate_e1_obligation_coverage(
        contract=contract,
        structure=structure,
        bindings=stripped,
    )
    assert not result.ok
    assert result.code == "COVERAGE_ALIGNMENT_E1_OBLIGATION"
    assert any(v.startswith("E1:orphan_obligation:") for v in result.violations)


def test_pd003e_e2_rejects_api_without_db_backing() -> None:
    contract, structure, plan, binding = _erp_stack()
    broken = copy.deepcopy(contract)
    mappings = [
        m
        for m in broken.facets.INTEGRATION.mappings
        if not (m.from_facet == "API" and m.to_facet == "DB")
    ]
    broken = broken.model_copy(
        update={
            "facets": broken.facets.model_copy(
                update={
                    "INTEGRATION": broken.facets.INTEGRATION.model_copy(update={"mappings": tuple(mappings)})
                }
            )
        }
    )
    result = validate_e2_facet_parity(contract=broken, structure=structure)
    assert not result.ok
    assert result.code == "COVERAGE_ALIGNMENT_E2_FACET_PARITY"
    assert any("E2:api_missing_db_model:" in v for v in result.violations)


def test_pd003e_e3_rejects_incomplete_structure_union() -> None:
    contract, structure, plan, binding = _erp_stack()
    if not binding.bindings:
        return
    api_binding = next(b for b in binding.bindings if b.role == "API_BACKEND")
    trimmed_ops = api_binding.operation_refs[:1] if len(api_binding.operation_refs) > 1 else ()
    patched = []
    for b in binding.bindings:
        if b.step_index == api_binding.step_index:
            patched.append(
                StepSemanticBindingV1(
                    binding_id=b.binding_id,
                    step_index=b.step_index,
                    role=b.role,
                    facet_refs=b.facet_refs,
                    operation_refs=trimmed_ops,
                    entity_refs=b.entity_refs,
                    mapping_refs=b.mapping_refs,
                    obligation_refs=b.obligation_refs,
                    structure_refs=b.structure_refs,
                )
            )
        else:
            patched.append(b)
    result = validate_e3_plan_union_coverage(structure=structure, bindings=tuple(patched))
    assert not result.ok
    assert result.code == "COVERAGE_ALIGNMENT_E3_PLAN_UNION"
    assert any(v.startswith("E3:structure_api_uncovered:") for v in result.violations)
