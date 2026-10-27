"""AIX-SEM-001-W6 — Live ERP continuity certification (L1–L5), deterministic.

Proves Continuity Law against the attenuated baseline task
``01M11F0G07HMTBFTG5X7CQWPW0`` / ``cyc_01M11F0GRZG9`` without requiring live LLM:

  L1 ref-bearing derived prompts contain op/entity tokens
  L2 depends_on upstream paths appear in discovery scope
  L3 no STEP_PASSED with product_structure_coverage=false when refs nonempty
  L4 connected deliverable signals (UI→api.php; API multi-domain ops)
  L5 attenuation matrix vs prior cycle shows gaps A/B/C closed
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.executor.step_prompt_continuity import (
    build_continuity_prompt_section,
)
from backend.aix_core.runtime.executor.step_upstream_discovery import (
    merge_discovery_paths,
    resolve_upstream_target_paths,
)
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    PlanRevisionV1,
    RequiredEffectV1,
    RiskTier,
    StepContractV1,
)
from backend.aix_core.runtime.objective.discovery import DiscoveryReport
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
    build_role_aware_acceptance_criteria,
)
from backend.aix_core.runtime.verification.completion_gates import evaluate_completion_gates
from backend.aix_core.runtime.verification.product_structure_coverage import (
    evaluate_product_structure_coverage,
    step_has_continuity_refs,
)

_AIX_ROOT = Path(__file__).resolve().parents[5]
_BASELINE_CYCLE = (
    _AIX_ROOT
    / ".tmp"
    / "aix-cycle-workspaces"
    / "5ED5Z0CF2MCPXHRAJWW7SBWZSE"
    / "2026-08-27"
    / "disc_01M11F059EHEDVWY"
    / "cyc_01M11F0GRZG9"
)
_BASELINE_ERP = (
    _BASELINE_CYCLE
    / "create-a-complete-greenfield-erp-mini-system-und"
    / "proposed"
    / "erp-system"
)
_EVIDENCE_DIR = (
    _AIX_ROOT
    / "docs"
    / "current"
    / "AIX Closed-Loop Execution Plan"
    / "product-plan-generation-sprint"
    / "evidence"
)
_TASK_ID = "01M11F0G07HMTBFTG5X7CQWPW0"
_CYCLE_ID = "cyc_01M11F0GRZG9"

_INTENT = (
    "Create a complete greenfield ERP mini-system under proposed/erp-system/. "
    "Governance create_root = proposed/. CREATE index.html, styles.css, app.js, "
    "api.php, config.php, database.sql, and README.md. PHP backend and MySQL database. "
    "Include authentication, customers, products, orders."
)


def _budget() -> BudgetV1:
    return BudgetV1(
        time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=12, concurrency=1
    )


def _fresh_erp_plan():
    proposal = propose_outcome_contract_heuristic(
        objective_id="obj_sem001_w6_live_cert",
        objective=_INTENT,
        definition_of_done=(_INTENT,),
    )
    assert proposal.ok and proposal.contract is not None
    contract = proposal.contract
    structure = compile_product_structure(contract).structure
    assert structure is not None
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


def _steps_from_plan(plan, binding) -> list[StepContractV1]:
    budget = _budget()
    bare: list[StepContractV1] = []
    for i, scope in enumerate(plan.step_scopes):
        bare.append(
            StepContractV1(
                cycle_id="cyc-sem001-w6",
                tenant_id="tenant-1",
                step_index=i,
                purpose="Create governed artifacts under create_root",
                target_scope=scope,
                expected_output_schema_ref="aix.step_contract.v1.generic_edit",
                acceptance_criteria=tuple(f"{p} exists" for p in scope),
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
    stamped = list(
        apply_semantic_bindings_to_steps(steps=tuple(bare), bindings=binding.bindings)
    )
    out: list[StepContractV1] = []
    id_by_index: dict[int, str] = {}
    for index, step in enumerate(stamped):
        b = binding.bindings[index]
        ac = build_role_aware_acceptance_criteria(
            scope=step.target_scope,
            role=b.role,
            operation_refs=step.operation_refs,
            entity_refs=step.entity_refs,
            mapping_refs=step.mapping_refs,
        )
        step = step.model_copy(update={"acceptance_criteria": ac})
        id_by_index[index] = step.id
        out.append(step)
    # Wire depends_on like the planner
    resolved: list[StepContractV1] = []
    for index, step in enumerate(out):
        dep_indexes = plan.depends_on[index] if index < len(plan.depends_on) else ()
        depends_on = tuple(
            id_by_index[i] for i in dep_indexes if i in id_by_index and i != index
        )
        if depends_on:
            step = step.model_copy(update={"depends_on": depends_on})
        resolved.append(step)
    return resolved


def _connected_deliverables() -> dict[str, str]:
    """Fresh greenfield-connected fixtures (create_root=proposed/)."""
    return {
        "proposed/erp-system/database.sql": (
            "CREATE TABLE customers (id INT PRIMARY KEY);\n"
            "CREATE TABLE products (id INT PRIMARY KEY);\n"
            "CREATE TABLE orders (id INT PRIMARY KEY);\n"
            "CREATE TABLE sessions (id INT PRIMARY KEY);\n"
        ),
        "proposed/erp-system/api.php": (
            "<?php\n"
            "// Continuity-connected API\n"
            "function auth_login() { return ['ok'=>true]; }\n"
            "function customers_list() { return []; }\n"
            "function products_list() { return []; }\n"
            "function orders_list() { return []; }\n"
            "// ops: auth.login customers.list products.list orders.list\n"
        ),
        "proposed/erp-system/index.html": (
            "<!doctype html><html><body>"
            "<script src='app.js'></script>"
            "<script>fetch('api.php?op=products.list')</script>"
            "</body></html>"
        ),
        "proposed/erp-system/app.js": (
            "export async function callApi(op){ return fetch('api.php?op='+op); }\n"
            "callApi('auth.login');\n"
        ),
        "proposed/erp-system/README.md": "# ERP mini-system (SEM-001-W6 connected)\n",
    }


def test_w6_l1_ref_bearing_prompts_contain_tokens() -> None:
    """L1: Every ref-bearing step's continuity prompt contains op/entity tokens."""
    _, _, plan, binding = _fresh_erp_plan()
    steps = _steps_from_plan(plan, binding)
    checked = 0
    for step in steps:
        if not step_has_continuity_refs(step):
            continue
        section = build_continuity_prompt_section(step)
        assert section.has_content, f"L1 FAIL empty continuity for {step.id}"
        for op in step.operation_refs:
            assert op in section.section_text
        for ent in step.entity_refs:
            assert ent in section.section_text
        checked += 1
    assert checked >= 2, "expected API/DB/UI ref-bearing steps"


