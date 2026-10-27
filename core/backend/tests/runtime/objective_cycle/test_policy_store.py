"""AIX-UGEF-001 Task 4: Policy Store V0 with all required scenarios."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest

from backend.aix_core.runtime.foundation.execution_context import (
    ExecutionContextV1,
    PrincipalV1,
    build_execution_context,
)
from backend.aix_core.runtime.foundation.policy_gateway import PolicyDecision, authorize_action
from backend.aix_core.runtime.foundation.policy_store import (
    PolicyRuleV1,
    PolicyStore,
    get_policy_store,
    reset_policy_store,
)
from backend.aix_core.runtime.foundation.capability_executor import execute_action
from backend.aix_core.runtime.objective.contracts import ActionRequestV1


@pytest.fixture(autouse=True)
def _clean_policy_store():
    reset_policy_store()
    yield
    reset_policy_store()


REPO_ROOT = Path(__file__).resolve().parent


def _ctx(
    *,
    role: str = "executor",
    principal_id: str = "coder",
    project_id: str = "",
    approved: bool = False,
    session_grants: tuple[str, ...] = (),
) -> ExecutionContextV1:
    return build_execution_context(
        principal=PrincipalV1(type="agent", id=principal_id, role=role),
        project_id=project_id,
        repository_id="test-repo",
        approved=approved,
        session_grants=session_grants,
    )


def _decide(
    capability: str,
    operation: str,
    ctx: ExecutionContextV1,
    arguments: dict | None = None,
    allowed_files: tuple[str, ...] = (),
) -> PolicyDecision:
    request = ActionRequestV1(
        capability=capability,
        operation=operation,
        arguments=arguments or {"path": "."},
    )
    return authorize_action(
        request,
        repository_id="test-repo",
        repository_root=REPO_ROOT,
        allowed_files=allowed_files,
        apply_edits=True,
        execution_context=ctx,
    )


# ---- Scenario: Project A mkdir ALLOW / Project B DENY ----

def test_project_a_allows_mkdir_project_b_denies() -> None:
    store = get_policy_store()
    store.add_rule(PolicyRuleV1(
        id="proj-a-allow-mkdir",
        capability="filesystem",
        operation="mkdir",
        decision="ALLOW",
        project_id="proj-a",
        reason="Project A allows mkdir",
    ))
    store.add_rule(PolicyRuleV1(
        id="proj-b-deny-mkdir",
        capability="filesystem",
        operation="mkdir",
        decision="DENY",
        project_id="proj-b",
        reason="Project B denies mkdir",
    ))

    ctx_a = _ctx(project_id="proj-a")
    ctx_b = _ctx(project_id="proj-b")

    decision_a = _decide("filesystem", "mkdir", ctx_a, {"path": "new_dir"})
    assert decision_a.allowed

    decision_b = _decide("filesystem", "mkdir", ctx_b, {"path": "new_dir"})
    assert not decision_b.allowed
    assert decision_b.code == "POLICY_DENIED"
    assert "Project B" in (decision_b.detail or "")


# ---- Scenario: Planner DENY patch / Coder ALLOW patch ----

def test_planner_cannot_patch_coder_can() -> None:
    store = get_policy_store()
    store.add_rule(PolicyRuleV1(
        id="planner-deny-patch",
        capability="filesystem",
        operation="patch",
        decision="DENY",
        principal_role="planner",
        reason="Planner cannot mutate files",
    ))

    ctx_planner = _ctx(role="planner")
    ctx_coder = _ctx(role="executor")

    decision_planner = _decide("filesystem", "patch", ctx_planner)
    assert not decision_planner.allowed
    assert decision_planner.code == "POLICY_DENIED"
    assert "Planner" in (decision_planner.detail or "")

    decision_coder = _decide("filesystem", "patch", ctx_coder)
    assert decision_coder.allowed


# ---- Scenario: delete requires CONFIRM ----

def test_delete_requires_confirmation_without_approval() -> None:
    """Delete with approval_required on the registry spec must be denied
    when execution_context.approved is False."""
    ctx = _ctx(approved=False)
    decision = _decide("filesystem", "delete", ctx, {"path": "test_policy_store.py"},
                        allowed_files=("test_policy_store.py",))
    assert not decision.allowed
    assert decision.code == "APPROVAL_REQUIRED"


def test_delete_zero_mutation_before_approval() -> None:
    """Denied delete must cause zero filesystem changes."""
    ctx = _ctx(approved=False)
    request = ActionRequestV1(
        capability="filesystem",
        operation="delete",
        arguments={"path": "test_policy_store.py"},
    )
    with mock_patch(
        "backend.aix_core.runtime.foundation.capability_executor.resolve_repository_root",
        return_value=REPO_ROOT,
    ):
        obs = execute_action(
            request,
            repository_id="test-repo",
            allowed_files=("test_policy_store.py",),
            apply_edits=True,
            execution_context=ctx,
        )
    assert obs.status == "BLOCKED"
    assert obs.changed is False
    assert obs.error_code == "APPROVAL_REQUIRED"
    assert REPO_ROOT.joinpath("test_policy_store.py").exists(), "file must still exist"


def test_delete_succeeds_when_approved() -> None:
    """Approved bounded delete must succeed."""
    target = REPO_ROOT / "_test_delete_target.txt"
    target.write_text("delete-me", encoding="utf-8")
    try:
        ctx = _ctx(approved=True)
        request = ActionRequestV1(
            capability="filesystem",
            operation="delete",
            arguments={"path": "_test_delete_target.txt"},
        )
        with mock_patch(
            "backend.aix_core.runtime.foundation.capability_executor.resolve_repository_root",
            return_value=REPO_ROOT,
        ):
            obs = execute_action(
                request,
                repository_id="test-repo",
                allowed_files=("_test_delete_target.txt",),
                apply_edits=True,
                execution_context=ctx,
            )
        assert obs.status == "SUCCESS"
        assert obs.changed is True
        assert not target.exists()
    finally:
        if target.exists():
            target.unlink()


# ---- Scenario: unknown capability DENY ----

def test_unknown_capability_denied() -> None:
    ctx = _ctx()
    request = ActionRequestV1(
        capability="database",
        operation="query",
        arguments={"sql": "SELECT 1"},
    )
    decision = authorize_action(
        request,
        repository_id="test-repo",
        repository_root=REPO_ROOT,
        execution_context=ctx,
    )
    assert not decision.allowed
    assert decision.code == "CAPABILITY_NOT_AVAILABLE"


# ---- Scenario: path outside allowed_files DENY ----

def test_path_outside_allowed_files_denied() -> None:
    ctx = _ctx()
    decision = _decide(
        "filesystem", "read", ctx,
        {"path": "secret/credentials.json"},
        allowed_files=("src/main.py",),
    )
    assert not decision.allowed
    assert decision.code == "PATH_OUTSIDE_SCOPE"


# ---- Scenario: session grant overrides project deny ----

def test_session_grant_overrides_project_deny() -> None:
    store = get_policy_store()
    store.add_rule(PolicyRuleV1(
        id="proj-deny-delete",
        capability="filesystem",
        operation="delete",
        decision="DENY",
        project_id="proj-strict",
        reason="Project denies delete by default",
    ))
    store.add_rule(PolicyRuleV1(
        id="session-allow-delete",
        capability="filesystem",
        operation="delete",
        decision="ALLOW",
        session_grant="temp-delete-grant",
        reason="Temporary session grant for delete",
    ))

    ctx_no_grant = _ctx(project_id="proj-strict", approved=True)
    decision_no = _decide("filesystem", "delete", ctx_no_grant,
                           {"path": "test_policy_store.py"},
                           allowed_files=("test_policy_store.py",))
    assert not decision_no.allowed

    ctx_granted = _ctx(
        project_id="proj-strict",
        approved=True,
        session_grants=("temp-delete-grant",),
    )
    decision_yes = _decide("filesystem", "delete", ctx_granted,
                            {"path": "test_policy_store.py"},
                            allowed_files=("test_policy_store.py",))
    assert decision_yes.allowed


# ---- Policy store unit tests ----

def test_policy_store_deny_at_any_level_blocks() -> None:
    store = PolicyStore()
    store.add_rule(PolicyRuleV1(
        id="system-allow",
        capability="filesystem",
        operation="mkdir",
        decision="ALLOW",
    ))
    store.add_rule(PolicyRuleV1(
        id="proj-deny",
        capability="filesystem",
        operation="mkdir",
        decision="DENY",
        project_id="locked",
    ))
    decision, rule = store.evaluate(
        capability="filesystem",
        operation="mkdir",
        project_id="locked",
    )
    assert decision == "DENY"
    assert rule is not None
    assert rule.id == "proj-deny"


def test_policy_store_no_rules_allows() -> None:
    store = PolicyStore()
    decision, rule = store.evaluate(capability="filesystem", operation="read")
    assert decision == "ALLOW"
    assert rule is None
