"""AIX-PD-003D — Flow coherence rules (D1–D3) + hostile regressions."""

from __future__ import annotations

import copy

from backend.aix_core.runtime.objective.contracts import BudgetV1
from backend.aix_core.runtime.objective.flow_coherence import (
    validate_d1_full_graph_binding_matrix,
    validate_d2_multi_step_state_propagation,
    validate_d3_shared_surface_guard,
    validate_product_structure_flow_coherence,
)
from backend.aix_core.runtime.objective.outcome_contract_proposer import propose_outcome_contract_heuristic
from backend.aix_core.runtime.objective.product_structure import compile_product_structure
from backend.aix_core.runtime.objective.product_structure_plan import compile_product_structure_plan
from backend.aix_core.runtime.objective.product_structure_step_binding import (
    compile_product_structure_step_bindings,
)

_INTENT = (
    "Create greenfield under proposed/erp-system/. Governance create_root = proposed/. "
    "CREATE index.html, styles.css, app.js, api.php, config.php, database.sql, and README.md. "
    "PHP backend and MySQL database. Include authentication, customers, products, orders, inventory."
)


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=12, concurrency=1)


def _erp_stack():
    proposal = propose_outcome_contract_heuristic(
        objective_id="obj_pd003d",
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


def test_pd003d_erp_fixture_passes_full_coherence() -> None:
    contract, structure, plan, binding = _erp_stack()
    result = validate_product_structure_flow_coherence(
        contract=contract,
        structure=structure,
        bindings=binding.bindings,
        step_scopes=plan.step_scopes,
        depends_on=plan.depends_on,
    )
    assert result.ok, f"{result.code}: {result.detail} {result.violations}"


def test_pd003d_d1_rejects_unmapped_ui_operation() -> None:
    contract, structure, plan, binding = _erp_stack()
    broken = copy.deepcopy(contract)
    mappings = list(broken.facets.INTEGRATION.mappings)
    if mappings:
        mappings.pop(0)
    broken = broken.model_copy(
        update={
            "facets": broken.facets.model_copy(
                update={
                    "INTEGRATION": broken.facets.INTEGRATION.model_copy(update={"mappings": tuple(mappings)})
                }
            )
        }
    )
    result = validate_d1_full_graph_binding_matrix(contract=broken, structure=structure)
    assert not result.ok
    assert result.code == "FLOW_COHERENCE_D1_BINDING_MATRIX"


def test_pd003d_d3_rejects_duplicate_api_surface() -> None:
    contract, structure, plan, binding = _erp_stack()
    scopes = list(plan.step_scopes)
    if scopes:
        first = list(scopes[0])
        api_path = next((p for p in first if p.endswith("api.php")), "proposed/erp-system/api.php")
        scopes[1] = tuple(list(scopes[1]) + [api_path])
    result = validate_d3_shared_surface_guard(
        structure=structure,
        bindings=binding.bindings,
        step_scopes=tuple(scopes),
    )
    assert not result.ok
    assert result.code == "FLOW_COHERENCE_D3_SHARED_SURFACE"


def test_pd003d_d2_propagation_checks_upstream_api_for_ui() -> None:
    contract, structure, plan, binding = _erp_stack()
    result = validate_d2_multi_step_state_propagation(
        contract=contract,
        bindings=binding.bindings,
        depends_on=plan.depends_on,
    )
    assert result.ok, result.violations
