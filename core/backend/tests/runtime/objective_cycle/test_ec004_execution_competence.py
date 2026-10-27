"""AIX-EC-004: execution competence — typed effects, dispatch, PHP structure, verify, repair."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from backend.aix_core.runtime.executor.capability_dispatch import CapabilityMismatch, select_strategy
from backend.aix_core.runtime.verification.completion_gates import evaluate_completion_gates
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    ExecutorProfileV1,
    RequiredEffectV1,
    RepairDirectiveV1,
    RiskTier,
    StepContractV1,
)
from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.executor.executor_profile import (
    STRATEGY_PHP_COMMENTS,
    STRATEGY_PHP_STRUCTURAL,
    default_executor_profile,
    strategy_profile,
)
from backend.aix_core.intelligence.repository.file_structure import build_file_outline
from backend.aix_core.runtime.executor.multi_step_execute import run_multi_step_mutation
from backend.aix_core.intelligence.repository.php_structural import (
    extract_repeated_query_helpers,
    group_public_methods,
    parse_php_structure,
    public_symbol_inventory,
    remove_unused_imports,
)
from backend.aix_core.runtime.recovery.repair_directive import build_repair_directive
from backend.aix_core.intelligence.repository.repository_tools import RepositoryToolbox
from backend.aix_core.runtime.executor.required_effects import infer_required_effects, parse_required_effects
from backend.aix_core.runtime.verification.structural_verify import verify_structural_effects

_BUDGET = BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=2, steps=5, concurrency=1)

_FIXTURE = """<?php
namespace App\\Http\\Controllers;
use App\\Models\\UsedModel;
use App\\Models\\NeverReferenced;
use DB;

class BatchController extends Controller
{
	public function runAlphaJob() {
		$mdb = env('DB_DATABASE');
		$checkDB = \\DB::select("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME =  ?", [$mdb]);
		return UsedModel::query()->count();
	}

	public function notifyUsers() {
		return 1;
	}

	public function runBetaJob() {
		$mdb = env('DB_DATABASE');
		$checkDB = \\DB::select("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME =  ?", [$mdb]);
		return 2;
	}

