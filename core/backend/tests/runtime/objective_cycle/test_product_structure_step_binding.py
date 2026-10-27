"""AIX-PD-003B2: ProductStructure → StepContract semantic bindings (T1–T3)."""

from __future__ import annotations

import json
from pathlib import Path

from backend.aix_core.runtime.objective.contracts import BudgetV1, RequiredEffectV1, RiskTier, StepContractV1
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
from backend.aix_core.runtime.objective.outcome_contract_proposer import (
    bind_steps_to_contract,
    propose_outcome_contract_heuristic,
)
from backend.aix_core.runtime.objective.product_structure import compile_product_structure
from backend.aix_core.runtime.objective.product_structure_plan import compile_product_structure_plan
from backend.aix_core.runtime.objective.product_structure_step_binding import (
    apply_semantic_bindings_to_steps,
    compile_product_structure_step_bindings,
)
from backend.aix_core.runtime.verification.contract_coverage import evaluate_contract_coverage

_INTENT = (
    "Create greenfield under proposed/erp-system/. Governance create_root = proposed/. "
    "CREATE index.html, styles.css, app.js, api.php, config.php, database.sql, and README.md. "
    "PHP backend and MySQL database. Include authentication, RBAC, dashboard/KPIs, customers, "
    "suppliers, products, categories, warehouses, inventory, invoices, payments, orders, "
    "notifications, reports."
)


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=12, concurrency=1)


def _erp_fixture():
    obj_path = (
        Path(__file__).resolve().parents[4]
        / ".tmp"
        / "aix-cycle-workspaces"
        / "5ED5Z0CF2MCPXHRAJWW7SBWZSE"
        / "2026-08-27"
        / "disc_01M10YNJ2DKV3W64"
        / "objective.json"
    )
    if obj_path.is_file():
        objective = json.loads(obj_path.read_text(encoding="utf-8"))["normalized_objective"]
    else:
        objective = _INTENT
    proposal = propose_outcome_contract_heuristic(
        objective_id="obj_pd003b2_erp",
        objective=objective,
        definition_of_done=(objective,),
    )
    assert proposal.ok and proposal.contract is not None
    contract = proposal.contract
    structure = compile_product_structure(contract).structure
    assert structure is not None
    plan = compile_product_structure_plan(
        contract=contract,
        structure=structure,
        intent_text=objective,
        create_roots=("proposed/",),
        create_capability=("CREATE_FILE", "CREATE_DIRECTORY"),
        outcome_claim_ids=("outcome_1",),
    )
    assert plan.ok, plan.detail
    return contract, structure, plan, objective


def _bare_steps(plan) -> list[StepContractV1]:
    budget = _budget()
    steps: list[StepContractV1] = []
    for i, scope in enumerate(plan.step_scopes):
        steps.append(
            StepContractV1(
                cycle_id="cyc",
                tenant_id="t",
                step_index=i,
                purpose="Create governed artifacts under create_root",
                target_scope=scope,
                expected_output_schema_ref="aix.step_contract.v1.generic_edit",
                acceptance_criteria=tuple(f"{p} exists" for p in scope) + ("ok",),
                validation_commands=tuple(f"test -f {p}" for p in scope),
                assigned_role="executor",
                step_budget=budget,
                required_effects=(RequiredEffectV1(kind="LITERAL_REPLACE"),),
                risk_tier=RiskTier.MEDIUM,
                status="PENDING",
            )
        )
    return steps


