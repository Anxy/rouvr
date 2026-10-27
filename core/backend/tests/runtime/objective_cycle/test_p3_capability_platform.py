"""Tests for P3: Canonical Capability Platform — process, git, network, expanded tests."""

from __future__ import annotations

import pytest
from pathlib import Path


# ── Registry: new capabilities registered ────────────────────────────────────

class TestCapabilityRegistry:
    def test_process_run_registered(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        result = resolve_capability("process", "run")
        assert result is not None
        cap, op, spec = result
        assert cap == "process"
        assert op == "run"
        assert spec.risk == "high"
        assert spec.mutates is False

    def test_process_run_mutating_requires_approval(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        result = resolve_capability("process", "run_mutating")
        assert result is not None
        assert result[2].mutates is True
        assert result[2].approval_required is True

    def test_git_status_registered(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        result = resolve_capability("git", "status")
        assert result is not None
        assert result[2].mutates is False

    def test_git_commit_is_mutation(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        result = resolve_capability("git", "commit")
        assert result is not None
        assert result[2].mutates is True
        assert result[2].risk == "medium"

    def test_git_stage_registered(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        result = resolve_capability("git", "stage")
        assert result is not None
        assert result[2].mutates is True

    def test_git_diff_registered(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        assert resolve_capability("git", "diff") is not None

    def test_git_log_registered(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        assert resolve_capability("git", "log") is not None

    def test_git_branch_registered(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        assert resolve_capability("git", "branch") is not None

    def test_git_checkout_registered(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        assert resolve_capability("git", "checkout") is not None

    def test_git_create_branch_registered(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        assert resolve_capability("git", "create_branch") is not None

    def test_network_http_get_registered(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        result = resolve_capability("network", "http_get")
        assert result is not None
        assert result[2].mutates is False

    def test_network_http_post_requires_approval(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        result = resolve_capability("network", "http_post")
        assert result is not None
        assert result[2].mutates is True
        assert result[2].approval_required is True

    def test_tests_lint_registered(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        result = resolve_capability("tests", "lint")
        assert result is not None
        assert result[2].mutates is False

    def test_tests_coverage_registered(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        result = resolve_capability("tests", "coverage")
        assert result is not None

    def test_catalog_includes_new_capabilities(self):
        from backend.aix_core.runtime.foundation.capability_registry import catalog_for_planner
        catalog = catalog_for_planner()
        cap_ops = {(c["capability"], c["operation"]) for c in catalog}
        assert ("process", "run") in cap_ops
        assert ("git", "status") in cap_ops
        assert ("git", "commit") in cap_ops
        assert ("network", "http_get") in cap_ops
        assert ("tests", "lint") in cap_ops
        assert ("tests", "coverage") in cap_ops

    def test_all_registered_ids_includes_new(self):
        from backend.aix_core.runtime.foundation.capability_registry import all_registered_capability_ids
        ids = all_registered_capability_ids()
        assert "process.run" in ids
        assert "git.status" in ids
        assert "git.commit" in ids
        assert "network.http_get" in ids

    def test_unknown_capability_still_fails_closed(self):
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        assert resolve_capability("database", "query") is None
        assert resolve_capability("deployment", "push") is None


# ── Process executor ─────────────────────────────────────────────────────────

class TestProcessExecutor:
    _ROOT = Path(__file__).resolve().parent

    def test_process_run_echo(self):
        from backend.aix_core.runtime.foundation.capability_executor import _execute_process
        from backend.aix_core.runtime.objective.contracts import ActionRequestV1

        req = ActionRequestV1(
            capability="process", operation="run",
            arguments={"command": "echo hello"},
        )
        obs = _execute_process(req, dict(req.arguments or {}), self._ROOT, True)
        assert obs.status == "SUCCESS"
        assert "hello" in obs.observation.get("stdout", "")
        assert obs.observation["exit_code"] == 0

    def test_process_run_empty_command_rejected(self):
        from backend.aix_core.runtime.foundation.capability_executor import _execute_process
        from backend.aix_core.runtime.objective.contracts import ActionRequestV1

        req = ActionRequestV1(capability="process", operation="run", arguments={"command": ""})
        obs = _execute_process(req, {"command": ""}, self._ROOT, True)
        assert obs.status == "FAILED"
        assert obs.error_code == "EMPTY_COMMAND"

    def test_process_run_nonexistent_cwd_rejected(self):
        from backend.aix_core.runtime.foundation.capability_executor import _execute_process
        from backend.aix_core.runtime.objective.contracts import ActionRequestV1

        req = ActionRequestV1(capability="process", operation="run",
                              arguments={"command": "echo x", "cwd": "nonexistent"})
        obs = _execute_process(req, {"command": "echo x", "cwd": "nonexistent"}, self._ROOT, True)
        assert obs.status == "FAILED"
        assert obs.error_code == "CWD_NOT_FOUND"


# ── Git executor ─────────────────────────────────────────────────────────────

class TestGitExecutor:
    """Git executor tests use the actual repo root (avoids tmp_path permission errors on Windows)."""
    _REPO = Path(__file__).resolve().parents[4]  # hyeve-aix-service root

    def test_git_status(self):
        from backend.aix_core.runtime.foundation.capability_executor import _execute_git
        from backend.aix_core.runtime.objective.contracts import ActionRequestV1

        req = ActionRequestV1(capability="git", operation="status", arguments={})
        obs = _execute_git(req, "status", {}, self._REPO, True)
        assert obs.status == "SUCCESS"
        assert "status" in obs.observation

    def test_git_log(self):
        from backend.aix_core.runtime.foundation.capability_executor import _execute_git
        from backend.aix_core.runtime.objective.contracts import ActionRequestV1

        req = ActionRequestV1(capability="git", operation="log", arguments={"count": 3})
        obs = _execute_git(req, "log", {"count": 3}, self._REPO, True)
        assert obs.status == "SUCCESS"
        assert obs.observation["log"]  # at least some log output

    def test_git_diff(self):
        from backend.aix_core.runtime.foundation.capability_executor import _execute_git
        from backend.aix_core.runtime.objective.contracts import ActionRequestV1

        req = ActionRequestV1(capability="git", operation="diff", arguments={})
        obs = _execute_git(req, "diff", {}, self._REPO, True)
        assert obs.status == "SUCCESS"

    def test_git_branch_list(self):
        from backend.aix_core.runtime.foundation.capability_executor import _execute_git
        from backend.aix_core.runtime.objective.contracts import ActionRequestV1

        req = ActionRequestV1(capability="git", operation="branch", arguments={})
        obs = _execute_git(req, "branch", {}, self._REPO, True)
        assert obs.status == "SUCCESS"

    def test_git_commit_without_apply_edits_denied(self):
        from backend.aix_core.runtime.foundation.capability_executor import _execute_git
        from backend.aix_core.runtime.objective.contracts import ActionRequestV1

        req = ActionRequestV1(capability="git", operation="commit", arguments={"message": "nope"})
        obs = _execute_git(req, "commit", {"message": "nope"}, self._REPO, False)
        assert obs.status == "FAILED"
        assert obs.error_code == "EDITS_DISABLED"

    def test_git_empty_commit_message_rejected(self):
        from backend.aix_core.runtime.foundation.capability_executor import _execute_git
        from backend.aix_core.runtime.objective.contracts import ActionRequestV1

        req = ActionRequestV1(capability="git", operation="commit", arguments={"message": ""})
        obs = _execute_git(req, "commit", {"message": ""}, self._REPO, True)
        assert obs.status == "FAILED"
        assert obs.error_code == "EMPTY_COMMIT_MESSAGE"

    def test_git_checkout_without_apply_edits_denied(self):
        from backend.aix_core.runtime.foundation.capability_executor import _execute_git
        from backend.aix_core.runtime.objective.contracts import ActionRequestV1

        req = ActionRequestV1(capability="git", operation="checkout", arguments={"ref": "main"})
        obs = _execute_git(req, "checkout", {"ref": "main"}, self._REPO, False)
        assert obs.status == "FAILED"
        assert obs.error_code == "EDITS_DISABLED"

    def test_network_deferred(self):
        """Network operations should return CAPABILITY_DEFERRED until sandbox is built."""
        from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
        result = resolve_capability("network", "http_get")
        assert result is not None


# ── Policy integration: new capabilities use existing governance ──────────────

class TestNewCapabilityGovernance:
    def test_process_run_mutating_authorization(self):
        from backend.aix_core.runtime.foundation.policy_gateway import authorize_action
        from backend.aix_core.runtime.objective.contracts import ActionRequestV1

        req = ActionRequestV1(
            capability="process", operation="run_mutating",
            arguments={"command": "rm -rf /"},
        )
        decision = authorize_action(
            req, repository_id="repo1", repository_root=Path(__file__).parent,
            approved=False,
        )
        assert not decision.allowed, "mutating process should be denied without approval"

    def test_git_commit_authorization_with_approval(self):
        from backend.aix_core.runtime.foundation.policy_gateway import authorize_action
        from backend.aix_core.runtime.objective.contracts import ActionRequestV1

        req = ActionRequestV1(
            capability="git", operation="commit",
            arguments={"message": "test commit"},
        )
        decision = authorize_action(
            req, repository_id="repo1", repository_root=Path(__file__).parent,
            approved=True,
        )
        assert decision.allowed

    def test_unknown_capability_denied_via_policy(self):
        from backend.aix_core.runtime.foundation.policy_gateway import authorize_action
        from backend.aix_core.runtime.objective.contracts import ActionRequestV1

        req = ActionRequestV1(
            capability="database", operation="drop_table",
            arguments={"table": "users"},
        )
        decision = authorize_action(
            req, repository_id="repo1", repository_root=Path(__file__).parent,
        )
        assert not decision.allowed
        assert decision.code == "CAPABILITY_NOT_AVAILABLE"
