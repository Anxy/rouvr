"""AIX-REAL-EXECUTION-005: canonical execution-stage API + final report, exposed
through the production `query()` path (`_cycle_payload`), not a side channel."""

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


@pytest.mark.asyncio
async def test_completed_read_exposes_truthful_stages_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("SMTP_HOST=smtp.example.com\n", encoding="utf-8", newline="\n")
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"svc": "{tmp_path.as_posix()}"}}'
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
            "objective": f"{tmp_path / '.env'} read this file",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "svc",
            "allowed_files": (),
        },
        idempotency_key="admit-stage1",
        expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-stage1",
        expected_version=None,
    )
    await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/stage1"},
        idempotency_key="approve-stage1",
        expected_version=started["version"],
    )

    view = await backend.query(
        operation="get_cycle",
        tenant_id="tenant-1",
        resource_id=started["cycle_id"],
        child_id=None,
        filters={},
    )
    item = view["item"]
    stages = {s["stage"]: s["status"] for s in item["execution_stages"]}
    assert stages["Completed|Blocked|Failed"] == "completed"
    assert stages["Verifying"] == "completed"
    assert stages["Executing"] == "completed"

    report = item["final_report"]
    assert report is not None
    assert report["final_status"] == "COMPLETED"
    assert report["real_operations"]["target_files_read"] == 1
    assert all(c["status"] == "PASSED" for c in report["verification_results"])