def test_t1_erp_fixture_coverage_gate_passes_with_binder() -> None:
    contract, structure, plan, _ = _erp_fixture()
    assert len(contract.facets.UI.required_operations) >= 10
    assert len(contract.facets.API.required_operations) >= 10
    assert len(contract.facets.DB.entities) >= 5

    # Before binder: CoverageGate fails (API/DB unbound)
    bare = _bare_steps(plan)
    before = evaluate_contract_coverage(
        outcome_contract=contract,
        steps=tuple(bare),
        require_outcome_contract=True,
    )
    assert before.ok is False
    assert before.code == "CONTRACT_FACET_MISSING"
    assert "API" in before.uncovered_facets and "DB" in before.uncovered_facets

    binding = compile_product_structure_step_bindings(
        contract=contract,
        structure=structure,
        step_scopes=plan.step_scopes,
        traces=plan.traces,
    )
    assert binding.ok is True, binding.detail
    assert binding.code == "PRODUCT_STRUCTURE_STEPS_BOUND"

    stamped = apply_semantic_bindings_to_steps(steps=tuple(bare), bindings=binding.bindings)
    # DoD-002E narrow preserve declared refs
    stamped = bind_steps_to_contract(steps=stamped, contract=contract)

    after = evaluate_contract_coverage(
        outcome_contract=contract,
        steps=stamped,
        require_outcome_contract=True,
    )
    assert after.ok is True, (after.code, after.detail, after.unbound_operations[:5])

    # Role narrowness: UI_PAGE carries UI only; API/DB stay on their steps
    ui_page = next(b for b in binding.bindings if b.role == "UI_PAGE")
    api_step = next(b for b in binding.bindings if b.role == "API_BACKEND")
    db_step = next(b for b in binding.bindings if b.role == "DB_SCHEMA")
    assert ui_page.facet_refs == ("UI",)
    assert "API" not in ui_page.facet_refs and "DB" not in ui_page.facet_refs
    assert api_step.facet_refs == ("API",)
    assert db_step.facet_refs == ("DB",)
    assert set(api_step.operation_refs) == {op.id for op in contract.facets.API.required_operations}
    assert set(db_step.entity_refs) == set(contract.facets.DB.entities)
    assert set(ui_page.operation_refs) == set(contract.facets.UI.required_operations)


def test_t2_bindings_trace_to_structure_not_filenames() -> None:
    module = (
        Path(__file__).resolve().parents[3]
        / "aix_core"
        / "runtime"
        / "objective"
        / "product_structure_step_binding.py"
    )
    source = module.read_text(encoding="utf-8")
    # Must not implement suffix dumps
    assert "endswith(\".php\")" not in source
    assert "endswith(\".sql\")" not in source
    assert "api.php" not in source
    assert "database.sql" not in source

    contract, structure, plan, _ = _erp_fixture()
    binding = compile_product_structure_step_bindings(
        contract=contract,
        structure=structure,
        step_scopes=plan.step_scopes,
        traces=plan.traces,
    )
    assert binding.ok
    api_step = next(b for b in binding.bindings if b.role == "API_BACKEND")
    assert api_step.structure_refs
    assert all(r.startswith("api__") for r in api_step.structure_refs)
    ui_step = next(b for b in binding.bindings if b.role == "UI_PAGE")
    assert ui_step.structure_refs
    assert all(r.startswith("view__") for r in ui_step.structure_refs)
    # Directory must not carry accidental create_product-style coverage
    directory = next(b for b in binding.bindings if b.role == "DIRECTORY")
    assert directory.operation_refs == ()
    assert directory.facet_refs == ()


def test_t3_unmapped_view_path_fails_closed() -> None:
    contract, structure, plan, _ = _erp_fixture()
    # Inject a fake UI_PAGE path with no matching view
    scopes = list(plan.step_scopes)
    traces = list(plan.traces)
    # Find UI_PAGE step index
    binding_ok = compile_product_structure_step_bindings(
        contract=contract,
        structure=structure,
        step_scopes=plan.step_scopes,
        traces=plan.traces,
    )
    assert binding_ok.ok
    page_idx = next(b.step_index for b in binding_ok.bindings if b.role == "UI_PAGE")
    bad_path = "proposed/erp-system/not_a_real_view_zzz.html"
    scopes[page_idx] = scopes[page_idx] + (bad_path,)
    from backend.aix_core.runtime.objective.product_structure_plan import TargetTrace

    traces.append(
        TargetTrace(
            path=bad_path,
            role="UI_PAGE",
            structure_ref="missing",
            contract_facet="UI",
        )
    )
    failed = compile_product_structure_step_bindings(
        contract=contract,
        structure=structure,
        step_scopes=tuple(scopes),
        traces=tuple(traces),
    )
    assert failed.ok is False
    assert failed.code == "VIEW_PATH_UNMAPPED"


