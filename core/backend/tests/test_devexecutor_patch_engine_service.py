from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.services.devexecutor_patch_engine_service import (
    DriftGuardPolicy,
    PatchAttemptBudget,
    apply_patch_with_outcome,
    build_drift_policy_from_repo_profile,
    classify_patch_failure,
)

pytestmark = pytest.mark.enable_pyright_lsp

PATCH_HELLO = """diff --git a/demo.txt b/demo.txt
index ce01362..94954ab 100644
--- a/demo.txt
+++ b/demo.txt
@@ -1 +1 @@
-hello
+hello world
"""


def test_patch_engine_applies_valid_patch(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    (tmp_path / "demo.txt").write_text("hello\n", encoding="utf-8")
    result = apply_patch_with_outcome(
        workspace_root=tmp_path,
        patch_text=PATCH_HELLO,
        semantic_validator=lambda **_kwargs: (True, ""),
        pyright_runner=lambda **_kwargs: (True, ""),
    )
    assert result.status == "applied"
    assert result.reason_code == "ok"
    assert "demo.txt" in result.changed_files
    assert result.patch_attempt == 1
    assert result.patch_bytes > 0
    assert result.patch_sha1
    assert (tmp_path / "demo.txt").read_text(encoding="utf-8") == "hello world\n"


def test_patch_engine_returns_conflict_or_failed_on_bad_patch(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    bad_patch = "diff --git a/nope.txt b/nope.txt\n--- a/nope.txt\n+++ b/nope.txt\n@@ -1 +1 @@\n-a\n+b\n"
    result = apply_patch_with_outcome(workspace_root=tmp_path, patch_text=bad_patch)
    assert result.status in {"conflict", "failed"}
    assert result.reason_code in {
        "hunk_conflict_terminal",
        "apply_failed",
        "path_missing_in_index",
        "malformed_patch",
    }
    assert result.patch_sha1
    assert result.retry_strategy in {"single_apply", "apply_then_3way"}


def test_drift_guard_blocks_disallowed_paths(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    (tmp_path / "secrets.env").write_text("x=1\n", encoding="utf-8")
    patch = """diff --git a/secrets.env b/secrets.env
index 587be6b..98f0f84 100644
--- a/secrets.env
+++ b/secrets.env
@@ -1 +1 @@
-x=1
+x=2
"""
    policy = DriftGuardPolicy(max_files_changed=5, max_diff_bytes=10_000, blocked_path_prefixes=["secrets.env"])
    result = apply_patch_with_outcome(workspace_root=tmp_path, patch_text=patch, drift_policy=policy)
    assert result.status == "failed"
    assert result.reason_code == "drift_guard_violation"
    assert any("BLOCKED_PATH" in item for item in result.policy_violations)


def test_drift_guard_blocks_oversized_diff(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    (tmp_path / "demo.txt").write_text("hello\n", encoding="utf-8")
    policy = DriftGuardPolicy(max_files_changed=5, max_diff_bytes=10, blocked_path_prefixes=[])
    result = apply_patch_with_outcome(workspace_root=tmp_path, patch_text=PATCH_HELLO, drift_policy=policy)
    assert result.status == "failed"
    assert result.reason_code == "drift_guard_violation"
    assert any("MAX_DIFF_BYTES_EXCEEDED" in item for item in result.policy_violations)


def test_patch_attempt_budget_exceeded_is_deterministic(tmp_path: Path) -> None:
    budget = PatchAttemptBudget(max_attempts=1, attempts_used=1)
    result = apply_patch_with_outcome(workspace_root=tmp_path, patch_text=PATCH_HELLO, patch_budget=budget)
    assert result.status == "failed"
    assert result.reason_code == "attempt_budget_exceeded"
    assert "PATCH_ATTEMPT_LIMIT_EXCEEDED" in result.stderr


def test_classify_patch_failure_taxonomy() -> None:
    assert classify_patch_failure("fatal: not a git repository")[1] == "not_git_repo"
    assert classify_patch_failure("error: corrupt patch at line 10")[1] == "malformed_patch"
    assert classify_patch_failure("error: nope.txt: does not exist in index")[1] == "path_missing_in_index"
    status, reason, retryable = classify_patch_failure("error: patch failed: a.txt:1")
    assert status == "conflict"
    assert reason == "hunk_conflict"
    assert retryable is True


def test_repo_profile_drift_policy_resolves_thresholds() -> None:
    profile = {
        "drift_guards": {
            "max_files_changed": 7,
            "max_diff_bytes": 2222,
            "blocked_path_prefixes": ["docs/private", "secrets"],
        }
    }
    policy = build_drift_policy_from_repo_profile(profile)
    assert policy.max_files_changed == 7
    assert policy.max_diff_bytes == 2222
    assert policy.blocked_path_prefixes == ["docs/private", "secrets"]


def test_patch_engine_can_resolve_policy_via_repo_profile_resolver(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    (tmp_path / "demo.txt").write_text("hello\n", encoding="utf-8")
    result = apply_patch_with_outcome(
        workspace_root=tmp_path,
        patch_text=PATCH_HELLO,
        repo_key="hyeve-core",
        explicit_profile_id="RP-HYEVE-PY-FASTAPI-CURRENT",
        profiles_dir="projects/AIX/hyeve-aix-service/backend/config/repo_profiles",
        semantic_validator=lambda **_kwargs: (True, ""),
        pyright_runner=lambda **_kwargs: (True, ""),
    )
    assert result.status == "applied"
    assert result.reason_code == "ok"


def test_patch_engine_retries_with_3way_after_conflict(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []

    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command: list[str], **_kwargs):
        commands.append(list(command))
        if len(commands) == 1:
            return FakeCompleted(1, stderr="error: patch failed: demo.txt:1")
        return FakeCompleted(0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = apply_patch_with_outcome(
        workspace_root=tmp_path,
        patch_text=PATCH_HELLO,
        max_conflict_retries=1,
        semantic_validator=lambda **_kwargs: (True, ""),
        pyright_runner=lambda **_kwargs: (True, ""),
    )

    assert result.status == "applied"
    assert result.reason_code == "ok_after_retry"
    assert result.retry_count == 1
    assert "--3way" not in commands[0]
    assert "--3way" in commands[1]
    assert "--ignore-space-change" in commands[0]
    assert "--ignore-whitespace" in commands[0]


def test_patch_engine_runs_pyright_and_keeps_valid_edit(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True, capture_output=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True, capture_output=True)
    demo = tmp_path / "demo.py"
    demo.write_text("value: int = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "demo.py"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], check=True, capture_output=True)
    patch = """diff --git a/demo.py b/demo.py
index 1405df6..f20d2fd 100644
--- a/demo.py
+++ b/demo.py
@@ -1 +1 @@
-value: int = 1
+value: int = 2
"""
    execution_context = {
        "ayrpack": {
            "spine": [],
        }
    }
    result = apply_patch_with_outcome(
        workspace_root=tmp_path,
        patch_text=patch,
        execution_context=execution_context,
        semantic_validator=lambda **_kwargs: (True, ""),
        pyright_runner=lambda **_kwargs: (True, '{"generalDiagnostics":[]}'),
    )

    assert result.status == "applied"
    assert result.semantic_precheck_passed is True
    assert result.pyright_passed is True
    assert result.post_apply_validation_scope == "touched_files"
    assert result.post_apply_validation_targets == ["demo.py"]
    assert len(result.post_apply_validation_results) == 1
    assert result.post_apply_validation_results[0].path == "demo.py"
    assert result.post_apply_validation_results[0].passed is True
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "value: int = 2\n"
    assert execution_context["ayrpack"]["spine"][-1]["kind"] == "attestation"
    assert execution_context["ayrpack"]["spine"][-1]["status"] == "passed"
    assert execution_context["ayrpack"]["spine"][-1]["details"]["touched_files"] == ["demo.py"]
    assert execution_context["ayrpack"]["spine"][-1]["details"]["validation_targets"] == ["demo.py"]
    assert execution_context["ayrpack"]["spine"][-1]["details"]["validation_results"][0]["path"] == "demo.py"


def test_patch_engine_reverts_edit_when_pyright_fails(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True, capture_output=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True, capture_output=True)
    demo = tmp_path / "demo.py"
    original = "value: int = 1\n"
    demo.write_text(original, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "demo.py"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], check=True, capture_output=True)
    patch = """diff --git a/demo.py b/demo.py
index 1405df6..ce9d27d 100644
--- a/demo.py
+++ b/demo.py
@@ -1 +1 @@
-value: int = 1
+value: int = "bad"
"""
    execution_context = {
        "ayrpack": {
            "spine": [],
        }
    }
    result = apply_patch_with_outcome(
        workspace_root=tmp_path,
        patch_text=patch,
        execution_context=execution_context,
        semantic_validator=lambda **_kwargs: (True, ""),
        pyright_runner=lambda **_kwargs: (
            False,
            '{"generalDiagnostics":[{"file":"demo.py","message":"Type \\"Literal[\'bad\']\\" is not assignable to declared type \\"int\\""}]}',
        ),
    )

    assert result.status == "failed"
    assert result.reason_code == "pyright_failed"
    assert result.pyright_passed is False
    assert result.post_apply_validation_scope == "touched_files"
    assert result.post_apply_validation_targets == ["demo.py"]
    assert len(result.post_apply_validation_results) == 1
    assert result.post_apply_validation_results[0].path == "demo.py"
    assert result.post_apply_validation_results[0].passed is False
    assert result.post_apply_validation_results[0].error_count == 1
    assert "not assignable" in result.stderr
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == original
    assert execution_context["ayrpack"]["spine"][-1]["kind"] == "attestation"
    assert execution_context["ayrpack"]["spine"][-1]["status"] == "failed"
    assert execution_context["ayrpack"]["spine"][-1]["details"]["touched_files"] == ["demo.py"]
    assert execution_context["ayrpack"]["spine"][-1]["details"]["validation_targets"] == ["demo.py"]
    assert execution_context["ayrpack"]["spine"][-1]["details"]["validation_results"][0]["error_count"] == 1


def test_patch_engine_rejects_preapply_semantic_failure_without_touching_disk(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    demo = tmp_path / "demo.py"
    original = "value: int = 1\n"
    demo.write_text(original, encoding="utf-8")
    patch = """diff --git a/demo.py b/demo.py
index 1405df6..ce9d27d 100644
--- a/demo.py
+++ b/demo.py
@@ -1 +1 @@
-value: int = 1
+value: int = "bad"
"""
    execution_context = {"ayrpack": {"spine": []}}

    result = apply_patch_with_outcome(
        workspace_root=tmp_path,
        patch_text=patch,
        execution_context=execution_context,
        semantic_validator=lambda **_kwargs: (False, "Type error on touched file"),
        pyright_runner=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("post-apply pyright should not run")),
    )

    assert result.status == "failed"
    assert result.reason_code == "pyright_precheck_failed"
    assert result.semantic_precheck_passed is False
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == original
    assert execution_context["ayrpack"]["spine"][-1]["kind"] == "attestation"
    assert execution_context["ayrpack"]["spine"][-1]["status"] == "failed"


def test_patch_engine_raises_if_semantic_reject_restore_leaves_mutated_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True, capture_output=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True, capture_output=True)
    demo = tmp_path / "demo.py"
    demo.write_text("value: int = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "demo.py"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], check=True, capture_output=True)
    patch = """diff --git a/demo.py b/demo.py
index 1405df6..ce9d27d 100644
--- a/demo.py
+++ b/demo.py
@@ -1 +1 @@
-value: int = 1
+value: int = "bad"
"""

    monkeypatch.setattr(
        "backend.services.devexecutor_patch_engine_service._restore_files_from_git",
        lambda **_kwargs: None,
    )

    with pytest.raises(AssertionError, match="SEMANTIC_REJECT_LEFT_MUTATED_BYTES"):
        apply_patch_with_outcome(
            workspace_root=tmp_path,
            patch_text=patch,
            semantic_validator=lambda **_kwargs: (True, ""),
            pyright_runner=lambda **_kwargs: (
                False,
                '{"generalDiagnostics":[{"file":"demo.py","message":"Type error"}]}',
            ),
        )
