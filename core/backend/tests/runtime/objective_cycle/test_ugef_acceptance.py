"""AIX-UGEF-001 FINAL ACCEPTANCE: end-to-end governed execution chain.

Every test proves a specific acceptance criterion from the sprint definition.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest

from backend.aix_core.runtime.foundation.audit import clear_audit_log, get_audit_log
from backend.aix_core.runtime.foundation.execution_context import PrincipalV1, build_execution_context
from backend.aix_core.runtime.foundation.capability_executor import execute_action, execute_action_loop
from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
from backend.aix_core.runtime.foundation.policy_gateway import authorize_action
from backend.aix_core.runtime.foundation.policy_store import (
    PolicyRuleV1,
    get_policy_store,
    reset_policy_store,
)
from backend.aix_core.runtime.objective.contracts import ActionRequestV1


REPO_ROOT = Path(__file__).resolve().parent


@pytest.fixture(autouse=True)
def _clean():
    clear_audit_log()
    reset_policy_store()
    yield
    clear_audit_log()
    reset_policy_store()


def _mock_root():
    return mock_patch(
        "backend.aix_core.runtime.foundation.capability_executor.resolve_repository_root",
        return_value=REPO_ROOT,
    )


# ---- FULL CHAIN: principal → request → resolution → authorization → execution → audit ----

def test_full_governed_chain() -> None:
    """Demonstrate: principal → ActionRequest → capability resolution →
    authorization decision → deterministic execution → ActionObservation →
    durable audit event."""
    ctx = build_execution_context(
        principal=PrincipalV1(type="agent", id="aix-coder", role="executor"),
        tenant_id="tenant-1",
        project_id="proj-alpha",
        repository_id="test-repo",
        cycle_id="cyc-001",
        step_id="step-1",
        attempt_id="att-1",
        approved=True,
    )
    request = ActionRequestV1(
        capability="filesystem",
        operation="exists",
        arguments={"path": "test_ugef_acceptance.py"},
    )
    resolved = resolve_capability("filesystem", "exists")
    assert resolved is not None
    cap, op, spec = resolved
    assert cap == "filesystem"
    assert op == "exists"
    assert not spec.mutates

    with _mock_root():
        obs = execute_action(
            request,
            repository_id="test-repo",
            execution_context=ctx,
        )
    assert obs.status == "SUCCESS"
    assert obs.observation["exists"] is True

    log = get_audit_log()
    types = [e["event_type"] for e in log]
    assert "capability.requested" in types
    assert "capability.authorized" in types
    assert "capability.execution_started" in types
    assert "capability.execution_completed" in types

    req_event = next(e for e in log if e["event_type"] == "capability.requested")
    assert req_event["principal"]["id"] == "aix-coder"
    assert req_event["cycle_id"] == "cyc-001"
    assert req_event["step_id"] == "step-1"
    assert req_event["attempt_id"] == "att-1"


# ---- Allowed mkdir succeeds ----

def test_allowed_mkdir_succeeds() -> None:
    target = REPO_ROOT / "_ugef_test_mkdir_dir"
    marker = target / ".gitkeep"
    try:
        ctx = build_execution_context(
            principal=PrincipalV1(type="agent", id="coder", role="executor"),
            repository_id="test-repo",
            approved=True,
        )
        request = ActionRequestV1(
            capability="filesystem",
            operation="mkdir",
            arguments={"path": "_ugef_test_mkdir_dir", "marker": True},
        )
        with _mock_root():
            obs = execute_action(request, repository_id="test-repo", execution_context=ctx)
        assert obs.status == "SUCCESS"
        assert obs.changed is True
        assert target.is_dir()
    finally:
        if marker.exists():
            marker.unlink()
        if target.exists():
            target.rmdir()


# ---- Denied mkdir does nothing ----

def test_denied_mkdir_does_nothing() -> None:
    store = get_policy_store()
    store.add_rule(PolicyRuleV1(
        id="deny-mkdir",
        capability="filesystem",
        operation="mkdir",
        decision="DENY",
        project_id="locked-project",
        reason="locked project denies mkdir",
    ))
    ctx = build_execution_context(
        principal=PrincipalV1(type="agent", id="coder", role="executor"),
        project_id="locked-project",
        repository_id="test-repo",
        approved=True,
    )
    request = ActionRequestV1(
        capability="filesystem",
        operation="mkdir",
        arguments={"path": "_should_not_exist"},
    )
    with _mock_root():
        obs = execute_action(request, repository_id="test-repo", execution_context=ctx)
    assert obs.status != "SUCCESS"
    assert not (REPO_ROOT / "_should_not_exist").exists()


# ---- Patch uses the same authorization boundary ----

def test_patch_uses_authorization_boundary() -> None:
    request = ActionRequestV1(
        capability="filesystem",
        operation="patch",
        arguments={"path": "test_ugef_acceptance.py"},
    )
    decision = authorize_action(
        request,
        repository_id="test-repo",
        repository_root=REPO_ROOT,
        allowed_files=("test_ugef_acceptance.py",),
        apply_edits=True,
    )
    assert decision.allowed
    assert decision.capability == "filesystem"
    assert decision.operation == "patch"


# ---- Delete cannot execute without required approval ----

def test_delete_blocked_without_approval() -> None:
    ctx = build_execution_context(
        principal=PrincipalV1(type="agent", id="coder", role="executor"),
        repository_id="test-repo",
        approved=False,
    )
    request = ActionRequestV1(
        capability="filesystem",
        operation="delete",
        arguments={"path": "test_ugef_acceptance.py"},
    )
    with _mock_root():
        obs = execute_action(
            request,
            repository_id="test-repo",
            allowed_files=("test_ugef_acceptance.py",),
            execution_context=ctx,
        )
    assert obs.status == "BLOCKED"
    assert obs.error_code == "APPROVAL_REQUIRED"
    assert obs.changed is False
    assert REPO_ROOT.joinpath("test_ugef_acceptance.py").exists()


# ---- Planner cannot patch while Coder can ----

def test_planner_denied_patch_coder_allowed() -> None:
    store = get_policy_store()
    store.add_rule(PolicyRuleV1(
        id="planner-no-patch",
        capability="filesystem",
        operation="patch",
        decision="DENY",
        principal_role="planner",
        reason="Planner cannot mutate",
    ))

    planner_ctx = build_execution_context(
        principal=PrincipalV1(type="agent", id="planner", role="planner"),
        repository_id="test-repo",
    )
    coder_ctx = build_execution_context(
        principal=PrincipalV1(type="agent", id="coder", role="executor"),
        repository_id="test-repo",
    )
    request = ActionRequestV1(
        capability="filesystem",
        operation="patch",
        arguments={"path": "test_ugef_acceptance.py"},
    )
    with _mock_root():
        planner_obs = execute_action(
            request, repository_id="test-repo",
            allowed_files=("test_ugef_acceptance.py",),
            execution_context=planner_ctx,
        )
        coder_obs = execute_action(
            request, repository_id="test-repo",
            allowed_files=("test_ugef_acceptance.py",),
            execution_context=coder_ctx,
        )
    assert planner_obs.status != "SUCCESS"
    assert planner_obs.error_code == "POLICY_DENIED"
    assert coder_obs.status == "SUCCESS"


# ---- Unknown capability cannot fall back to legacy mutation ----

def test_unknown_capability_fails_closed() -> None:
    ctx = build_execution_context(
        principal=PrincipalV1(type="agent", id="coder", role="executor"),
        repository_id="test-repo",
    )
    request = ActionRequestV1(
        capability="database",
        operation="query",
        arguments={"sql": "DROP TABLE users"},
    )
    with _mock_root():
        obs = execute_action(request, repository_id="test-repo", execution_context=ctx)
    assert obs.status == "FAILED"
    assert obs.error_code == "CAPABILITY_NOT_AVAILABLE"
    assert obs.changed is False

    log = get_audit_log()
    denied = [e for e in log if e["event_type"] == "capability.denied"]
    assert len(denied) == 1


# ---- Existing complex multi-file read regression ----

def test_multi_action_read_regression() -> None:
    ctx = build_execution_context(
        principal=PrincipalV1(type="agent", id="coder", role="executor"),
        repository_id="test-repo",
        approved=True,
    )
    requests = [
        ActionRequestV1(
            capability="filesystem",
            operation="read",
            arguments={"path": "test_ugef_acceptance.py"},
        ),
        ActionRequestV1(
            capability="filesystem",
            operation="exists",
            arguments={"path": "test_ugef_acceptance.py"},
        ),
        ActionRequestV1(
            capability="filesystem",
            operation="inspect",
            arguments={"path": "."},
        ),
    ]
    with _mock_root():
        observations = execute_action_loop(
            requests,
            repository_id="test-repo",
            execution_context=ctx,
        )
    assert len(observations) == 3
    assert all(o.status == "SUCCESS" for o in observations)