def test_t3_missing_traces_fails_closed() -> None:
    contract, structure, plan, _ = _erp_fixture()
    failed = compile_product_structure_step_bindings(
        contract=contract,
        structure=structure,
        step_scopes=plan.step_scopes,
        traces=(),
    )
    assert failed.ok is False
    assert failed.code == "TRACES_REQUIRED"


def test_binding_ids_deterministic() -> None:
    contract, structure, plan, _ = _erp_fixture()
    a = compile_product_structure_step_bindings(
        contract=contract,
        structure=structure,
        step_scopes=plan.step_scopes,
        traces=plan.traces,
    )
    b = compile_product_structure_step_bindings(
        contract=contract,
        structure=structure,
        step_scopes=plan.step_scopes,
        traces=plan.traces,
    )
    assert a.ok and b.ok
    assert [x.binding_id for x in a.bindings] == [x.binding_id for x in b.bindings]
    assert [x.operation_refs for x in a.bindings] == [x.operation_refs for x in b.bindings]


def test_small_contract_page_step_stays_narrow() -> None:
    mappings = (
        IntegrationMappingV1(
            id="m1", from_facet="UI", from_id="list_inventory", to_facet="API", to_id="inventory.list"
        ),
        IntegrationMappingV1(
            id="m2", from_facet="UI", from_id="list_customers", to_facet="API", to_id="customers.list"
        ),
        IntegrationMappingV1(
            id="m3", from_facet="API", from_id="inventory.list", to_facet="DB", to_id="inventory"
        ),
        IntegrationMappingV1(
            id="m4", from_facet="API", from_id="customers.list", to_facet="DB", to_id="customers"
        ),
    )
    contract = seal_outcome_contract(
        OutcomeContractV1(
            objective_id="obj_narrow",
            definition_of_done=("inventory and customers",),
            facets=OutcomeFacetsV1(
                UI=UiFacetV1(
                    required_operations=("list_inventory", "list_customers"),
                    required_views=("inventory", "customers"),
                    acceptance_criteria=("ui",),
                ),
                API=ApiFacetV1(
                    required_operations=(
                        ApiOperationSpecV1(id="inventory.list", path_or_action="GET /inventory"),
                        ApiOperationSpecV1(id="customers.list", path_or_action="GET /customers"),
                    ),
                    acceptance_criteria=("api",),
                ),
                DB=DbFacetV1(entities=("inventory", "customers"), acceptance_criteria=("db",)),
                INTEGRATION=IntegrationFacetV1(mappings=mappings, acceptance_criteria=("map",)),
            ),
        )
    )
    structure = compile_product_structure(contract).structure
    assert structure is not None
    intent = (
        "Create under proposed/demo-app/. CREATE index.html, styles.css, app.js, "
        "api.php, database.sql, README.md. PHP MySQL."
    )
    plan = compile_product_structure_plan(
        contract=contract,
        structure=structure,
        intent_text=intent,
        create_roots=("proposed/",),
        create_capability=("CREATE_FILE", "CREATE_DIRECTORY"),
        outcome_claim_ids=("o1",),
    )
    assert plan.ok, plan.detail
    binding = compile_product_structure_step_bindings(
        contract=contract,
        structure=structure,
        step_scopes=plan.step_scopes,
        traces=plan.traces,
    )
    assert binding.ok, binding.detail
    ui = next(b for b in binding.bindings if b.role == "UI_PAGE")
    # Both views on one UI_PAGE step group → both UI ops on that step (still < full dump via api)
    assert set(ui.operation_refs) == {"list_inventory", "list_customers"}
    assert "API" not in ui.facet_refs
    stamped = apply_semantic_bindings_to_steps(steps=tuple(_bare_steps(plan)), bindings=binding.bindings)
    cov = evaluate_contract_coverage(
        outcome_contract=contract, steps=stamped, require_outcome_contract=True
    )
    assert cov.ok is True, (cov.code, cov.detail)
