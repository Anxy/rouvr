"""AIX Closed-Loop Execution Plan, Sprint W4 exit condition: prove -- through the real
production admission/cycle path (`DurableObjectiveCycleApiBackend.command`), not just the
isolated `requires_exploration()` unit test in `test_refactor_bound_skips_exploration.py`
-- that a REFACTOR objective bound to a real, single, already-known file does not hit the
`REPEATED_IDENTICAL_READ`/`EXPLORATION_REQUIRED` block the 2026-08-12 FeeDue POC found
(A1_vague/A3_extract). This is a repeatable, automated substitute for manually re-running
that POC's live HTTP scenario, which needs infrastructure (a running :8100 service) this
test environment does not have -- same code path, same real repository/file binding, no
HTTP layer in between.
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
def fee_repo(tmp_path: Path) -> Path:
    (tmp_path / "FeeDue.php").write_text(
        "function getDueFeeStudent($selectedid,$studentIds,$filter,$in,$InstBrId='') {\n"
        "    return array('data' => array(), 'total_due_fee_amount' => 0);\n"
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


@pytest.mark.asyncio
async def test_bound_refactor_objective_does_not_block_on_exploration_required(
    fee_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"visions": "{fee_repo.as_posix()}"}}'
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_stub
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": "refactor and optimise FeeDue.php",
            "task_class": "single_file_helper",
            "definition_of_done": ("N+1 queries batched",),
            "repository_id": "visions",
            "allowed_files": ("FeeDue.php",),
        },
        idempotency_key="admit-w4", expected_version=None,
    )
    assert admit["ok"] is True, admit
    assert admit["task_family"] == "REFACTOR"

    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-w4",
        expected_version=None,
    )
    assert started["ok"] is True

    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/w4"},
        idempotency_key="approve-w4", expected_version=started["version"],
    )

    # The 2026-08-12 FeeDue POC's A1_vague/A3_extract both got exactly this outcome:
    # ok=False, phase stuck, error code EXPLORATION_REQUIRED / REPEATED_IDENTICAL_READ,
    # real_execute never called. A bound single-file REFACTOR must not reproduce it.
    assert approved.get("error", {}).get("code") not in {"EXPLORATION_REQUIRED", "REPEATED_IDENTICAL_READ"}
    assert approved["ok"] is True, approved
    assert approved["phase"] == "COMPLETED_FOR_REVIEW"

    # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P5: "target known" skips TARGET
    # DISCOVERY (the iterative explore-to-find-the-file loop) only, not required
    # understanding -- a bounded discovery pass now still runs and collects real
    # evidence (previously this asserted the opposite, `not context.get("files_read")`,
    # which was P0's own documented bug: zero evidence ever collected for a bound-file
    # mutation). The bound-file skip's actual point -- never entering the iterative
    # multi-round EXPLORATION_REQUIRED loop -- is unaffected, proven by the assertions
    # above (no EXPLORATION_REQUIRED/REPEATED_IDENTICAL_READ, straight to
    # COMPLETED_FOR_REVIEW).
    objective_id = admit["objective_id"]
    record = backend._objectives[objective_id]  # noqa: SLF001
    context = record.execution_context or {}
    assert context.get("files_read"), "bound-target understanding should have read the real file"
    assert objective_id in backend._understanding_evidence  # noqa: SLF001
    evidence = backend._understanding_evidence[objective_id]  # noqa: SLF001
    assert "FeeDue.php" in evidence.files_read
    assert evidence.understanding_complete is True