def test_w6_l2_upstream_discovery_for_depends_on() -> None:
    """L2: UI/API discovery scope includes depends_on upstream paths."""
    _, _, plan, binding = _fresh_erp_plan()
    steps = _steps_from_plan(plan, binding)
    by_id = {s.id: s for s in steps}
    checked = 0
    for step in steps:
        if not step.depends_on:
            continue
        upstream = resolve_upstream_target_paths(
            step_contract=step, plan_steps=tuple(steps)
        )
        assert upstream.upstream_paths or upstream.unresolved_depends_on == ()
        # At least one prior file path when prior step has file targets
        prior_files = []
        for dep in step.depends_on:
            prior = by_id.get(dep)
            if prior:
                prior_files.extend(
                    p for p in prior.target_scope if "." in Path(str(p)).name
                )
        if prior_files:
            merged = merge_discovery_paths(
                mutation_paths=step.target_scope,
                upstream_paths=upstream.upstream_paths,
            )
            assert any(p in merged for p in prior_files)
            checked += 1
    assert checked >= 1


def test_w6_l3_no_pass_with_coverage_false() -> None:
    """L3: Cannot STEP_PASSED when product_structure_coverage=false and refs nonempty."""
    _, _, plan, binding = _fresh_erp_plan()
    steps = _steps_from_plan(plan, binding)
    api = next(
        s
        for s, b in zip(steps, binding.bindings)
        if b.role == "API_BACKEND" and s.operation_refs
    )
    # Attenuated content (products-only) must not pass
    after = {
        "proposed/erp-system/api.php": "<?php\nfunction products_list() {}\n",
    }
    result = evaluate_completion_gates(
        discovery=DiscoveryReport(config_files=dict(after)),
        diff_stats={"proposed/erp-system/api.php": {"additions": 8, "deletions": 0}},
        diff_text="+api",
        test_result={
            "returncode": 0,
            "stdout": "ok",
            "stderr": "",
            "command": ["test", "-f", "proposed/erp-system/api.php"],
        },
        after_files=after,
        before_files={"proposed/erp-system/api.php": ""},
        step_contract=api,
        objective=api.purpose,
    )
    assert result.gates.get("product_structure_coverage") is False
    assert result.passed is False


