"""AIX-UGEF-001 Task 5: durable audit events for capability execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest

from backend.aix_core.runtime.foundation.audit import clear_audit_log, get_audit_log
from backend.aix_core.runtime.foundation.execution_context import PrincipalV1, build_execution_context
from backend.aix_core.runtime.foundation.capability_executor import execute_action, execute_action_loop
from backend.aix_core.runtime.objective.contracts import ActionRequestV1


REPO_ROOT = Path(__file__).resolve().parent


@pytest.fixture(autouse=True)
def _clean_audit():
    clear_audit_log()
    yield
    clear_audit_log()


def _ctx(**kwargs):
    defaults = dict(
        principal=PrincipalV1(type="agent", id="coder", role="executor"),
        repository_id="test-repo",
        cycle_id="cyc-1",
        step_id="step-1",
        attempt_id="att-1",
    )
    defaults.update(kwargs)
    return build_execution_context(**defaults)


def test_successful_read_produces_full_audit_chain() -> None:
    ctx = _ctx()
    request = ActionRequestV1(
        capability="filesystem",
        operation="read",
        arguments={"path": "test_audit_events.py"},
    )
    with mock_patch(
        "backend.aix_core.runtime.foundation.capability_executor.resolve_repository_root",
        return_value=REPO_ROOT,
    ):
        obs = execute_action(
            request,
            repository_id="test-repo",
            execution_context=ctx,
        )
    assert obs.status == "SUCCESS"

    log = get_audit_log()
    types = [e["event_type"] for e in log]
    assert "capability.requested" in types
    assert "capability.authorized" in types
    assert "capability.execution_started" in types
    assert "capability.execution_completed" in types

    requested = next(e for e in log if e["event_type"] == "capability.requested")
    assert requested["action_id"] == request.action_id
    assert requested["capability"] == "filesystem"
    assert requested["operation"] == "read"
    assert requested["principal"]["id"] == "coder"
    assert requested["cycle_id"] == "cyc-1"
    assert requested["step_id"] == "step-1"

    authorized = next(e for e in log if e["event_type"] == "capability.authorized")
    assert authorized["decision"] == "ALLOW"

    completed = next(e for e in log if e["event_type"] == "capability.execution_completed")
    assert completed["status"] == "SUCCESS"
    assert completed["action_id"] == request.action_id


def test_denied_action_produces_audit_trail() -> None:
    ctx = _ctx()
    request = ActionRequestV1(
        capability="database",
        operation="query",
        arguments={"sql": "SELECT 1"},
    )
    with mock_patch(
        "backend.aix_core.runtime.foundation.capability_executor.resolve_repository_root",
        return_value=REPO_ROOT,
    ):
        obs = execute_action(
            request,
            repository_id="test-repo",
            execution_context=ctx,
        )
    assert obs.status == "FAILED"

    log = get_audit_log()
    types = [e["event_type"] for e in log]
    assert "capability.requested" in types
    assert "capability.denied" in types


def test_delete_confirmation_required_produces_audit() -> None:
    ctx = _ctx(approved=False)
    request = ActionRequestV1(
        capability="filesystem",
        operation="delete",
        arguments={"path": "test_audit_events.py"},
    )
    with mock_patch(
        "backend.aix_core.runtime.foundation.capability_executor.resolve_repository_root",
        return_value=REPO_ROOT,
    ):
        obs = execute_action(
            request,
            repository_id="test-repo",
            allowed_files=("test_audit_events.py",),
            execution_context=ctx,
        )
    assert obs.status == "BLOCKED"

    log = get_audit_log()
    types = [e["event_type"] for e in log]
    assert "capability.confirmation_required" in types


def test_execute_action_loop_produces_correlated_events() -> None:
    ctx = _ctx()
    requests = [
        ActionRequestV1(
            capability="filesystem",
            operation="read",
            arguments={"path": "test_audit_events.py"},
        ),
        ActionRequestV1(
            capability="filesystem",
            operation="exists",
            arguments={"path": "test_audit_events.py"},
        ),
    ]
    with mock_patch(
        "backend.aix_core.runtime.foundation.capability_executor.resolve_repository_root",
        return_value=REPO_ROOT,
    ):
        observations = execute_action_loop(
            requests,
            repository_id="test-repo",
            execution_context=ctx,
        )
    assert all(o.status == "SUCCESS" for o in observations)

    log = get_audit_log()
    requested = [e for e in log if e["event_type"] == "capability.requested"]
    completed = [e for e in log if e["event_type"] == "capability.execution_completed"]
    assert len(requested) == 2
    assert len(completed) == 2
    assert requested[0]["action_id"] != requested[1]["action_id"]
