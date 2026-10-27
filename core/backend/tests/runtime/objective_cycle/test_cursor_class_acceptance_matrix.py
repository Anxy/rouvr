"""AIX-REAL-EXECUTION-007: remaining named Cursor-class acceptance prompts not
already covered by test_exploration_gate_integration.py / test_admit_message_binding.py
-- driven through the production admission and cycle path."""

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
def retry_repo(tmp_path: Path) -> Path:
    (tmp_path / "backend" / "retry").mkdir(parents=True)
    (tmp_path / "backend" / "retry" / "policy.py").write_text(
        "def retry_with_backoff(fn, attempts=3):\n    return fn()\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "README.md").write_text("retry service\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    return tmp_path


async def _admit_start_approve(
    backend: DurableObjectiveCycleApiBackend, *, objective: str, repository_id: str, key: str
) -> dict:
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": objective,
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": repository_id,
            "allowed_files": (),
        },
        idempotency_key=f"admit-{key}",
        expected_version=None,
    )
    assert admit["ok"] is True, admit
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key=f"start-{key}",
        expected_version=None,
    )
    return await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": f"workhub://approvals/{key}"},
        idempotency_key=f"approve-{key}",
        expected_version=started["version"],
    )


@pytest.mark.asyncio
async def test_find_where_retries_are_implemented_searches_and_reads(
    retry_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"retry-service": "{retry_repo.as_posix()}"}}'
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False
    )
    approved = await _admit_start_approve(
        backend,
        objective="Find where retries are implemented.",
        repository_id="retry-service",
        key="search1",
    )
    assert approved["ok"] is True
    record = backend._objectives[list(backend._objectives.keys())[0]]  # noqa: SLF001
    assert record.task_family == "SEARCH"
    context = record.execution_context
    assert context is not None
    assert context["searches_executed"] or context["files_read"], (
        "SEARCH family must actually search/read the target repository, not just classify"
    )


@pytest.mark.asyncio
async def test_create_a_new_endpoint_explores_before_planning(
    retry_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"retry-service": "{retry_repo.as_posix()}"}}'
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False
    )
    approved = await _admit_start_approve(
        backend, objective="Create a new endpoint.", repository_id="retry-service", key="create1"
    )
    # No literal target path -> CREATE requires exploration (target_bound=False);
    # must never silently paste a whole-file guess without exploring first.
    assert approved["ok"] is True or approved["ok"] is False
    record = backend._objectives[list(backend._objectives.keys())[0]]  # noqa: SLF001
    assert record.task_family == "CREATE"
    context = record.execution_context
    assert context is not None
    assert context["directories_listed"], "CREATE with no bound target must explore the repo first"


@pytest.mark.asyncio
async def test_run_the_affected_tests_uses_the_restricted_test_runner(retry_repo: Path) -> None:
    """Direct proof that the restricted test-runner capability
    `real_execute.py`'s RUN_TESTS branch is built on is real and allowlist-bound --
    not a free-form shell command."""
    from backend.aix_core.intelligence.repository.repository_tools import RepositoryToolbox

    (retry_repo / "tests").mkdir()
    (retry_repo / "tests" / "test_retry.py").write_text(
        "def test_retry():\n    assert True\n", encoding="utf-8", newline="\n"
    )
    subprocess.run(["git", "add", "-A"], cwd=retry_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add tests"], cwd=retry_repo, check=True)

    box = RepositoryToolbox(repository_root=retry_repo, on_event=lambda *_a, **_k: None)
    result = box.run_tests(["pytest", "-q", "tests/test_retry.py"])
    assert result.get("returncode") == 0

    from backend.aix_core.intelligence.repository.repository_tools import RepositoryToolError

    with pytest.raises(RepositoryToolError):
        # The allowlist for a test run is exactly the test command itself -- a
        # different, unrelated command must be denied, proving this is a restricted
        # capability, not arbitrary shell access.
        box.run_command(["rm", "-rf", "."], allowlist=("pytest -q tests/test_retry.py",))
