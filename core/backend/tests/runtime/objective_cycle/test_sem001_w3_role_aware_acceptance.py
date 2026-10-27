"""AIX-SEM-001-W3 — Role-aware acceptance criteria (ProductStructure planner).

W3-T1: ERP fixture API step AC ≠ only ``api.php exists``
W3-T2: Directory-only / empty-ref step → existence AC still OK
W3-T3: plan.md / StepContract persistence shows new AC on accepted plan
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    PlanRevisionV1,
    RequiredEffectV1,
    RiskTier,
    StepContractV1,
)
from backend.aix_core.runtime.objective.objective_scope import render_plan_markdown
from backend.aix_core.runtime.objective.outcome_contract_proposer import (
    propose_outcome_contract_heuristic,
)
from backend.aix_core.runtime.objective.product_structure import compile_product_structure
from backend.aix_core.runtime.objective.product_structure_plan import compile_product_structure_plan
from backend.aix_core.runtime.objective.product_structure_step_binding import (
    apply_semantic_bindings_to_steps,
    compile_product_structure_step_bindings,
)
from backend.aix_core.runtime.planner.step_acceptance_continuity import (
    CREATE_ROOT_ACCEPTANCE,
    build_role_aware_acceptance_criteria,
)

_INTENT = (
    "Create greenfield under proposed/erp-system/. Governance create_root = proposed/. "
    "CREATE index.html, styles.css, app.js, api.php, config.php, database.sql, and README.md. "
    "PHP backend and MySQL database. Include authentication, RBAC, dashboard/KPIs, customers, "
    "suppliers, products, categories, warehouses, inventory, invoices, payments, orders, "
    "notifications, reports."
)


def _budget() -> BudgetV1:
    return BudgetV1(
        time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=12, concurrency=1
    )


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
        objective_id="obj_sem001_w3_erp",
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


def _planner_style_steps(contract, structure, plan) -> list[StepContractV1]:
    """Mirror SEM-001-W3 wiring in step_contract_planner (bind → role-aware AC)."""
    budget = _budget()
    bare: list[StepContractV1] = []
    for i, scope in enumerate(plan.step_scopes):
        bare.append(
            StepContractV1(
                cycle_id="cyc-sem001-w3",
                tenant_id="tenant-1",
                step_index=i,
                purpose="Create governed artifacts under create_root",
                target_scope=scope,
                expected_output_schema_ref="aix.step_contract.v1.generic_edit",
                acceptance_criteria=tuple(f"{p} exists" for p in scope)
                + (CREATE_ROOT_ACCEPTANCE,),
                validation_commands=tuple(
                    f"test -d {p}" if str(p).endswith("/") else f"test -f {p}" for p in scope
                ),
                assigned_role="executor",
                step_budget=budget,
                required_effects=(RequiredEffectV1(kind="LITERAL_REPLACE"),),
                risk_tier=RiskTier.MEDIUM,
                status="PENDING",
            )
        )
    binding = compile_product_structure_step_bindings(
        contract=contract,
        structure=structure,
        step_scopes=plan.step_scopes,
        traces=plan.traces,
    )
    assert binding.ok, binding.detail
    stamped = list(
        apply_semantic_bindings_to_steps(steps=tuple(bare), bindings=binding.bindings)
    )
    enriched: list[StepContractV1] = []
    for index, step in enumerate(stamped):
        b = binding.bindings[index] if index < len(binding.bindings) else None
        role = b.role if b is not None else "SUPPORTING"
        ac = build_role_aware_acceptance_criteria(
            scope=step.target_scope,
            role=role,
            operation_refs=step.operation_refs,
            entity_refs=step.entity_refs,
            mapping_refs=step.mapping_refs,
        )
        enriched.append(step.model_copy(update={"acceptance_criteria": ac}))
    return enriched, binding


def test_w3_t1_erp_api_ac_not_existence_only() -> None:
    """W3-T1: ProductStructure ERP fixture — API step AC ≠ only api.php exists."""
    contract, structure, plan, _ = _erp_fixture()
    steps, binding = _planner_style_steps(contract, structure, plan)
    api_binding = next(b for b in binding.bindings if b.role == "API_BACKEND")
    api_step = steps[api_binding.step_index]

    existence_only = {
        c for c in api_step.acceptance_criteria if c.endswith(" exists")
    }
    assert any("api.php exists" in c for c in api_step.acceptance_criteria)
    # Must not be existence-only
    non_existence = [
        c
        for c in api_step.acceptance_criteria
        if not c.endswith(" exists") and c != CREATE_ROOT_ACCEPTANCE
    ]
    assert non_existence, f"API AC was existence-only: {api_step.acceptance_criteria}"
    impl = next(c for c in non_existence if c.startswith("implements operations:"))
    assert api_binding.operation_refs
    for op in api_binding.operation_refs[:5]:
        assert op in impl
    # Guard: still retain path exists + create_root
    assert CREATE_ROOT_ACCEPTANCE in api_step.acceptance_criteria
    assert existence_only


def test_w3_t1_db_and_ui_role_lines() -> None:
    """Role-aware DB/UI lines are backed by refs (no invented tokens)."""
    contract, structure, plan, _ = _erp_fixture()
    steps, binding = _planner_style_steps(contract, structure, plan)

    db_b = next(b for b in binding.bindings if b.role == "DB_SCHEMA")
    db_step = steps[db_b.step_index]
    db_line = next(
        c for c in db_step.acceptance_criteria if c.startswith("defines entities:")
    )
    for ent in db_b.entity_refs[:3]:
        assert ent in db_line

    ui_b = next(b for b in binding.bindings if b.role == "UI_PAGE")
    ui_step = steps[ui_b.step_index]
    ui_line = next(
        c
        for c in ui_step.acceptance_criteria
        if c.startswith("wires UI operations / calls API ops:")
    )
    for op in ui_b.operation_refs[:3]:
        assert op in ui_line


def test_w3_t2_directory_empty_refs_existence_ok() -> None:
    """W3-T2: Directory-only step with empty refs → existence AC still OK."""
    ac = build_role_aware_acceptance_criteria(
        scope=("proposed/erp-system/",),
        role="DIRECTORY",
        operation_refs=(),
        entity_refs=(),
        mapping_refs=(),
    )
    assert ac == (
        "proposed/erp-system/ exists",
        CREATE_ROOT_ACCEPTANCE,
    )
    # README / supporting empty-ref
    readme = build_role_aware_acceptance_criteria(
        scope=("proposed/erp-system/README.md",),
        role="SUPPORTING",
    )
    assert readme[0] == "proposed/erp-system/README.md exists"
    assert all(
        (c.endswith(" exists") or c == CREATE_ROOT_ACCEPTANCE) for c in readme
    )
    assert not any(
        c.startswith("implements operations:")
        or c.startswith("defines entities:")
        or c.startswith("wires UI operations")
        for c in readme
    )


def test_w3_t2_erp_directory_step_from_fixture() -> None:
    """W3-T2 via ERP fixture DIRECTORY binding remains existence-scoped."""
    contract, structure, plan, _ = _erp_fixture()
    steps, binding = _planner_style_steps(contract, structure, plan)
    dir_bindings = [b for b in binding.bindings if b.role == "DIRECTORY"]
    assert dir_bindings
    for b in dir_bindings:
        step = steps[b.step_index]
        assert not b.operation_refs and not b.entity_refs and not b.mapping_refs
        assert all(
            c.endswith(" exists") or c == CREATE_ROOT_ACCEPTANCE
            for c in step.acceptance_criteria
        )


def test_w3_t3_plan_md_persistence_shows_role_aware_ac() -> None:
    """W3-T3: Accepted plan.md rendering includes role-aware AC from StepContracts."""
    contract, structure, plan_compile, objective = _erp_fixture()
    steps, binding = _planner_style_steps(contract, structure, plan_compile)
    api_b = next(b for b in binding.bindings if b.role == "API_BACKEND")
    api_step = steps[api_b.step_index]

    plan = PlanRevisionV1(
        objective_id="obj_sem001_w3",
        cycle_id="cyc-sem001-w3",
        revision=1,
        created_from_evidence="ev_w3",
        steps=tuple(steps),
        obligation_coverage=(),
        planner_reasoning_ref="sem001-w3-test",
        status="ACCEPTED",
    )
    plan_md = render_plan_markdown(
        original_objective=objective,
        understanding_summary="SEM-001-W3 role-aware AC persistence check",
        obligations=(),
        constraints=("create_root=proposed/",),
        plan=plan,
        execution_lane="real_execute",
    )
    assert "Acceptance Criteria:" in plan_md
    assert "implements operations:" in plan_md
    assert "defines entities:" in plan_md
    assert "wires UI operations / calls API ops:" in plan_md
    # At least one concrete API op token from the contract survives into plan.md
    sample_op = api_b.operation_refs[0]
    assert sample_op in plan_md
    assert any("api.php exists" in c for c in api_step.acceptance_criteria)


def test_w3_no_hallucinated_ops() -> None:
    """Stop rule: AC must not invent ops not present in refs."""
    ac = build_role_aware_acceptance_criteria(
        scope=("proposed/erp-system/api.php",),
        role="API_BACKEND",
        operation_refs=("products.list",),
        entity_refs=(),
        mapping_refs=(),
    )
    joined = " | ".join(ac)
    assert "products.list" in joined
    assert "auth.login" not in joined
    assert "implements operations: products.list" in ac