	public function alphaSummary() {
		return UsedModel::query()->count();
	}
}
"""


def _step(
    *,
    purpose: str,
    effects: tuple[RequiredEffectV1, ...],
    target: str = "BatchController.php",
    validation_commands: tuple[str, ...] = ("php -l BatchController.php",),
) -> StepContractV1:
    return StepContractV1(
        cycle_id="cyc-1",
        tenant_id="tenant-1",
        step_index=0,
        purpose=purpose,
        target_scope=(target,),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("done",),
        validation_commands=validation_commands,
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        required_effects=effects,
        status="PENDING",
    )


def _init_git(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


class TestEC01RequiredEffects:
    def test_parse_and_infer_are_typed_not_coverage_keywords(self) -> None:
        parsed = parse_required_effects(
            [{"kind": "GROUP_METHODS", "grouping_keys": ["Alpha", "Beta"]}]
        )
        assert parsed[0].kind == "GROUP_METHODS"
        assert parsed[0].grouping_keys == ("Alpha", "Beta")
        inferred = infer_required_effects(
            purpose="Group public methods by domain (Alpha, Beta) and extract repeated DB boilerplate",
            acceptance_criteria=("preserve public names",),
            validation_commands=("php -l BatchController.php",),
        )
        kinds = {e.kind for e in inferred}
        assert "GROUP_METHODS" in kinds
        assert "EXTRACT_HELPER" in kinds
        assert "PRESERVE_PUBLIC_INTERFACE" in kinds
        assert "RUN_DECLARED_VALIDATION" in kinds


class TestEC02TruthfulProfile:
    def test_default_profile_is_not_blanket_true(self) -> None:
        profile = default_executor_profile()
        assert profile.supports_multi_file is False
        assert profile.context_budget < 128_000
        assert "GROUP_METHODS" in profile.supported_effects
        assert profile.supports_structural_edit is True

    def test_comment_fallback_supports_nothing(self) -> None:
        comments = strategy_profile(STRATEGY_PHP_COMMENTS)
        assert comments.supported_effects == ()
        assert comments.supports_structural_edit is False

    def test_cannot_claim_structural_edit_without_effects(self) -> None:
        with pytest.raises(Exception):
            ExecutorProfileV1(executor_id="x", supports_structural_edit=True)


class TestEC03Dispatch:
    def test_grouping_selects_php_structural_not_comments(self) -> None:
        choice = select_strategy(
            effects=(RequiredEffectV1(kind="GROUP_METHODS"),),
            language="php",
        )
        assert choice.strategy_id == STRATEGY_PHP_STRUCTURAL

    def test_forbidden_strategy_is_skipped(self) -> None:
        with pytest.raises(CapabilityMismatch) as exc:
            select_strategy(
                effects=(RequiredEffectV1(kind="GROUP_METHODS"),),
                language="php",
                forbidden_strategies=(STRATEGY_PHP_STRUCTURAL,),
            )
        assert exc.value.code == "UNSUPPORTED_CAPABILITY"


class TestEC04Outline:
    def test_php_outline_lists_methods_and_imports(self) -> None:
        outline = build_file_outline("BatchController.php", _FIXTURE)
        assert outline.language == "php"
        assert "runAlphaJob" in outline.text
        assert "NeverReferenced" in outline.text
        assert "L" in outline.text
        assert outline.public_symbols[0] == "runAlphaJob"


class TestEC05PhpStructural:
    def test_group_moves_methods_contiguous(self) -> None:
        updated, groups = group_public_methods(
            _FIXTURE, grouping_keys=("Alpha", "Beta")
        )
        order = [
            m.name
            for m in parse_php_structure(updated).methods
            if m.visibility == "public"
        ]
        assert "runAlphaJob" in order
        alpha = next(names for key, names in groups if key.lower() == "alpha")
        positions = [order.index(n) for n in alpha]
        assert max(positions) - min(positions) + 1 == len(positions)

    def test_extract_repeated_db_and_unused_imports(self) -> None:
        updated, helpers = extract_repeated_query_helpers(_FIXTURE, min_sites=2)
        assert helpers
        assert "$this->" in updated
        cleaned, n = remove_unused_imports(updated)
        assert n >= 1
        assert "NeverReferenced" not in cleaned
        assert public_symbol_inventory(cleaned) == public_symbol_inventory(_FIXTURE)


class TestEC06StructuralVerify:
    def test_comment_only_fails_grouping(self) -> None:
        commented = _FIXTURE.replace(
            "public function runAlphaJob()",
            "// --- Alpha ---\n\tpublic function runAlphaJob()",
        )
        diff = (
            "--- a/BatchController.php\n+++ b/BatchController.php\n@@ -1 +1,2 @@\n"
            "+// --- Alpha ---\n public function runAlphaJob() {\n"
        )
        result = verify_structural_effects(
            path="BatchController.php",
            before=_FIXTURE,
            after=commented,
            effects=(RequiredEffectV1(kind="GROUP_METHODS", grouping_keys=("Alpha", "Beta")),),
            diff_text=diff,
        )
        assert result.passed is False
        assert result.comment_only is True
        assert "GROUP_METHODS" in result.missing_effects

    def test_real_reorder_passes_grouping(self) -> None:
        updated, _groups = group_public_methods(_FIXTURE, grouping_keys=("Alpha", "Beta"))
        result = verify_structural_effects(
            path="BatchController.php",
            before=_FIXTURE,
            after=updated,
            effects=(RequiredEffectV1(kind="GROUP_METHODS", grouping_keys=("Alpha", "Beta")),),
            diff_text="not-comment-only +function",
        )
        assert result.passed is True


class TestEC07RepairStrategy:
    def test_no_progress_forbids_last_strategy(self) -> None:
        step = _step(
            purpose="Group methods",
            effects=(RequiredEffectV1(kind="GROUP_METHODS"),),
        )
        directive = build_repair_directive(
            step=step,
            reasons=("comments cannot prove grouping",),
            last_strategy_id=STRATEGY_PHP_COMMENTS,
            remaining_asked_work=True,
            comment_only=True,
            made_progress=False,
        )
        assert STRATEGY_PHP_COMMENTS in directive.forbidden_strategies
        assert directive.preferred_strategy == STRATEGY_PHP_STRUCTURAL
        assert directive.insufficiency_class == "STRATEGY_INSUFFICIENT"
        assert directive.remaining_asked_work is True


class TestEC08HotPath:
    def test_comment_coverage_cannot_pass_required_group_effect(self) -> None:
        discovery = DiscoveryReport()
        discovery.config_files["BatchController.php"] = _FIXTURE
        commented = _FIXTURE.replace(
            "public function runAlphaJob()",
            "// --- Alpha ---\n\tpublic function runAlphaJob()",
        )
        diff = "+// --- Alpha ---\n-public function runAlphaJob()\n+public function runAlphaJob()\n"
        gate = evaluate_completion_gates(
            discovery=discovery,
            diff_stats={"BatchController.php": {"additions": 1, "deletions": 0}},
            diff_text=diff,
            test_result={"returncode": 0},
            required_effects=(RequiredEffectV1(kind="GROUP_METHODS"),),
            before_files={"BatchController.php": _FIXTURE},
            after_files={"BatchController.php": commented},
        )
        assert gate.passed is False
        assert gate.gates.get("structural_effects") is False

    def test_run_multi_step_mutation_performs_structural_refactor(self) -> None:
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).strftime("%H%M%S%f")
        repo = Path(r"D:\v2.1\AIX\.tmp") / f"ec004-hotpath-{stamp}"
        repo.mkdir(parents=True)
        (repo / "BatchController.php").write_text(_FIXTURE, encoding="utf-8", newline="\n")
        _init_git(repo)
        events: list[dict] = []
        step = _step(
            purpose="Group Alpha/Beta methods, extract repeated DB queries, remove unused imports",
            effects=(
                RequiredEffectV1(kind="GROUP_METHODS", grouping_keys=("Alpha", "Beta")),
                RequiredEffectV1(kind="EXTRACT_HELPER", min_call_sites=2),
                RequiredEffectV1(kind="STRIP_UNUSED_IMPORTS"),
                RequiredEffectV1(kind="PRESERVE_PUBLIC_INTERFACE"),
            ),
        )

        def _infer(_manifests: set[str]) -> list[str] | None:
            return None

        result = asyncio.run(
            run_multi_step_mutation(
                objective=step.purpose,
                toolbox=RepositoryToolbox(repository_root=repo, on_event=lambda *_a, **_k: None),
                repository_root=repo,
                settings=None,
                model_override=None,
                budget_mgr=None,
                on_event=lambda *_a, **_k: None,
                events=events,
                allowed_files=("BatchController.php",),
                apply_edits=True,
                task_family="REFACTOR",
                evaluate_completion_gates=evaluate_completion_gates,
                infer_test_command=_infer,
                normalize_repo_relative_path=lambda p: p.replace("\\", "/").lstrip("/"),
                looks_like_a_file=lambda p: bool(p) and "." in Path(p).name,
                extract_target_path=lambda _o: "BatchController.php",
                resolve_relative_path=lambda p, _root: p,
                step_contract=step,
                required_effects=step.required_effects,
            )
        )
        after = (repo / "BatchController.php").read_text(encoding="utf-8")
        assert result.ok
        assert result.strategy_id == STRATEGY_PHP_STRUCTURAL
        assert "NeverReferenced" not in after
        assert "$this->" in after
        order = [
            m.name
            for m in parse_php_structure(after).methods
            if m.visibility == "public"
        ]
        assert order.index("runAlphaJob") < order.index("notifyUsers") or "alphaSummary" in after
        assert set(public_symbol_inventory(after)) == set(public_symbol_inventory(_FIXTURE))

    def test_large_php_class_round_trip_preserves_public_methods(self) -> None:
        large = Path(r"D:\v2.1\Visions\CronJobController.php")
        if not large.is_file():
            pytest.skip("large PHP fixture not present")
        src = large.read_text(encoding="utf-8", errors="replace")
        before = parse_php_structure(src)
        assert len(before.methods) >= 20
        before_public = set(public_symbol_inventory(src))
        grouped, groups = group_public_methods(src)
        after_group = parse_php_structure(grouped)
        assert sorted(m.name for m in after_group.methods) == sorted(m.name for m in before.methods)
        assert set(public_symbol_inventory(grouped)) == before_public
        assert any(len(names) >= 2 for _key, names in groups)
        extracted, helpers = extract_repeated_query_helpers(grouped, min_sites=2)
        assert helpers
        cleaned, removed = remove_unused_imports(extracted)
        assert set(n for n in public_symbol_inventory(cleaned) if n != "__construct") == set(
            n for n in before_public if n != "__construct"
        )
        assert "$this->" in cleaned
        result = verify_structural_effects(
            path="LargeController.php",
            before=src,
            after=cleaned,
            effects=(
                RequiredEffectV1(kind="GROUP_METHODS"),
                RequiredEffectV1(kind="EXTRACT_HELPER", min_call_sites=2),
                RequiredEffectV1(kind="STRIP_UNUSED_IMPORTS"),
                RequiredEffectV1(kind="PRESERVE_PUBLIC_INTERFACE"),
            ),
            diff_text="not-comment-only +function private",
        )
        assert result.passed, result.detail
        # Touching the original fixture path is forbidden.
        assert large.read_text(encoding="utf-8", errors="replace") == src

    def test_run_multi_step_mutation_on_large_php_fixture(self) -> None:
        from datetime import datetime, timezone

        source = Path(r"D:\v2.1\Visions\CronJobController.php")
        if not source.is_file():
            pytest.skip("large PHP fixture not present")
        original = source.read_text(encoding="utf-8", errors="replace")
        stamp = datetime.now(timezone.utc).strftime("%H%M%S%f")
        repo = Path(r"D:\v2.1\AIX\.tmp") / f"ec004-hotpath-large-{stamp}"
        repo.mkdir(parents=True)
        target = repo / "LargeController.php"
        target.write_text(original, encoding="utf-8", newline="\n")
        _init_git(repo)
        before_public = set(public_symbol_inventory(original))
        before_order = [
            m.name
            for m in parse_php_structure(original).methods
            if m.visibility == "public" and m.name != "__construct"
        ]
        step = _step(
            purpose="Group public methods by domain, extract repeated DB queries, remove unused imports",
            target="LargeController.php",
            validation_commands=("php -l LargeController.php",),
            effects=(
                RequiredEffectV1(kind="GROUP_METHODS"),
                RequiredEffectV1(kind="EXTRACT_HELPER", min_call_sites=2),
                RequiredEffectV1(kind="STRIP_UNUSED_IMPORTS"),
                RequiredEffectV1(kind="PRESERVE_PUBLIC_INTERFACE"),
                RequiredEffectV1(kind="RUN_DECLARED_VALIDATION"),
            ),
        )
        result = asyncio.run(
            run_multi_step_mutation(
                objective=step.purpose,
                toolbox=RepositoryToolbox(repository_root=repo, on_event=lambda *_a, **_k: None),
                repository_root=repo,
                settings=None,
                model_override=None,
                budget_mgr=None,
                on_event=lambda *_a, **_k: None,
                events=[],
                allowed_files=("LargeController.php",),
                apply_edits=True,
                task_family="REFACTOR",
                evaluate_completion_gates=evaluate_completion_gates,
                infer_test_command=lambda _m: None,
                normalize_repo_relative_path=lambda p: p.replace("\\", "/").lstrip("/"),
                looks_like_a_file=lambda p: bool(p) and "." in Path(p).name,
                extract_target_path=lambda _o: "LargeController.php",
                resolve_relative_path=lambda p, _root: p,
                step_contract=step,
                required_effects=step.required_effects,
            )
        )
        after = target.read_text(encoding="utf-8")
        after_order = [
            m.name
            for m in parse_php_structure(after).methods
            if m.visibility == "public" and m.name != "__construct"
        ]
        assert result.ok, getattr(result, "detail", "")
        assert result.strategy_id == STRATEGY_PHP_STRUCTURAL
        assert after_order != before_order
        assert "$this->" in after
        assert set(public_symbol_inventory(after)) == before_public
        assert source.read_text(encoding="utf-8", errors="replace") == original

    def test_repair_does_not_reselect_comment_strategy(self) -> None:
        step = _step(
            purpose="Group methods",
            effects=(RequiredEffectV1(kind="GROUP_METHODS"),),
        )
        directive = RepairDirectiveV1(
            step_id=step.id,
            remaining_requirements=("grouped",),
            required_correction="comments are not grouping",
            forbidden_strategies=(STRATEGY_PHP_COMMENTS,),
            preferred_strategy=STRATEGY_PHP_STRUCTURAL,
            remaining_asked_work=True,
            insufficiency_class="STRATEGY_INSUFFICIENT",
        )
        choice = select_strategy(
            effects=step.required_effects,
            language="php",
            forbidden_strategies=directive.forbidden_strategies,
            preferred_strategy=directive.preferred_strategy,
        )
        assert choice.strategy_id == STRATEGY_PHP_STRUCTURAL
        assert choice.strategy_id != STRATEGY_PHP_COMMENTS


class TestIntentAndValidation:
    def test_grouping_purpose_classifies_as_edit(self) -> None:
        from backend.aix_core.runtime.executor.real_execute import classify_intent

        intent = classify_intent(
            "Planner guidance: Group the public cron action methods by domain",
            target_path="LargeController.php",
            symbol_name=None,
        )
        assert intent == "EDIT_FILE"

    def test_preserve_only_planner_effects_still_infer_group(self) -> None:
        from backend.aix_core.runtime.executor.required_effects import effects_for_step

        step = _step(
            purpose="Group public methods by domain and extract repeated DB queries",
            effects=(RequiredEffectV1(kind="PRESERVE_PUBLIC_INTERFACE"),),
        )
        kinds = {e.kind for e in effects_for_step(step)}
        assert "GROUP_METHODS" in kinds
        assert "EXTRACT_HELPER" in kinds
        assert "PRESERVE_PUBLIC_INTERFACE" in kinds

    def test_validation_command_with_metacharacters_is_sanitized(self) -> None:
        from backend.aix_core.runtime.executor.multi_step_execute import _safe_validation_argv

        assert _safe_validation_argv(
            "php -l CronJobController.php && vendor/bin/phpunit",
            fallback_file="CronJobController.php",
        ) == ["php", "-l", "CronJobController.php"]
        assert _safe_validation_argv(
            "php -l Foo.php 2>&1",
            fallback_file="Foo.php",
        ) == ["php", "-l", "Foo.php"]

    def test_structural_gate_helper_reads_completion_gates(self) -> None:
        from backend.aix_core.runtime.verification.semantic_verifier import _structural_effects_passed
        from backend.aix_core.runtime.executor.real_execute import RealExecuteResult

        result = RealExecuteResult(
            ok=True,
            code="REAL_EXECUTE_OK",
            workspace="w",
            completion_gates={"gates": {"structural_effects": True}, "passed": True},
        )
        assert _structural_effects_passed(result) is True

