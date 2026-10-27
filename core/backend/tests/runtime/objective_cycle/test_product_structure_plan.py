"""AIX-PD-003B: ProductStructureV1 → governed PlanTargets compiler."""

from __future__ import annotations

from pathlib import Path

from backend.aix_core.runtime.objective.contracts import BudgetV1
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
from backend.aix_core.runtime.objective.product_structure import compile_product_structure
from backend.aix_core.runtime.objective.product_structure_plan import (
    compile_product_structure_plan,
    resolve_product_root,
)
from backend.aix_core.runtime.objective.provenance.admit import freeze_user_intent
from backend.aix_core.runtime.objective.provenance.contracts import (
    ClaimCite,
    UnderstandingClaim,
    UnderstandingRevision,
)
from backend.aix_core.runtime.objective.provenance.plan_request import (
    build_plan_request,
    construct_plan_revision,
    default_governance,
)


def _erp_like_sealed() -> OutcomeContractV1:
    """Views named like an ERP — data-driven, not hard-coded in the compiler."""
    mappings = (
        IntegrationMappingV1(
            id="map-d", from_facet="UI", from_id="open_dashboard", to_facet="API", to_id="kpi.list"
        ),
        IntegrationMappingV1(
            id="map-i", from_facet="UI", from_id="manage_inventory", to_facet="API", to_id="stock.list"
        ),
        IntegrationMappingV1(
            id="map-c", from_facet="UI", from_id="manage_customers", to_facet="API", to_id="customers.list"
        ),
        IntegrationMappingV1(
            id="map-d2", from_facet="API", from_id="kpi.list", to_facet="DB", to_id="kpi_metrics"
        ),
        IntegrationMappingV1(
            id="map-i2", from_facet="API", from_id="stock.list", to_facet="DB", to_id="inventory"
        ),
        IntegrationMappingV1(
            id="map-c2", from_facet="API", from_id="customers.list", to_facet="DB", to_id="customers"
        ),
    )
    return seal_outcome_contract(
        OutcomeContractV1(
            objective_id="obj_pd003b_erp_like",
            definition_of_done=("Greenfield product with dashboard, inventory, customers",),
            facets=OutcomeFacetsV1(
                UI=UiFacetV1(
                    required_operations=("open_dashboard", "manage_inventory", "manage_customers"),
                    required_views=("dashboard", "inventory", "customers"),
                    acceptance_criteria=("UI views declared",),
                ),
                API=ApiFacetV1(
                    required_operations=(
                        ApiOperationSpecV1(id="kpi.list", path_or_action="GET /kpi"),
                        ApiOperationSpecV1(id="stock.list", path_or_action="GET /stock"),
                        ApiOperationSpecV1(id="customers.list", path_or_action="GET /customers"),
                    ),
                    acceptance_criteria=("API declared",),
                ),
                DB=DbFacetV1(
                    entities=("kpi_metrics", "inventory", "customers"),
                    acceptance_criteria=("DB declared",),
                ),
                INTEGRATION=IntegrationFacetV1(
                    mappings=mappings,
                    acceptance_criteria=("mappings compose",),
                ),
            ),
        )
    )


_INTENT = (
    "Create greenfield under proposed/erp-system/. Governance create_root = proposed/. "
    "CREATE index.html, styles.css, app.js, api.php, config.php, database.sql, and README.md. "
    "PHP backend and MySQL database."
)


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=12, concurrency=1)


def test_resolve_product_root_from_intent() -> None:
    root, err, _ = resolve_product_root(intent_text=_INTENT, create_roots=("proposed/",))
    assert err is None
    assert root == "proposed/erp-system/"


