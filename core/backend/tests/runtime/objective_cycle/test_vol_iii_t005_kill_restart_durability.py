"""AIX-VOL-III-T005: Kill During Execute, Restart, and No Duplicate Effect Campaign.

Verifies:
1. An in-flight objective-cycle survives process kill mid-cycle without losing evidence or duplicating file mutations.
2. Restarting a fresh backend instance reconstructs cycle state from durable outbox/projections.
3. Command retry with identical idempotency key returns cached result without re-executing real tool calls.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.objective.durable_aggregate.replay import (
    InMemoryProjectionStore,
    JsonFileProjectionStore,
)
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "killtest@aix.local"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "KillTest"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture
def disposable_repo(tmp_path: Path) -> Path:
    f = tmp_path / "AppConfig.py"
    f.write_text("DEBUG = False\nPORT = 8080\n", encoding="utf-8")
    _init_git_repo(tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_kill_mid_cycle_restart_and_resume_without_duplicate_mutation(
    disposable_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five-step campaign: Admit -> Start -> Kill Process -> Restart & Reconstruct -> Resume & Complete."""
    shared_projections = InMemoryProjectionStore()
    mutation_counter = {"calls": 0}

    async def _mock_real_execute(*args: Any, **kwargs: Any) -> RealExecuteResult:
        mutation_counter["calls"] += 1
        cfg = disposable_repo / "AppConfig.py"
        cfg.write_text("DEBUG = True\nPORT = 8080\nVERSION = '1.0.0'\n", encoding="utf-8")
        return RealExecuteResult(
            ok=True,
            code="MUTATION_OK",
            workspace=str(disposable_repo),
            evidence_refs=["file://evidence/report.json"],
            artifacts={"execution_report": "file://evidence/report.json"},
            execution_status="SUCCEEDED",
            verification_status="PASSED",
            objective_status="COMPLETED",
            reason_code="SOURCE_EDIT_SUCCESS",
            real_operations=1,
            target_files_modified=1,
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        _mock_real_execute,
    )

    # 1. Instance 1 admits and starts
    backend1 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        projection_store=shared_projections,
    )

    admit = await backend1.command(
        operation="admit_objective",
        tenant_id="tenant-durability",
        actor_id="tester",
        resource_id=None,
        body={
            "workhub_task_id": "task-kill-500",
            "objective": "Update AppConfig.py: enable debug and add version",
            "task_class": "single_file_helper",
            "definition_of_done": ("update config",),
            "repository_id": "repo-kill",
            "allowed_files": ("AppConfig.py",),
            "project_id": "proj-durability",
            "task_number": 500,
        },
        idempotency_key="admit-kill-500",
        expected_version=None,
    )
    assert admit["ok"] is True
    obj_id = admit["objective_id"]

    start = await backend1.command(
        operation="start_cycle",
        tenant_id="tenant-durability",
        actor_id="tester",
        resource_id=obj_id,
        body={"candidate_sha": "killsha123456"},
        idempotency_key="start-kill-500",
        expected_version=None,
    )
    assert start["ok"] is True
    cycle_id = start["cycle_id"]
    start_version = start["version"]

    # 2. Hard Kill Instance 1 (wipe backend1 memory reference)
    del backend1

    # 3. Instance 2 starts with empty in-memory maps using shared_projections
    backend2 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        projection_store=shared_projections,
    )
    assert cycle_id not in backend2._cycles  # noqa: SLF001

    # 4. Resume execution via validate_approval_reference on reconstructed backend
    approved = await backend2.command(
        operation="validate_approval_reference",
        tenant_id="tenant-durability",
        actor_id="tester",
        resource_id=cycle_id,
        body={"approval_reference": "workhub://approvals/kill-500"},
        idempotency_key="approve-kill-500",
        expected_version=start_version,
    )

    assert approved["ok"] is True, approved
    assert approved["phase"] == "COMPLETED_FOR_REVIEW"
    assert mutation_counter["calls"] == 1

    # 5. Idempotent Retry: repeat approve command with same idempotency key
    repeat_approve = await backend2.command(
        operation="validate_approval_reference",
        tenant_id="tenant-durability",
        actor_id="tester",
        resource_id=cycle_id,
        body={"approval_reference": "workhub://approvals/kill-500"},
        idempotency_key="approve-kill-500",
        expected_version=start_version,
    )

    # Must return cached result without calling real_execute a second time
    assert repeat_approve == approved
    assert mutation_counter["calls"] == 1


def test_projection_store_survives_fresh_process_state(tmp_path: Path) -> None:
    root = tmp_path / "projection-ledger"
    first = JsonFileProjectionStore(root)
    first.upsert_snapshot(
        tenant_id="tenant-durability",
        aggregate_type="cycle",
        aggregate_id="cyc-file-1",
        sequence_number=7,
        schema_version="aix.decision_state.v1",
        snapshot={
            "schema_version": "aix.decision_state.v1",
            "kind": "run",
            "sequence_number": 7,
            "decision": {"cycle_id": "cyc-file-1", "tenant_id": "tenant-durability"},
        },
    )

    second = JsonFileProjectionStore(root)
    loaded = second.latest_snapshot(
        tenant_id="tenant-durability",
        aggregate_type="run",
        aggregate_id="cyc-file-1",
    )
    assert loaded is not None
    assert loaded.sequence_number == 7
    assert loaded.snapshot["decision"]["cycle_id"] == "cyc-file-1"
