"""AIX-UGEF-001 Task 1: filesystem.patch must receive an authorization decision
before any mutation reaches apply_patch_atomic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest

from backend.aix_core.runtime.foundation.policy_gateway import PolicyDecision, authorize_action
from backend.aix_core.runtime.objective.contracts import ActionRequestV1


def test_patch_authorization_allows_when_policy_permits() -> None:
    """filesystem.patch with valid scope returns ALLOW."""
    request = ActionRequestV1(
        capability="filesystem",
        operation="patch",
        arguments={"path": "src/main.py"},
    )
    repo = Path(__file__).resolve().parent
    decision = authorize_action(
        request,
        repository_id="test-repo",
        repository_root=repo,
        allowed_files=("src/main.py",),
        apply_edits=True,
    )
    assert decision.allowed
    assert decision.capability == "filesystem"
    assert decision.operation == "patch"


def test_patch_authorization_denied_when_apply_edits_false() -> None:
    """Patch must be denied when apply_edits is False (mutation not permitted)."""
    request = ActionRequestV1(
        capability="filesystem",
        operation="patch",
        arguments={"path": "src/main.py"},
    )
    repo = Path(__file__).resolve().parent
    decision = authorize_action(
        request,
        repository_id="test-repo",
        repository_root=repo,
        allowed_files=("src/main.py",),
        apply_edits=False,
    )
    assert not decision.allowed
    assert decision.code == "MUTATION_NOT_PERMITTED"


def test_patch_authorization_denied_when_path_outside_scope() -> None:
    """Patch targeting files outside allowed_files must be denied."""
    request = ActionRequestV1(
        capability="filesystem",
        operation="patch",
        arguments={"paths": ["secret/credentials.json"]},
    )
    repo = Path(__file__).resolve().parent
    decision = authorize_action(
        request,
        repository_id="test-repo",
        repository_root=repo,
        allowed_files=("src/main.py",),
        apply_edits=True,
    )
    assert not decision.allowed
    assert decision.code == "PATH_OUTSIDE_SCOPE"


def test_in_repo_absolute_path_is_confined_not_an_escape() -> None:
    repo = Path(__file__).resolve().parent
    request = ActionRequestV1(
        capability="filesystem",
        operation="inspect",
        arguments={"path": str(repo).replace("\\", "/")},
    )
    decision = authorize_action(
        request,
        repository_id="test-repo",
        repository_root=repo,
        apply_edits=False,
    )
    assert decision.allowed, decision.detail


def test_absolute_path_outside_repo_stays_path_outside_scope() -> None:
    repo = Path(__file__).resolve().parent
    request = ActionRequestV1(
        capability="filesystem",
        operation="inspect",
        arguments={"path": "C:/Windows/System32"},
    )
    decision = authorize_action(
        request,
        repository_id="test-repo",
        repository_root=repo,
        apply_edits=False,
    )
    assert not decision.allowed
    assert decision.code == "PATH_OUTSIDE_SCOPE"


def test_patch_authorization_denied_when_no_repository() -> None:
    """Patch with no repository root must be denied."""
    request = ActionRequestV1(
        capability="filesystem",
        operation="patch",
        arguments={"path": "src/main.py"},
    )
    decision = authorize_action(
        request,
        repository_id="",
        repository_root=None,
        apply_edits=True,
    )
    assert not decision.allowed
    assert decision.code == "REPOSITORY_NOT_AUTHORIZED"


def test_patch_authorization_denied_budget_exhausted() -> None:
    """Patch denied when mutation budget is zero."""
    request = ActionRequestV1(
        capability="filesystem",
        operation="patch",
        arguments={"path": "src/main.py"},
    )
    repo = Path(__file__).resolve().parent
    decision = authorize_action(
        request,
        repository_id="test-repo",
        repository_root=repo,
        allowed_files=("src/main.py",),
        apply_edits=True,
        mutation_budget_remaining=0,
    )
    assert not decision.allowed
    assert decision.code == "BUDGET_EXCEEDED"


def test_patch_executor_returns_deferred_ok() -> None:
    """When called directly through the executor, filesystem.patch returns
    authorized-deferred (not FAILED)."""
    from backend.aix_core.runtime.foundation.capability_executor import execute_action

    repo = Path(__file__).resolve().parent
    request = ActionRequestV1(
        capability="filesystem",
        operation="patch",
        arguments={"path": "test_patch_authorization.py"},
    )
    with mock_patch(
        "backend.aix_core.runtime.foundation.capability_executor.resolve_repository_root",
        return_value=repo,
    ):
        obs = execute_action(
            request,
            repository_id="test-repo",
            allowed_files=("test_patch_authorization.py",),
            apply_edits=True,
        )
    assert obs.status == "SUCCESS"
    assert obs.observation.get("deferred") is True
    assert obs.changed is False
