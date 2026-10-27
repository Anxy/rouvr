"""AIX-SEM-001-W4 — Hard gates / product_structure_coverage.

W4-T1: Mock API with only /products while ops include auth.login+customers → GATE FAIL
W4-T2: API implementing all op tokens → GATE PASS
W4-T3: UI with no api.php/fetch and UI ops bound → GATE FAIL
W4-T4: Windows test -f via Path.exists (no false skip → diff_only)
W4-T5: Empty-ref README step → still PASS on existence
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.aix_core.runtime.executor.multi_step_execute import _posix_existence_validation
from backend.aix_core.runtime.objective.contracts import BudgetV1, RequiredEffectV1, StepContractV1
from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.verification.completion_gates import evaluate_completion_gates
from backend.aix_core.runtime.verification.product_structure_coverage import (
    evaluate_product_structure_coverage,
    step_has_continuity_refs,
)


def _budget() -> BudgetV1:
    return BudgetV1(
        time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=10, concurrency=1
    )


def _contract(**kwargs: Any) -> StepContractV1:
    base = dict(
        cycle_id="cyc-sem001-w4",
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


def _discovery(files: dict[str, str]) -> DiscoveryReport:
    return DiscoveryReport(config_files=dict(files))


def _gate(
    *,
    step: StepContractV1,
    after_files: dict[str, str],
    test_result: dict[str, Any] | None = None,
    diff_only: bool = False,
    already_satisfied: bool = False,
    already_satisfied_reason: str = "",
) -> Any:
    paths = list(after_files)
    diff_stats = {p: {"additions": 5, "deletions": 0} for p in paths}
    diff_text = "\n".join(f"+{p}" for p in paths)
    return evaluate_completion_gates(
        discovery=_discovery(after_files),
        diff_stats=diff_stats,
        diff_text=diff_text or "+x",
        test_result=test_result
        if test_result is not None
        else {"returncode": 0, "stdout": "ok", "stderr": "", "command": ["test", "-f", paths[0]]},
        already_satisfied=already_satisfied,
        already_satisfied_reason=already_satisfied_reason,
        diff_only_verification_allowed=diff_only,
        before_files={p: "" for p in paths},
        after_files=after_files,
        step_contract=step,
        objective=step.purpose,
    )


def test_w4_t1_products_only_api_fails_coverage() -> None:
    """W4-T1: products-only API while ops include auth.login + customers → FAIL."""
    ops = ("auth.login", "customers.list", "products.list")
    step = _contract(operation_refs=ops)
    after = {
        "proposed/erp-system/api.php": "<?php\n// products only\nfunction products_list() {}\n"
    }
    cov = evaluate_product_structure_coverage(
        step_contract=step, after_files=after
    )
    assert cov.checked
    assert cov.ok is False
    assert "auth.login" in cov.missing_operation_refs
    assert "customers.list" in cov.missing_operation_refs

    result = _gate(step=step, after_files=after)
    assert result.passed is False
    assert result.gates.get("product_structure_coverage") is False
    assert any("PRODUCT_STRUCTURE_COVERAGE_FAILED" in r for r in result.reasons)


def test_w4_t2_api_all_op_tokens_pass() -> None:
    """W4-T2: API implementing all op tokens → GATE PASS (mechanical)."""
    ops = ("auth.login", "customers.list", "products.list")
    step = _contract(operation_refs=ops)
    after = {
        "proposed/erp-system/api.php": (
            "<?php\n"
            "function auth_login() {}\n"
            "function customers_list() {}\n"
            "function products_list() {}\n"
            "// routes: auth.login customers.list products.list\n"
        )
    }
    cov = evaluate_product_structure_coverage(step_contract=step, after_files=after)
    assert cov.ok is True
    assert cov.missing_operation_refs == ()

    result = _gate(step=step, after_files=after)
    assert result.gates.get("product_structure_coverage") is True
    assert result.passed is True


def test_w4_t3_ui_without_api_wiring_fails() -> None:
    """W4-T3: UI with no api.php/fetch and UI ops bound → GATE FAIL."""
    step = _contract(
        purpose="Create UI page views from ProductStructure",
        target_scope=("proposed/erp-system/index.html",),
        acceptance_criteria=("proposed/erp-system/index.html exists",),
        validation_commands=("test -f proposed/erp-system/index.html",),
        operation_refs=("auth.login", "products.list"),
        mapping_refs=("map-ui-api-1",),
    )
    after = {
        "proposed/erp-system/index.html": (
            "<html><body><h1>ERP Mock</h1><p>static only</p></body></html>"
        )
    }
    cov = evaluate_product_structure_coverage(step_contract=step, after_files=after)
    assert cov.checked
    assert cov.ok is False
    assert cov.ui_wiring_ok is False

    result = _gate(step=step, after_files=after)
    assert result.passed is False
    assert result.gates.get("product_structure_coverage") is False


def test_w4_t3_ui_with_api_php_passes_wiring() -> None:
    """UI that references api.php satisfies wiring when ops are bound."""
    step = _contract(
        purpose="Create UI page views from ProductStructure",
        target_scope=("proposed/erp-system/index.html",),
        operation_refs=("products.list",),
    )
    after = {
        "proposed/erp-system/index.html": (
            "<html><script>fetch('api.php?op=products.list')</script></html>"
        )
    }
    cov = evaluate_product_structure_coverage(step_contract=step, after_files=after)
    assert cov.ok is True
    assert cov.ui_wiring_ok is True


def test_w4_t4_test_f_via_path_exists(tmp_path: Path) -> None:
    """W4-T4: test -f implemented via Path.exists — no false skip to diff_only."""
    target = tmp_path / "proposed" / "erp-system" / "README.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# ok\n", encoding="utf-8")

    hit = _posix_existence_validation(
        repository_root=tmp_path,
        argv=["test", "-f", "proposed/erp-system/README.md"],
    )
    assert hit is not None
    assert hit["returncode"] == 0

    miss = _posix_existence_validation(
        repository_root=tmp_path,
        argv=["test", "-f", "proposed/erp-system/missing.md"],
    )
    assert miss is not None
    assert miss["returncode"] == 1

    # Ref-bearing step cannot clear via diff_only even if caller asks for it.
    step = _contract(operation_refs=("auth.login",))
    after = {"proposed/erp-system/api.php": "<?php\n// incomplete\n"}
    result = _gate(step=step, after_files=after, diff_only=True)
    assert result.gates.get("product_structure_coverage") is False
    # verification_ran must not be satisfied solely by diff_only opt-in
    # (continuity forces diff_only off inside evaluate_completion_gates)
    assert result.passed is False


def test_w4_t5_empty_ref_readme_existence_pass() -> None:
    """W4-T5: Empty-ref README step still PASS on existence / legacy path."""
    step = _contract(
        purpose="Create supporting artifacts under create_root",
        target_scope=("proposed/erp-system/README.md",),
        acceptance_criteria=("proposed/erp-system/README.md exists",),
        validation_commands=("test -f proposed/erp-system/README.md",),
        operation_refs=(),
        entity_refs=(),
        mapping_refs=(),
        obligation_refs=(),
    )
    assert step_has_continuity_refs(step) is False
    after = {"proposed/erp-system/README.md": "# ERP mini system\n"}
    cov = evaluate_product_structure_coverage(step_contract=step, after_files=after)
    assert cov.checked is False
    assert cov.ok is True

    result = _gate(
        step=step,
        after_files=after,
        test_result={
            "returncode": 0,
            "stdout": "path_exists:proposed/erp-system/README.md",
            "stderr": "",
            "command": ["test", "-f", "proposed/erp-system/README.md"],
        },
    )
    assert result.gates.get("product_structure_coverage") is True
    assert result.passed is True


def test_w4_opaque_dod_obligation_ids_do_not_force_token_match() -> None:
    """ULID DoD-obligation refs carry no derivable content token.

    Live lane: derive_obligations mints ULID obligation IDs and the planner
    correctly stamps them on the step; requiring derived-token hits would fail
    every such step unconditionally. Actionable obl_* refs stay enforced.
    """
    ulid = "01M1SGYZYN8H1Y7K1FMWXE86RJ"
    after = {"proposed/notes.md": "# Live Run 3\n\n* First bullet line\n* Second bullet line\n"}
    step = _contract(obligation_refs=(ulid,))
    cov = evaluate_product_structure_coverage(step_contract=step, after_files=after)
    assert cov.ok is True
    # Continuity law intact: the step is still ref-bearing.
    assert step_has_continuity_refs(step) is True
    # Actionable obl_* refs without corpus tokens still fail closed.
    step2 = _contract(obligation_refs=("obl_api_auth_login",))
    cov2 = evaluate_product_structure_coverage(step_contract=step2, after_files=after)
    assert cov2.ok is False


def test_w4_diff_only_disabled_for_ref_bearing_soft_presence() -> None:
    """Presence soft-pass alone must not PASS when refs nonempty."""
    step = _contract(operation_refs=("auth.login", "customers.list"))
    after = {
        "proposed/erp-system/api.php": "<?php\necho 'hello';\n" + ("x" * 80),
    }
    result = _gate(
        step=step,
        after_files=after,
        already_satisfied=True,
        already_satisfied_reason="greenfield_php_sql_deliverables_already_present",
        diff_only=True,
        test_result=None,
    )
    assert result.passed is False
    assert result.gates.get("product_structure_coverage") is False
