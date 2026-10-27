"""AIX-SEM-001-W5 — Hostile / regression suite (H1–H5).

Proves attenuation cannot be reintroduced silently:
  H1 Strip refs from prompt builder → unit detects FAIL condition
  H2 Discovery without depends_on upstream → unit detects FAIL
  H3 Soft-pass path with nonempty refs → gate FAIL
  H4 PD-003A/B/B2 suites still PASS (invoked separately / smoke here)
  H5 Disconnected UI+API fixture → product_structure_coverage FAIL
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.executor.multi_step_execute import (
    MultiStepMutationError,
    _plan_and_validate_upstream_discovery,
)
from backend.aix_core.runtime.executor.step_prompt_continuity import (
    build_continuity_prompt_section,
)
from backend.aix_core.runtime.executor.step_upstream_discovery import (
    merge_discovery_paths,
    resolve_upstream_target_paths,
)
from backend.aix_core.runtime.objective.contracts import BudgetV1, RequiredEffectV1, StepContractV1
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
from backend.aix_core.runtime.verification.completion_gates import evaluate_completion_gates
from backend.aix_core.runtime.verification.product_structure_coverage import (
    evaluate_product_structure_coverage,
    step_has_continuity_refs,
)

_LIVE_ERP_ROOT = (
    Path(__file__).resolve().parents[5]
    / ".tmp"
    / "aix-cycle-workspaces"
    / "5ED5Z0CF2MCPXHRAJWW7SBWZSE"
    / "2026-08-27"
    / "disc_01M11F059EHEDVWY"
    / "cyc_01M11F0GRZG9"
    / "create-a-complete-greenfield-erp-mini-system-und"
    / "proposed"
    / "erp-system"
)

_INTENT = (
    "Create greenfield under proposed/erp-system/. Governance create_root = proposed/. "
    "CREATE index.html, styles.css, app.js, api.php, config.php, database.sql, and README.md. "
    "PHP backend and MySQL database. Include authentication, customers, products, orders."
)


def _budget() -> BudgetV1:
    return BudgetV1(
        time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=12, concurrency=1
    )


def _contract(**kwargs: Any) -> StepContractV1:
    base = dict(
        cycle_id="cyc-sem001-w5",
        tenant_id="tenant-1",
        step_index=0,
        purpose="Create API/backend artifacts from ProductStructure",
        target_scope=("proposed/erp-system/api.php",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("proposed/erp-system/api.php exists",),
        validation_commands=("test -f proposed/erp-system/api.php",),
        required_effects=(RequiredEffectV1(kind="LITERAL_REPLACE"),),
        assigned_role="executor",
        step_budget=_budget(),
        status="PENDING",
    )
    base.update(kwargs)
    return StepContractV1(**base)


def test_h1_strip_refs_from_prompt_builder_is_detected() -> None:
    """H1: nonempty refs MUST yield continuity tokens — stripped builder is a FAIL."""
    step = _contract(
        operation_refs=("auth.login", "customers.list", "products.list"),
        entity_refs=(),
    )
    section = build_continuity_prompt_section(step)
    # Hostile condition: stripping refs would empty the section — assert that is illegal.
    assert section.has_content, "H1 FAIL: continuity section empty despite operation_refs"
    for op in step.operation_refs:
        assert op in section.section_text
        assert op in section.actionable_tokens
    # Simulated attack: empty fake prompt must not be accepted as continuous.
    stripped = ""
    assert not all(op in stripped for op in step.operation_refs)


def test_h2_discovery_without_upstream_is_detected(tmp_path: Path) -> None:
    """H2: depends_on must expand discovery; missing upstream fails closed."""
    api = _contract(
        id="step-api",
        step_index=1,
        target_scope=("proposed/erp-system/api.php",),
    )
    ui = _contract(
        id="step-ui",
        step_index=2,
        purpose="Create UI",
        target_scope=("proposed/erp-system/index.html",),
        depends_on=("step-api",),
        operation_refs=("products.list",),
    )
    plan = resolve_upstream_target_paths(step_contract=ui, plan_steps=(api, ui))
    assert "proposed/erp-system/api.php" in plan.upstream_paths
    merged = merge_discovery_paths(
        mutation_paths=ui.target_scope, upstream_paths=plan.upstream_paths
    )
    # Hostile: targets-only discovery for a depends_on step is illegal.
    hostile_targets_only = tuple(ui.target_scope)
    assert "proposed/erp-system/api.php" not in hostile_targets_only
    assert "proposed/erp-system/api.php" in merged

    events: list[dict[str, Any]] = []

    def on_event(name: str, payload: dict[str, Any]) -> None:
        events.append({"event": name, **payload})

    with pytest.raises(MultiStepMutationError) as ei:
        _plan_and_validate_upstream_discovery(
            step_contract=ui,
            plan_steps=(api, ui),
            repository_root=tmp_path,  # api.php absent
            looks_like_a_file=lambda p: "." in Path(p).name,
            events=events,
            on_event=on_event,
        )
    assert ei.value.code == "NEED_UPSTREAM"


def test_h3_soft_pass_with_nonempty_refs_gate_fails() -> None:
    """H3: greenfield presence / diff_only soft-pass cannot PASS with refs."""
    step = _contract(operation_refs=("auth.login", "customers.list"))
    assert step_has_continuity_refs(step)
    after = {
        "proposed/erp-system/api.php": "<?php\necho 'exists';\n" + ("x" * 80),
    }
    result = evaluate_completion_gates(
        discovery=DiscoveryReport(config_files=dict(after)),
        diff_stats={"proposed/erp-system/api.php": {"additions": 10, "deletions": 0}},
        diff_text="+proposed/erp-system/api.php",
        test_result=None,
        already_satisfied=True,
        already_satisfied_reason="greenfield_php_sql_deliverables_already_present",
        diff_only_verification_allowed=True,
        before_files={"proposed/erp-system/api.php": ""},
        after_files=after,
        step_contract=step,
        objective=step.purpose,
    )
    assert result.passed is False
    assert result.gates.get("product_structure_coverage") is False


def test_h4_pd003_product_structure_modules_importable() -> None:
    """H4 smoke: PD-003A/B/B2 modules remain importable (full suites run in CI cmd)."""
    from backend.aix_core.runtime.objective import product_structure as a
    from backend.aix_core.runtime.objective import product_structure_plan as b
    from backend.aix_core.runtime.objective import product_structure_step_binding as b2

    assert hasattr(a, "compile_product_structure")
    assert hasattr(b, "compile_product_structure_plan")
    assert hasattr(b2, "compile_product_structure_step_bindings")


def test_h5_disconnected_live_erp_fixture_fails_coverage() -> None:
    """H5: Live attenuated ERP deliverable (UI mock + products-only API) → gate FAIL."""
    if not _LIVE_ERP_ROOT.is_dir():
        pytest.skip(f"live ERP fixture missing: {_LIVE_ERP_ROOT}")

    api_path = _LIVE_ERP_ROOT / "api.php"
    ui_path = _LIVE_ERP_ROOT / "index.html"
    app_path = _LIVE_ERP_ROOT / "app.js"
    assert api_path.is_file() and ui_path.is_file()

    api_body = api_path.read_text(encoding="utf-8", errors="replace")
    ui_body = ui_path.read_text(encoding="utf-8", errors="replace")
    app_body = app_path.read_text(encoding="utf-8", errors="replace") if app_path.is_file() else ""

    # Bound ops from the class of ProductStructure ERP (auth + customers + products).
    api_step = _contract(
        operation_refs=("auth.login", "customers.list", "products.list", "orders.list"),
        target_scope=("proposed/erp-system/api.php",),
    )
    api_cov = evaluate_product_structure_coverage(
        step_contract=api_step,
        after_files={"proposed/erp-system/api.php": api_body},
    )
    assert api_cov.ok is False, "H5: products-only live API must fail multi-domain ops coverage"
    assert api_cov.missing_operation_refs

    ui_step = _contract(
        purpose="Create UI page views from ProductStructure",
        target_scope=("proposed/erp-system/index.html",),
        operation_refs=("auth.login", "products.list"),
        mapping_refs=("map-ui-api",),
    )
    ui_cov = evaluate_product_structure_coverage(
        step_contract=ui_step,
        after_files={
            "proposed/erp-system/index.html": ui_body,
            "proposed/erp-system/app.js": app_body,
        },
    )
    assert ui_cov.ok is False, "H5: disconnected UI mock must fail wiring coverage"
    assert ui_cov.ui_wiring_ok is False

    gate = evaluate_completion_gates(
        discovery=DiscoveryReport(
            config_files={
                "proposed/erp-system/api.php": api_body,
                "proposed/erp-system/index.html": ui_body,
            }
        ),
        diff_stats={
            "proposed/erp-system/api.php": {"additions": 20, "deletions": 0},
            "proposed/erp-system/index.html": {"additions": 10, "deletions": 0},
        },
        diff_text="+api.php\n+index.html",
        test_result={
            "returncode": 0,
            "stdout": "path_exists",
            "stderr": "",
            "command": ["test", "-f", "proposed/erp-system/api.php"],
        },
        after_files={"proposed/erp-system/api.php": api_body},
        before_files={"proposed/erp-system/api.php": ""},
        step_contract=api_step,
        objective="Create API from ProductStructure",
    )
    assert gate.passed is False
    assert gate.gates.get("product_structure_coverage") is False


def test_h5b_erp_fixture_bindings_still_compile() -> None:
    """Hostile pack sanity: ProductStructure bind still works for ERP intent."""
    proposal = propose_outcome_contract_heuristic(
        objective_id="obj_sem001_w5_h5",
        objective=_INTENT,
        definition_of_done=(_INTENT,),
    )
    assert proposal.ok and proposal.contract is not None
    structure = compile_product_structure(proposal.contract).structure
    assert structure is not None
    plan = compile_product_structure_plan(
        contract=proposal.contract,
        structure=structure,
        intent_text=_INTENT,
        create_roots=("proposed/",),
        create_capability=("CREATE_FILE", "CREATE_DIRECTORY"),
        outcome_claim_ids=("outcome_1",),
    )
    assert plan.ok, plan.detail
    binding = compile_product_structure_step_bindings(
        contract=proposal.contract,
        structure=structure,
        step_scopes=plan.step_scopes,
        traces=plan.traces,
    )
    assert binding.ok, binding.detail
    api = next(b for b in binding.bindings if b.role == "API_BACKEND")
    assert api.operation_refs
