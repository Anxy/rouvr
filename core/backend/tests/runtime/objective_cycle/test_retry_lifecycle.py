"""AIX gap-closure sprint 3, Priority 4: real retry lifecycle.

BLOCKED -> RESOLUTION_PENDING -> READY_TO_RETRY -> RETRYING -> RUNNING_STEP, driven
through the real `DurableObjectiveCycleApiBackend.command()` path. Retry never creates
a new objective/cycle -- the same `_ObjectiveRecord`/`_CycleRecord` is reused, so
execution_context, repository binding, and prior evidence persist automatically.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.tests.runtime.objective_cycle.plan_bootstrap import seed_accepted_bootstrap_contract


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture
def edit_repo_with_broken_tests(tmp_path: Path) -> Path:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "config.py").write_text(
        "class Settings:\n    VALUE_HOST: str = ''\n    VALUE_PORT: int = 587\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / ".env").write_text("VALUE_HOST=example.com\nVALUE_PORT=587\n", encoding="utf-8", newline="\n")
    (tmp_path / ".env.example").write_text("VALUE_HOST=\nVALUE_PORT=587\n", encoding="utf-8", newline="\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'svc'\n", encoding="utf-8", newline="\n")
    (tmp_path / "tests").mkdir()
    # Deliberately broken -- the real patch applies cleanly but real completion gates
    # (registered tests) fail, raising COMPLETION_GATES_FAILED -> BLOCKED.
    (tmp_path / "tests" / "test_smoke.py").write_text(
        "def test_smoke():\n    assert False, 'intentionally broken'\n", encoding="utf-8", newline="\n"
    )
    _init_git_repo(tmp_path)
    return tmp_path


@pytest.mark.no_plan_bootstrap
@pytest.mark.asyncio
async def test_retry_after_real_blocker_resolution_completes_same_objective(
    edit_repo_with_broken_tests: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        json.dumps({"svc": str(edit_repo_with_broken_tests).replace("\\", "/")}),
    )
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    admit = await backend.command(
        operation="admit_objective", tenant_id="t", actor_id="h", resource_id=None,
        body={
            "workhub_task_id": "wh-1", "task_class": "bounded_repair", "definition_of_done": ("done",),
            "repository_id": "svc", "allowed_files": (),
            "objective": "replace 'VALUE_HOST' with 'VALUE_HOST_READY'",
        },
        idempotency_key="admit-1", expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle", tenant_id="t", actor_id="h", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 5}},
        idempotency_key="start-1", expected_version=None,
    )
    seed_accepted_bootstrap_contract(
        backend,
        admit["objective_id"],
        started["cycle_id"],
        target="backend/config.py",
        purpose="replace 'VALUE_HOST' with 'VALUE_HOST_READY'",
        validation_commands=("python -m pytest tests/test_smoke.py",),
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="t", actor_id="h", resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/1"},
        idempotency_key="approve-1", expected_version=started["version"],
    )
    assert approved["ok"] is False
    assert approved["phase"] == "BLOCKED"
    assert approved["terminal_reason"] == "COMPLETION_GATES_FAILED"

    # The real-world blocker gets resolved (a human fixes the repo's broken test).
    (edit_repo_with_broken_tests / "tests" / "test_smoke.py").write_text(
        "def test_smoke():\n    assert True\n", encoding="utf-8", newline="\n"
    )

    retried = await backend.command(
        operation="retry_step", tenant_id="t", actor_id="h", resource_id=started["cycle_id"],
        body={"reason": "fixed the repo's broken test suite"},
        idempotency_key="retry-1", expected_version=approved.get("version"),
    )
    assert retried["ok"] is True
    assert retried["phase"] == "COMPLETED_FOR_REVIEW"
    assert retried["real_execute"]["objective_status"] == "COMPLETED"
    assert retried["attempt_number"] == 2

    # Same objective/cycle -- never a new one.
    assert admit["objective_id"] in backend._objectives  # noqa: SLF001
    record = backend._objectives[admit["objective_id"]]  # noqa: SLF001
    assert record.repository_id == "svc"
    assert record.execution_context is not None


@pytest.mark.asyncio
async def test_retry_on_structural_block_is_not_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    """A structural/config block (unregistered capability, unbound workspace, ...)
    cannot be fixed by a bare retry -- `decide_retry` must not authorize one."""
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    admit = await backend.command(
        operation="admit_objective", tenant_id="t", actor_id="h", resource_id=None,
        body={
            "workhub_task_id": "wh-1", "task_class": "bounded_repair", "definition_of_done": ("done",),
            "repository_id": "unregistered-repo", "allowed_files": (),
            "objective": "D:\\nope\\this\\path\\does\\not\\exist\\.env read this file",
        },
        idempotency_key="admit-2", expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle", tenant_id="t", actor_id="h", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 5}},
        idempotency_key="start-2", expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="t", actor_id="h", resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/2"},
        idempotency_key="approve-2", expected_version=started["version"],
    )
    assert approved["ok"] is False
    assert approved["phase"] == "BLOCKED"
    assert approved["terminal_reason"] == "WORKSPACE_NOT_BOUND"

    retried = await backend.command(
        operation="retry_step", tenant_id="t", actor_id="h", resource_id=started["cycle_id"],
        body={"reason": "just try again"},
        idempotency_key="retry-2", expected_version=approved.get("version"),
    )
    assert retried["ok"] is False
    assert retried["error"]["code"] == "RETRY_NOT_AUTHORIZED"