def test_product_structure_plan_emits_view_pages_without_erp_hardcode() -> None:
    module = (
        Path(__file__).resolve().parents[3]
        / "aix_core"
        / "runtime"
        / "objective"
        / "product_structure_plan.py"
    )
    source = module.read_text(encoding="utf-8")
    for token in ("dashboard.html", "inventory.html", "customers.html", "erp-system", "ERP_PAGES"):
        assert f'"{token}"' not in source and f"'{token}'" not in source

    contract = _erp_like_sealed()
    structure = compile_product_structure(contract).structure
    assert structure is not None
    result = compile_product_structure_plan(
        contract=contract,
        structure=structure,
        intent_text=_INTENT,
        create_roots=("proposed/",),
        create_capability=("CREATE_FILE", "CREATE_DIRECTORY"),
        outcome_claim_ids=("outcome_1",),
    )
    assert result.ok is True, result.detail
    assert result.code == "PRODUCT_STRUCTURE_PLAN_COMPILED"
    paths = {t.path for t in result.targets}
    assert "proposed/erp-system/dashboard.html" in paths
    assert "proposed/erp-system/inventory.html" in paths
    assert "proposed/erp-system/customers.html" in paths
    assert "proposed/erp-system/api.php" in paths
    assert "proposed/erp-system/database.sql" in paths
    assert "proposed/erp-system/styles.css" in paths
    assert any(p.rstrip("/").endswith("erp-system") for p in paths)


def test_product_structure_plan_deterministic() -> None:
    contract = _erp_like_sealed()
    structure = compile_product_structure(contract).structure
    assert structure is not None
    kwargs = dict(
        contract=contract,
        structure=structure,
        intent_text=_INTENT,
        create_roots=("proposed/",),
        create_capability=("CREATE_FILE", "CREATE_DIRECTORY"),
        outcome_claim_ids=("outcome_1",),
    )
    a = compile_product_structure_plan(**kwargs)
    b = compile_product_structure_plan(**kwargs)
    assert a.ok and b.ok
    assert [t.id for t in a.targets] == [t.id for t in b.targets]
    assert [t.path for t in a.targets] == [t.path for t in b.targets]
    assert a.step_scopes == b.step_scopes
    assert a.depends_on == b.depends_on


def test_product_structure_plan_rejects_missing_root() -> None:
    contract = _erp_like_sealed()
    structure = compile_product_structure(contract).structure
    assert structure is not None
    result = compile_product_structure_plan(
        contract=contract,
        structure=structure,
        intent_text="Build something with no proposed path",
        create_roots=("proposed/",),
        create_capability=("CREATE_FILE", "CREATE_DIRECTORY"),
        outcome_claim_ids=("outcome_1",),
    )
    assert result.ok is False
    assert result.code == "PRODUCT_ROOT_MISSING"


def test_product_structure_plan_rejects_scope_widening() -> None:
    contract = _erp_like_sealed()
    structure = compile_product_structure(contract).structure
    assert structure is not None
    result = compile_product_structure_plan(
        contract=contract,
        structure=structure,
        intent_text=_INTENT,
        create_roots=("other-root/",),
        create_capability=("CREATE_FILE", "CREATE_DIRECTORY"),
        outcome_claim_ids=("outcome_1",),
    )
    assert result.ok is False
    assert result.code in {"PRODUCT_ROOT_MISSING", "SCOPE_WIDENING"}


def test_product_structure_plan_dependency_order() -> None:
    contract = _erp_like_sealed()
    structure = compile_product_structure(contract).structure
    assert structure is not None
    result = compile_product_structure_plan(
        contract=contract,
        structure=structure,
        intent_text=_INTENT,
        create_roots=("proposed/",),
        create_capability=("CREATE_FILE", "CREATE_DIRECTORY"),
        outcome_claim_ids=("outcome_1",),
    )
    assert result.ok
    flat = [p for scope in result.step_scopes for p in scope]
    dir_i = next(i for i, p in enumerate(flat) if p.endswith("/"))
    page_i = next(i for i, p in enumerate(flat) if p.endswith("dashboard.html"))
    assert dir_i < page_i
    for i, deps in enumerate(result.depends_on):
        assert i not in deps
        assert all(0 <= d < len(result.step_scopes) for d in deps)


