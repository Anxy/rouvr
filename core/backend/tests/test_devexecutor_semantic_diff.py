"""AIFIRST-3: semantic diff / patch engine tests.

Focused tests for apply_patch_with_outcome, classify_patch_failure,
and DriftGuardPolicy — the semantic diff capabilities introduced in AIFIRST-3.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.services.devexecutor_patch_engine_service import (
    DriftGuardPolicy,
    PatchApplyResult,
    apply_patch_with_outcome,
    classify_patch_failure,
)

PATCH_HELLO = """\
diff --git a/hello.txt b/hello.txt
index ce01362..94954ab 100644
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-hello
+hello world
"""


def _git_init(path: Path) -> None:
    subprocess.run(["git", "-C", str(path), "init"], check=True, capture_output=True)


# ---------------------------------------------------------------------------
# classify_patch_failure
# ---------------------------------------------------------------------------


def test_classify_patch_failure_not_git_repo() -> None:
    status, code, retryable = classify_patch_failure("fatal: not a git repository")
    assert status == "failed"
    assert code == "not_git_repo"
    assert retryable is False


def test_classify_patch_failure_corrupt_patch() -> None:
    status, code, retryable = classify_patch_failure("error: corrupt patch at line 4")
    assert status == "failed"
    assert code == "malformed_patch"
    assert retryable is False


def test_classify_patch_failure_hunk_conflict() -> None:
    status, code, retryable = classify_patch_failure("error: patch failed: src/main.py:10")
    assert status == "conflict"
    assert code == "hunk_conflict"
    assert retryable is True


def test_classify_patch_failure_path_missing_in_index() -> None:
    status, code, _ = classify_patch_failure("error: src/x.py: does not exist in index")
    assert status == "failed"
    assert code == "path_missing_in_index"


def test_classify_patch_failure_generic_fallback() -> None:
    status, code, retryable = classify_patch_failure("some unexpected error output")
    assert status == "failed"
    assert retryable is False


# ---------------------------------------------------------------------------
# DriftGuardPolicy — structure and defaults
# ---------------------------------------------------------------------------


def test_drift_guard_policy_default_limits() -> None:
    policy = DriftGuardPolicy()
    assert policy.max_files_changed == 25
    assert policy.max_diff_bytes == 500_000
    assert policy.blocked_path_prefixes == []


def test_drift_guard_blocks_sensitive_paths(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "secrets.env").write_text("TOKEN=abc\n", encoding="utf-8")
    patch = """\
diff --git a/secrets.env b/secrets.env
index 587be6b..98f0f84 100644
--- a/secrets.env
+++ b/secrets.env
@@ -1 +1 @@
-TOKEN=abc
+TOKEN=HACKED
"""
    policy = DriftGuardPolicy(blocked_path_prefixes=["secrets.env"])
    result = apply_patch_with_outcome(
        workspace_root=tmp_path,
        patch_text=patch,
        drift_policy=policy,
    )
    assert result.status == "failed"
    assert result.reason_code == "drift_guard_violation"
    assert any("BLOCKED_PATH" in v for v in result.policy_violations)


# ---------------------------------------------------------------------------
# apply_patch_with_outcome — happy path & error paths
# ---------------------------------------------------------------------------


def test_apply_patch_succeeds_and_returns_result(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "hello.txt").write_text("hello\n", encoding="utf-8")
    result = apply_patch_with_outcome(
        workspace_root=tmp_path,
        patch_text=PATCH_HELLO,
        pyright_runner=lambda **_kwargs: (True, ""),
    )
    assert isinstance(result, PatchApplyResult)
    assert result.status == "applied"
    assert result.reason_code == "ok"
    assert "hello.txt" in result.changed_files
    assert result.patch_sha1
    assert result.patch_bytes > 0
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hello world\n"


def test_apply_patch_returns_conflict_on_bad_hunk(tmp_path: Path) -> None:
    _git_init(tmp_path)
    bad_patch = "diff --git a/missing.py b/missing.py\n" "--- a/missing.py\n+++ b/missing.py\n@@ -1 +1 @@\n-old\n+new\n"
    result = apply_patch_with_outcome(workspace_root=tmp_path, patch_text=bad_patch)
    assert result.status in {"conflict", "failed"}
    assert result.reason_code in {
        "hunk_conflict_terminal",
        "apply_failed",
        "path_missing_in_index",
        "malformed_patch",
    }


def test_apply_patch_records_approx_diff_bytes(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "hello.txt").write_text("hello\n", encoding="utf-8")
    result = apply_patch_with_outcome(
        workspace_root=tmp_path,
        patch_text=PATCH_HELLO,
        pyright_runner=lambda **_kwargs: (True, ""),
    )
    assert result.approx_diff_bytes >= 0


def test_apply_patch_policy_hook_fires(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "hello.txt").write_text("hello\n", encoding="utf-8")
    events: list[str] = []

    def hook(evt: str, payload: dict) -> None:
        events.append(evt)

    apply_patch_with_outcome(
        workspace_root=tmp_path,
        patch_text=PATCH_HELLO,
        policy_hook=hook,
        pyright_runner=lambda **_kwargs: (True, ""),
    )
    assert any("patch" in e for e in events)
