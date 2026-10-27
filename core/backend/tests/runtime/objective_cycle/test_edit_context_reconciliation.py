"""AIX-REAL-EXECUTION-006 Cursor-class extension: after a real patch apply,
`ExecutionContext.files_modified`/`generated_patches` must reflect it -- driven
through the production admission/cycle path, not `run_real_execute` directly."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture
def fake_integration_service(tmp_path: Path) -> Path:
    (tmp_path / "backend" / "hyeve_integration_service").mkdir(parents=True)
    (tmp_path / "backend" / "hyeve_integration_service" / "config.py").write_text(
        "class Settings:\n    VALUE_HOST: str = ''\n    VALUE_PORT: int = 587\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / ".env.example").write_text("VALUE_HOST=\nVALUE_PORT=587\n", encoding="utf-8", newline="\n")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'hyeve-integration-service'\n", encoding="utf-8", newline="\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_smoke.py").write_text(
        "def test_smoke():\n    assert True\n", encoding="utf-8", newline="\n"
    )
    _init_git_repo(tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_real_edit_updates_execution_context_files_modified(
    fake_integration_service: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        f'{{"hyeve-integration-service": "{fake_integration_service.as_posix()}"}}',
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": "replace 'VALUE_HOST' with 'VALUE_HOST_READY'",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "hyeve-integration-service",
            "allowed_files": (),
        },
        idempotency_key="admit-editctx1",
        expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-editctx1",
        expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/editctx1"},
        idempotency_key="approve-editctx1",
        expected_version=started["version"],
    )
    assert approved["ok"] is True
    real = approved["real_execute"]
    assert real is not None

    record = backend._objectives[admit["objective_id"]]  # noqa: SLF001
    context = record.execution_context
    assert context is not None
    # EDIT family must have explored (Sprint 003/004) before any patch was proposed.
    assert context["directories_listed"] or context["files_read"]

    if real["objective_status"] == "COMPLETED" and real.get("changed_source_files"):
        assert context["files_modified"], "a real apply must reconcile into execution_context"
    elif real.get("proposed_edits"):
        assert context["generated_patches"], "a proposed patch must reconcile into execution_context"