def test_construct_plan_revision_accepts_pd003b_targets() -> None:
    contract = _erp_like_sealed()
    structure = compile_product_structure(contract).structure
    assert structure is not None
    intent = freeze_user_intent(objective_id="obj_pd003b_erp_like", text=_INTENT)
    outcome = UnderstandingClaim(
        id="cl_outcome",
        kind="OUTCOME",
        text="Greenfield product delivered under create_root",
        standing="ASSERTED",
        status="ACTIVE",
        cites=(ClaimCite(kind="INTENT_SPAN", ref=intent.id),),
    )
    understanding = UnderstandingRevision(
        objective_id=intent.objective_id,
        intent_id=intent.id,
        scope_mode="OPEN_IMPLEMENTATION",
        claims=(outcome,),
    )
    gov = default_governance(
        objective_id=intent.objective_id,
        task_class="code_change",
        budgets=_budget(),
        create_root=("proposed/",),
    )
    plan_result = compile_product_structure_plan(
        contract=contract,
        structure=structure,
        intent_text=_INTENT,
        create_roots=gov.create_root,
        create_capability=gov.create_capability,
        outcome_claim_ids=(outcome.id,),
    )
    assert plan_result.ok, plan_result.detail
    request = build_plan_request(
        cycle_id="cyc_pd003b",
        intent=intent,
        understanding=understanding,
        evidence=(),
        governance=gov,
    )
    construction = construct_plan_revision(
        request=request,
        intent=intent,
        understanding=understanding,
        needs=(),
        evidence=(),
        governance=gov,
        targets=plan_result.targets,
        step_scopes=plan_result.step_scopes,
    )
    assert construction.validation.ok, (
        construction.validation.rule_id,
        construction.validation.detail,
        construction.validation.decision,
    )
    assert construction.validation.decision == "ACCEPT"
    assert len(construction.plan.targets) == len(plan_result.targets)


def test_pd003a_regression_structure_still_compiles() -> None:
    contract = _erp_like_sealed()
    ps = compile_product_structure(contract)
    assert ps.ok and ps.structure is not None
    assert ps.structure.views


_EXPENSE_TRACKER = (
    "Build a complete Expense Tracker web app using FastAPI + Python backend, "
    "HTML/CSS/JavaScript frontend, and SQLite. Include registration/login, "
    "add/edit/delete expenses, categories, monthly budgets, dashboard totals, "
    "filters, validation, error handling, responsive UI, automated API tests, "
    "and README. The app must run with uvicorn and be verified end-to-end."
)


def test_infer_and_resolve_expense_tracker_without_proposed_path() -> None:
    from backend.aix_core.runtime.objective.product_class import infer_greenfield_product_slug

    assert infer_greenfield_product_slug(objective=_EXPENSE_TRACKER) == "expense-tracker"
    root, err, _ = resolve_product_root(intent_text=_EXPENSE_TRACKER, create_roots=("proposed/",))
    assert err is None
    assert root == "proposed/expense-tracker/"


def test_expense_tracker_product_structure_plan_compiles_without_named_files() -> None:
    from backend.aix_core.runtime.objective.outcome_contract_proposer import (
        propose_outcome_contract_heuristic,
    )

    proposal = propose_outcome_contract_heuristic(
        objective_id="obj_expense_tracker",
        objective=_EXPENSE_TRACKER,
        definition_of_done=("Expense Tracker runs with uvicorn",),
    )
    assert proposal.ok and proposal.contract is not None, proposal.detail
    structure = compile_product_structure(proposal.contract).structure
    assert structure is not None
    result = compile_product_structure_plan(
        contract=proposal.contract,
        structure=structure,
        intent_text=_EXPENSE_TRACKER,
        create_roots=("proposed/",),
        create_capability=("CREATE_FILE", "CREATE_DIRECTORY"),
        outcome_claim_ids=("outcome_1",),
    )
    assert result.ok is True, f"{result.code}:{result.detail}"
    paths = {t.path for t in result.targets}
    assert "proposed/expense-tracker/" in paths or any(
        p.rstrip("/").endswith("expense-tracker") for p in paths
    )
    assert "proposed/expense-tracker/api.py" in paths
    assert "proposed/expense-tracker/database.sql" in paths
    assert "proposed/expense-tracker/styles.css" in paths
    assert "proposed/expense-tracker/app.js" in paths
    assert "proposed/expense-tracker/README.md" in paths
    assert "proposed/expense-tracker/tests/test_api.py" in paths
    assert any(p.endswith(".html") for p in paths)


def test_greenfield_extract_synthesizes_stack_when_intent_omits_filenames() -> None:
    from backend.aix_core.runtime.planner.step_contract_planner import (
        _extract_greenfield_create_paths,
    )

    paths = _extract_greenfield_create_paths(
        intent_text=_EXPENSE_TRACKER,
        create_roots=("proposed/",),
    )
    assert "proposed/expense-tracker/api.py" in paths
    assert "proposed/expense-tracker/database.sql" in paths
    assert "proposed/expense-tracker/README.md" in paths