def test_w6_l4_connected_deliverable_signals() -> None:
    """L4: Connected UI references api.php; API covers >1 domain of bound ops."""
    files = _connected_deliverables()
    _, _, plan, binding = _fresh_erp_plan()
    steps = _steps_from_plan(plan, binding)
    api_b = next(b for b in binding.bindings if b.role == "API_BACKEND")
    ui_b = next(b for b in binding.bindings if b.role == "UI_PAGE")
    api_step = steps[api_b.step_index]
    ui_step = steps[ui_b.step_index]

    # Restrict to a representative multi-domain subset if fixture has many ops
    sample_ops = tuple(api_step.operation_refs[:8]) or (
        "auth.login",
        "customers.list",
        "products.list",
    )
    api_probe = api_step.model_copy(update={"operation_refs": sample_ops})
    # Ensure connected fixture mentions the sample ops
    body = files["proposed/erp-system/api.php"]
    for op in sample_ops:
        variant = op.replace(".", "_")
        if op not in body and variant not in body:
            body += f"\n// {op}\n"
    files["proposed/erp-system/api.php"] = body

    api_cov = evaluate_product_structure_coverage(
        step_contract=api_probe, after_files=files
    )
    assert api_cov.ok is True, api_cov.detail
    domains = {op.split(".", 1)[0] for op in sample_ops if "." in op}
    assert len(domains) > 1, "L4 requires multi-domain API ops"

    ui_cov = evaluate_product_structure_coverage(
        step_contract=ui_step.model_copy(
            update={"operation_refs": tuple(ui_step.operation_refs[:4]) or ("products.list",)}
        ),
        after_files=files,
    )
    assert ui_cov.ok is True, ui_cov.detail
    assert "api.php" in files["proposed/erp-system/index.html"].casefold() or "api.php" in files[
        "proposed/erp-system/app.js"
    ].casefold()


