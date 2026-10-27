"""Completion-gate enforcement: COMPLETED_FOR_REVIEW is never reached without real
evidence for every required gate; any gate failure fails closed to BLOCKED."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.aix_core.runtime.verification.completion_gates import (
    RUN_ARTIFACT_PREFIXES,
    evaluate_completion_gates,
    split_changed_paths,
)
from backend.aix_core.runtime.objective.discovery import DiscoveryReport, run_repository_discovery
from backend.aix_core.intelligence.repository.repository_tools import RepositoryToolbox
from backend.aix_core.runtime.executor.source_patch_codegen import local_fallback_text_replacements


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


@pytest.fixture
def repo_with_real_edit(tmp_path: Path) -> tuple[Path, DiscoveryReport, dict]:
    (tmp_path / "config.py").write_text("VALUE_ALPHA=''\nVALUE_BETA=1\n", encoding="utf-8", newline="\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    box = RepositoryToolbox(repository_root=tmp_path)
    report = run_repository_discovery(toolbox=box, objective="replace 'VALUE_ALPHA' with 'VALUE_OMEGA'")
    # Ensure the target file is present for replacement fallbacks.
    if "config.py" not in report.config_files:
        report.config_files["config.py"] = (tmp_path / "config.py").read_text(encoding="utf-8")
    patch = local_fallback_text_replacements(report, "replace 'VALUE_ALPHA' with 'VALUE_OMEGA'")
    assert patch is not None and patch.edits
    for edit in patch.edits:
        box.apply_patch(edit.unified_diff)
    diff_result = box.get_git_diff()
    return tmp_path, report, diff_result


def test_real_source_edit_with_passing_tests_passes_all_gates(repo_with_real_edit) -> None:
    repo, report, diff_result = repo_with_real_edit
    box = RepositoryToolbox(repository_root=repo)
    test_result = box.run_tests(["pytest", "-q"])

    result = evaluate_completion_gates(
        discovery=report,
        diff_stats=diff_result["stats"],
        diff_text=diff_result["diff"],
        test_result=test_result,
    )
    assert result.passed is True
    assert result.gates == {
        "source_read": True,
        "source_changed": True,
        "diff_exists": True,
        "verification_ran": True,
        "verification_passed": True,
        "no_secrets_leaked": True,
        "objective_coverage": True,
        "refactor_substantive": True,
        "product_structure_coverage": True,
    }
    assert "config.py" in result.changed_source_files
    assert result.run_artifacts == []
    assert result.verification_verdict == "PASSED"


def test_no_source_change_and_not_already_satisfied_blocks(repo_with_real_edit) -> None:
    _repo, report, _diff = repo_with_real_edit
    result = evaluate_completion_gates(
        discovery=report,
        diff_stats={},
        diff_text="",
        test_result=None,
    )
    assert result.passed is False
    assert result.gates["source_changed"] is False
    assert result.gates["diff_exists"] is False
    assert "no real source file was changed and the objective was not proven already satisfied" in result.reasons


def test_already_satisfied_with_evidence_passes_without_a_diff(repo_with_real_edit) -> None:
    _repo, report, _diff = repo_with_real_edit
    result = evaluate_completion_gates(
        discovery=report,
        diff_stats={},
        diff_text="",
        test_result=None,
        already_satisfied=True,
        already_satisfied_reason="VALUE_HOST_READY already present in config.py",
        already_satisfied_evidence_refs=("config.py",),
    )
    assert result.passed is True
    assert result.gates["source_changed"] is True
    assert result.gates["diff_exists"] is True


def test_already_satisfied_without_a_reason_is_rejected(repo_with_real_edit) -> None:
    _repo, report, _diff = repo_with_real_edit
    result = evaluate_completion_gates(
        discovery=report,
        diff_stats={},
        diff_text="",
        test_result=None,
        already_satisfied=True,
        already_satisfied_reason="",
        already_satisfied_evidence_refs=("config.py",),
    )
    assert result.passed is False
    assert any("requires a recorded reason" in reason for reason in result.reasons)


def test_failing_verification_blocks_even_with_a_real_change(repo_with_real_edit) -> None:
    repo, report, diff_result = repo_with_real_edit
    failing_test_result = {"returncode": 1, "stdout": "", "stderr": "1 failed"}
    result = evaluate_completion_gates(
        discovery=report,
        diff_stats=diff_result["stats"],
        diff_text=diff_result["diff"],
        test_result=failing_test_result,
    )
    assert result.passed is False
    assert result.gates["verification_passed"] is False
    assert "verification command exited non-zero" in result.reasons


def test_run_artifacts_excluded_from_changed_source_files() -> None:
    diff_stats = {
        "config.py": {"additions": 1, "deletions": 0},
        "aix-generated/abc123/plan.json": {"additions": 5, "deletions": 0},
        ".aix/cycles/cyc_1/proposed/objective.json": {"additions": 3, "deletions": 0},
        "execution-artifacts/run.log": {"additions": 2, "deletions": 0},
    }
    source, artifacts = split_changed_paths(diff_stats)
    assert source == ["config.py"]
    assert set(artifacts) == {
        "aix-generated/abc123/plan.json",
        ".aix/cycles/cyc_1/proposed/objective.json",
        "execution-artifacts/run.log",
    }
    assert all(any(p.startswith(prefix) for prefix in RUN_ARTIFACT_PREFIXES) for p in artifacts)


def test_zero_change_file_is_not_counted_as_a_meaningful_source_change(repo_with_real_edit) -> None:
    _repo, report, _diff = repo_with_real_edit
    diff_stats = {"config.py": {"additions": 0, "deletions": 0}}
    result = evaluate_completion_gates(
        discovery=report,
        diff_stats=diff_stats,
        diff_text="",
        test_result=None,
    )
    assert result.changed_source_files == []
    assert result.gates["source_changed"] is False


def test_secret_leak_in_diff_blocks_regardless_of_other_gates(repo_with_real_edit) -> None:
    repo, report, diff_result = repo_with_real_edit
    test_result = RepositoryToolbox(repository_root=repo).run_tests(["pytest", "-q"])
    result = evaluate_completion_gates(
        discovery=report,
        diff_stats=diff_result["stats"],
        diff_text=diff_result["diff"] + "\n+SMTP_PASSWORD = 'hunter2-real-secret'\n",
        test_result=test_result,
        secret_values=["hunter2-real-secret"],
    )
    assert result.passed is False
    assert result.gates["no_secrets_leaked"] is False
