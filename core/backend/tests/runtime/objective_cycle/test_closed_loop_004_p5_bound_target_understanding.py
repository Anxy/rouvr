"""AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P5: Understanding Becomes Task-Required.

P0's documented assumption: "file/path already known -> skip understanding". Fixed to:
"target known -> skip target discovery only -> still perform required understanding".
Proven through the real `DurableObjectiveCycleApiBackend.command()` path (not just a
unit test of the helper), reusing the same temp-git-repo fixture style
`test_w4_refactor_bound_exploration_live.py` already established.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture
def cron_repo(tmp_path: Path) -> Path:
    (tmp_path / "CronJobController.php").write_text(
        "<?php\nclass CronJobController {\n"
        "    public function runBillingCron() { return true; }\n"
        "    public function runNotifyCron() { return true; }\n"
        "}\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "OtherController.php").write_text(
        "<?php\nclass OtherController {\n"
        "    public function runBillingCron() { return false; }\n"
        "}\n",
        encoding="utf-8",
        newline="\n",
    )
    _init_git_repo(tmp_path)
    return tmp_path


async def _run_real_execute_stub(**kwargs: Any) -> RealExecuteResult:
    return RealExecuteResult(
        ok=True, code="REAL_EXECUTE_OK", workspace=str(kwargs.get("repository_id") or ""),
        evidence_refs=[], workhub_loaded=True, artifacts={}, execution_status="COMPLETED",
        verification_status="PASSED", objective_status="COMPLETED", real_operations=1,
        target_files_modified=1,
    )


async def _admit_bound_objective(
    backend: DurableObjectiveCycleApiBackend, *, allowed_files: tuple[str, ...]
) -> dict[str, Any]:
    return await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": "refactor CronJobController.php: group public cron methods by domain",
            "task_class": "single_file_helper",
            "definition_of_done": ("group public methods by domain",),
            "repository_id": "visions",
            "allowed_files": allowed_files,
        },
        idempotency_key="admit-p5", expected_version=None,
    )


@pytest.mark.asyncio
async def test_bound_target_gets_real_file_content_as_understanding_evidence(
    cron_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"visions": "{cron_repo.as_posix()}"}}'
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_stub
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    admit = await _admit_bound_objective(backend, allowed_files=("CronJobController.php",))
    assert admit["ok"] is True

    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-p5",
        expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/p5"},
        idempotency_key="approve-p5", expected_version=started["version"],
    )
    assert approved["ok"] is True, approved
    assert approved["phase"] == "COMPLETED_FOR_REVIEW"

    objective_id = admit["objective_id"]
    assert objective_id in backend._understanding_evidence  # noqa: SLF001
    evidence = backend._understanding_evidence[objective_id]  # noqa: SLF001
    assert "CronJobController.php" in evidence.files_read
    assert evidence.understanding_complete is True
    # A real search for objective-derived keywords ("cronjobcontroller", "public",
    # "domain", ...) ran too -- not just the single target-file read.
    assert len(evidence.evidence_refs) >= 1


@pytest.mark.asyncio
async def test_multiple_bound_files_are_all_read_within_the_bound(
    cron_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"visions": "{cron_repo.as_posix()}"}}'
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_stub
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    admit = await _admit_bound_objective(
        backend, allowed_files=("CronJobController.php", "OtherController.php")
    )
    assert admit["ok"] is True
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-p5b",
        expected_version=None,
    )
    await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/p5b"},
        idempotency_key="approve-p5b", expected_version=started["version"],
    )

    evidence = backend._understanding_evidence[admit["objective_id"]]  # noqa: SLF001
    assert "CronJobController.php" in evidence.files_read
    assert "OtherController.php" in evidence.files_read


@pytest.mark.asyncio
async def test_bound_target_understanding_is_collected_once_not_on_every_call(
    cron_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-approval/resume on the same objective must not silently re-collect (and
    thereby overwrite) evidence with a second discovery pass -- it is guarded by
    `obj.objective_id not in self._understanding_evidence`, same idempotency
    discipline as the rest of this module's caches."""
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"visions": "{cron_repo.as_posix()}"}}'
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_stub
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    admit = await _admit_bound_objective(backend, allowed_files=("CronJobController.php",))
    objective_id = admit["objective_id"]
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=objective_id,
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-p5c",
        expected_version=None,
    )
    await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/p5c"},
        idempotency_key="approve-p5c", expected_version=started["version"],
    )
    first_evidence_id = backend._understanding_evidence[objective_id].id  # noqa: SLF001

    obj = backend._objectives[objective_id]  # noqa: SLF001
    gate_result = await backend._run_exploration_gate(  # noqa: SLF001
        cycle=backend._cycles[started["cycle_id"]], obj=obj  # noqa: SLF001
    )
    assert gate_result is None
    assert backend._understanding_evidence[objective_id].id == first_evidence_id  # noqa: SLF001