def test_w6_l5_attenuation_matrix_vs_baseline(tmp_path: Path) -> None:
    """L5: Side-by-side matrix vs task 01M11F0G07HMTBFTG5X7CQWPW0 shows A/B/C closed."""
    assert _BASELINE_ERP.is_dir(), f"baseline ERP missing: {_BASELINE_ERP}"

    baseline_api = (_BASELINE_ERP / "api.php").read_text(encoding="utf-8", errors="replace")
    baseline_ui = (_BASELINE_ERP / "index.html").read_text(encoding="utf-8", errors="replace")
    baseline_app = ""
    if (_BASELINE_ERP / "app.js").is_file():
        baseline_app = (_BASELINE_ERP / "app.js").read_text(encoding="utf-8", errors="replace")

    # Baseline attenuation probes (pre-SEM-001 behavior class)
    attenuated_api_step = StepContractV1(
        cycle_id=_CYCLE_ID,
        tenant_id="t",
        step_index=2,
        purpose="Create API",
        target_scope=("proposed/erp-system/api.php",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("proposed/erp-system/api.php exists",),
        validation_commands=("test -f proposed/erp-system/api.php",),
        required_effects=(RequiredEffectV1(kind="LITERAL_REPLACE"),),
        assigned_role="executor",
        step_budget=_budget(),
        operation_refs=("auth.login", "customers.list", "products.list"),
        status="PENDING",
    )
    # A — prompt: without continuity helper, existence-only would omit ops
    continuity = build_continuity_prompt_section(attenuated_api_step)
    gap_a_closed = continuity.has_content and "auth.login" in continuity.section_text

    # B — upstream: UI depends_on API must resolve upstream path
    api_step = attenuated_api_step.model_copy(update={"id": "step-api"})
    ui_step = attenuated_api_step.model_copy(
        update={
            "id": "step-ui",
            "target_scope": ("proposed/erp-system/index.html",),
            "depends_on": ("step-api",),
            "operation_refs": ("products.list",),
        }
    )
    upstream = resolve_upstream_target_paths(
        step_contract=ui_step, plan_steps=(api_step, ui_step)
    )
    gap_b_closed = "proposed/erp-system/api.php" in upstream.upstream_paths

    # C — gate: baseline disconnected content must FAIL coverage under SEM-001
    api_fail = evaluate_product_structure_coverage(
        step_contract=attenuated_api_step,
        after_files={"proposed/erp-system/api.php": baseline_api},
    )
    ui_fail = evaluate_product_structure_coverage(
        step_contract=ui_step,
        after_files={
            "proposed/erp-system/index.html": baseline_ui,
            "proposed/erp-system/app.js": baseline_app,
        },
    )
    gap_c_closed = (api_fail.ok is False) and (ui_fail.ok is False)

    connected = _connected_deliverables()
    # Ensure connected API mentions auth.login / customers.list / products.list
    connected["proposed/erp-system/api.php"] += (
        "\n// auth.login customers.list products.list\n"
    )
    api_pass = evaluate_product_structure_coverage(
        step_contract=attenuated_api_step, after_files=connected
    )
    ui_pass = evaluate_product_structure_coverage(
        step_contract=ui_step, after_files=connected
    )

    matrix = {
        "task_id": _TASK_ID,
        "cycle_id": _CYCLE_ID,
        "certification": "SEM-001-W6",
        "mode": "deterministic_offline_continuity_cert",
        "create_root": "proposed/",
        "gaps": {
            "A_prompt_continuity": {
                "baseline": "existence-only coder prompts; refs stripped from derived prompt",
                "sem001": "actionable op/entity tokens in derived_execution_prompt",
                "closed": gap_a_closed,
            },
            "B_upstream_discovery": {
                "baseline": "discovery bound to this step targets only; depends_on ignored",
                "sem001": "discovery_paths = target_scope ∪ depends_on.target_scope",
                "closed": gap_b_closed,
            },
            "C_hard_gate": {
                "baseline": "soft PASS on test -f / diff_only / presence; disconnected ERP PASSED",
                "sem001": "product_structure_coverage required; baseline fixture FAIL; connected PASS",
                "closed": gap_c_closed,
                "baseline_api_coverage_ok": api_fail.ok,
                "baseline_ui_coverage_ok": ui_fail.ok,
                "connected_api_coverage_ok": api_pass.ok,
                "connected_ui_coverage_ok": ui_pass.ok,
            },
        },
        "honest_non_claims": [
            "not full product QA",
            "not OpenAPI platform",
            "not WorkHub lifecycle authority",
            "offline deterministic cert (ProductStructure + SEM-001 helpers); not a live OpenRouter cycle",
        ],
    }
    assert gap_a_closed and gap_b_closed and gap_c_closed
    assert api_pass.ok and ui_pass.ok

    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out = _EVIDENCE_DIR / "SEM-001-W6-attenuation-matrix.json"
    out.write_text(json.dumps(matrix, indent=2) + "\n", encoding="utf-8")
    assert out.is_file()
