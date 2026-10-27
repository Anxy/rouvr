"""AIX gap-closure sprint 3, Priority 6: allowed_files enforcement on every real
mutation path (EDIT_FILE/EDIT_SYMBOL/CREATE_FILE all funnel through the same
`_run_patch_propose_execute` branch -- there is no other real write path;
`RepositoryToolbox.create_file`/`delete_file` are unreachable from `run_real_execute`).
"""
from __future__ import annotations

import json
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
def multi_file_edit_repo(tmp_path: Path) -> Path:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "config.py").write_text(
        "class Settings:\n    VALUE_HOST: str = ''\n    VALUE_PORT: int = 587\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / ".env.example").write_text("VALUE_HOST=\nVALUE_PORT=587\n", encoding="utf-8", newline="\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'svc'\n", encoding="utf-8", newline="\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_smoke.py").write_text(
        "def test_smoke():\n    assert True\n", encoding="utf-8", newline="\n"
    )
    _init_git_repo(tmp_path)
    return tmp_path


async def _run(backend: DurableObjectiveCycleApiBackend, *, key: str, allowed_files: tuple[str, ...]) -> dict:
    admit = await backend.command(
        operation="admit_objective", tenant_id="t", actor_id="h", resource_id=None,
        body={
            "workhub_task_id": "wh-1", "task_class": "bounded_repair", "definition_of_done": ("done",),
            "repository_id": "svc", "allowed_files": allowed_files,
            "objective": "replace 'VALUE_HOST' with 'VALUE_HOST_READY'",
        },
        idempotency_key=f"admit-{key}", expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle", tenant_id="t", actor_id="h", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 5}},
        idempotency_key=f"start-{key}", expected_version=None,
    )
    return await backend.command(
        operation="validate_approval_reference", tenant_id="t", actor_id="h", resource_id=started["cycle_id"],
        body={"approval_reference": f"workhub://approvals/{key}"},
        idempotency_key=f"approve-{key}", expected_version=started["version"],
    )


@pytest.mark.asyncio
async def test_multi_file_mutation_with_one_file_outside_scope_is_fully_rejected(
    multi_file_edit_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This objective's real discovery surfaces TWO files to edit (backend/config.py
    and .env.example). Approving only one must reject the whole plan -- and, more
    strongly, must never write EITHER file to disk (validation runs for every edit
    before any edit is applied), not just the out-of-scope one."""
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        json.dumps({"svc": str(multi_file_edit_repo).replace("\\", "/")}),
    )
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    result = await _run(backend, key="partial", allowed_files=("backend/config.py",))

    assert result["ok"] is False
    assert result["error"]["code"] == "FILE_NOT_IN_APPROVED_SCOPE"

    config_text = (multi_file_edit_repo / "backend" / "config.py").read_text(encoding="utf-8")
    env_example_text = (multi_file_edit_repo / ".env.example").read_text(encoding="utf-8")
    assert "VALUE_HOST_READY" not in config_text
    assert "VALUE_HOST_READY" not in env_example_text


@pytest.mark.asyncio
async def test_mutation_with_all_touched_files_approved_succeeds(
    multi_file_edit_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        json.dumps({"svc": str(multi_file_edit_repo).replace("\\", "/")}),
    )
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    result = await _run(backend, key="full", allowed_files=("backend/config.py", ".env.example"))

    assert result["ok"] is True
    assert result["real_execute"]["objective_status"] == "COMPLETED"
    config_text = (multi_file_edit_repo / "backend" / "config.py").read_text(encoding="utf-8")
    assert "VALUE_HOST_READY" in config_text
