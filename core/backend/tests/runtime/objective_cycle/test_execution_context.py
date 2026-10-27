"""AIX-UGEF-001 Task 3: ExecutionContextV1 propagates through the full chain."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch as mock_patch

from backend.aix_core.runtime.foundation.execution_context import (
    ExecutionContextV1,
    PrincipalV1,
    build_execution_context,
    SYSTEM_PRINCIPAL,
)
from backend.aix_core.runtime.foundation.policy_gateway import authorize_action
from backend.aix_core.runtime.foundation.capability_executor import execute_action
from backend.aix_core.runtime.objective.contracts import ActionRequestV1


def test_build_execution_context_defaults() -> None:
    ctx = build_execution_context()
    assert ctx.principal == SYSTEM_PRINCIPAL
    assert ctx.tenant_id == ""
    assert ctx.apply_edits is True
    assert ctx.approved is False


def test_build_execution_context_with_principal() -> None:
    principal = PrincipalV1(type="agent", id="aix-coder", role="executor")
    ctx = build_execution_context(
        principal=principal,
        tenant_id="t1",
        project_id="proj-a",
        repository_id="aix-frontend",
        cycle_id="cyc-1",
        step_id="step-1",
        attempt_id="att-1",
        allowed_files=("src/main.py",),
        apply_edits=True,
        approved=True,
    )
    assert ctx.principal.type == "agent"
    assert ctx.principal.id == "aix-coder"
    assert ctx.principal.role == "executor"
    assert ctx.tenant_id == "t1"
    assert ctx.project_id == "proj-a"
    assert ctx.cycle_id == "cyc-1"
    assert ctx.step_id == "step-1"
    assert ctx.attempt_id == "att-1"
    assert ctx.allowed_files == ("src/main.py",)
    assert ctx.approved is True


def test_execution_context_is_frozen() -> None:
    ctx = build_execution_context()
    try:
        ctx.tenant_id = "changed"  # type: ignore[misc]
        assert False, "should raise"
    except AttributeError:
        pass


def test_authorize_action_accepts_execution_context() -> None:
    """authorize_action threads execution_context without error."""
    ctx = build_execution_context(
        principal=PrincipalV1(type="agent", id="coder", role="executor"),
        tenant_id="t1",
        project_id="proj-a",
        repository_id="test-repo",
    )
    request = ActionRequestV1(
        capability="filesystem",
        operation="read",
        arguments={"path": "test_execution_context.py"},
    )
    repo = Path(__file__).resolve().parent
    decision = authorize_action(
        request,
        repository_id="test-repo",
        repository_root=repo,
        execution_context=ctx,
    )
    assert decision.allowed


def test_execute_action_accepts_execution_context() -> None:
    """execute_action threads execution_context without error."""
    ctx = build_execution_context(
        principal=PrincipalV1(type="agent", id="coder", role="executor"),
        repository_id="test-repo",
    )
    request = ActionRequestV1(
        capability="filesystem",
        operation="read",
        arguments={"path": "test_execution_context.py"},
    )
    repo = Path(__file__).resolve().parent
    with mock_patch(
        "backend.aix_core.runtime.foundation.capability_executor.resolve_repository_root",
        return_value=repo,
    ):
        obs = execute_action(
            request,
            repository_id="test-repo",
            execution_context=ctx,
        )
    assert obs.status == "SUCCESS"
