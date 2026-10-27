"""AIX-VOL-II-T005: Protocol Compliance Integration Tests.

Traces the canonical 5-stage lifecycle for a disposable fixture objective:
1. Admit: admit_objective -> objective admitted, obligations derived, envelope created.
2. Explore / Ground / Plan: start_cycle -> grounding -> planning -> WAITING_APPROVAL.
3. Approve & Execute: validate_approval_reference -> RUNNING_STEP -> real_execute edit.
4. Test & Verify: validation commands run, evaluate_completion_gates evaluates diff/tests.
5. Prove & Completion Gate Evaluation: cycle reaches COMPLETED_FOR_REVIEW with evidence,
   retaining workhub_lifecycle_authority=False (derived completion, not WorkHub acceptance).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.verification.completion_gates import evaluate_completion_gates


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "aix@test.local"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "AIX-Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial commit"], cwd=root, check=True)


@pytest.fixture
def disposable_repo(tmp_path: Path) -> Path:
    target_file = tmp_path / "UserService.py"
    target_file.write_text(
        "# Target module for protocol compliance test\n"
        "class UserService:\n"
        "    def getUser(self, user_id: str):\n"
        "        return {'id': user_id}\n",
        encoding="utf-8",
    )
    _init_git_repo(tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_full_protocol_compliance_admit_explore_execute_verify_prove(
    disposable_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Traces full canonical lifecycle: Admit -> Explore/Plan -> Approve/Execute -> Verify -> Prove."""

    # 1. Setup mock real_execute for disposable repo edit
    async def _mock_real_execute(*args: Any, **kwargs: Any) -> RealExecuteResult:
        # Simulate a real edit to UserService.py
        user_service = disposable_repo / "UserService.py"
        user_service.write_text(
            "# Target module for protocol compliance test\n"
            "class UserService:\n"
            "    def getUser(self, user_id: str):\n"
            "        return {'id': user_id, 'active': True}\n"
            "    def findUserByEmail(self, email: str):\n"
            "        return {'email': email}\n",
            encoding="utf-8",
        )
        return RealExecuteResult(
            ok=True,
            code="REAL_EXECUTE_OK",
            workspace=str(disposable_repo),
            evidence_refs=["file://evidence/execution_report.json"],
            artifacts={"execution_report": "file://evidence/execution_report.json"},
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

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False
    )

    # Stage 1: Admit
    admit_res = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-compliance",
        actor_id="human-tester",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-9001",
            "objective": "Refactor UserService.py: add findUserByEmail method",
            "task_class": "single_file_helper",
            "definition_of_done": ("add findUserByEmail method",),
            "repository_id": "disposable-user-repo",
            "allowed_files": ("UserService.py",),
            "project_id": "proj-compliance",
            "task_number": 9001,
        },
        idempotency_key="admit-compliance-9001",
        expected_version=None,
    )

    assert admit_res["ok"] is True
    assert admit_res["operation"] == "admit_objective"
    assert "objective_id" in admit_res
    obj_id = admit_res["objective_id"]

    # Stage 2: Explore & Plan
    start_res = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-compliance",
        actor_id="human-tester",
        resource_id=obj_id,
        body={
            "candidate_sha": "a1b2c3d4e5f6",
            "budgets": {"max_steps": 5, "time_seconds": 3600},
        },
        idempotency_key="start-compliance-9001",
        expected_version=None,
    )

    assert start_res["ok"] is True
    assert start_res["phase"] == "WAITING_APPROVAL"
    cycle_id = start_res["cycle_id"]

    # Stage 3 & 4: Approve, Execute, Verify & Prove
    approve_res = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-compliance",
        actor_id="human-tester",
        resource_id=cycle_id,
        body={"approval_reference": "workhub://approvals/compliance-9001"},
        idempotency_key="approve-compliance-9001",
        expected_version=start_res["version"],
    )

    assert approve_res["ok"] is True
    assert approve_res["phase"] == "COMPLETED_FOR_REVIEW"
    assert approve_res["workhub_lifecycle_authority"] is False

    # Stage 5: Verification of evidence and gate derivation
    assert "real_execute" in approve_res
    real_exec = approve_res["real_execute"]
    assert real_exec["ok"] is True
    assert real_exec["execution_status"] == "SUCCEEDED"
    assert real_exec["verification_status"] == "PASSED"
    assert real_exec["objective_status"] == "COMPLETED"


def test_completion_gate_derivation_rejects_cosmetic_edits(disposable_repo: Path) -> None:
    """Verifies that evaluate_completion_gates derives failure for non-substantive cosmetic refactors."""

    class MockDiscovery:
        files_read = 1
        config_files: dict[str, str] = {}

    diff_stats = {"UserService.py": {"additions": 1, "deletions": 0}}
    diff_text = "+# cosmetic comment\n"

    gate_res = evaluate_completion_gates(
        discovery=MockDiscovery(),  # type: ignore[arg-type]
        diff_stats=diff_stats,
        diff_text=diff_text,
        test_result=None,
        already_satisfied=False,
        task_family="REFACTOR",
        objective="refactor UserService.py",
        required_effects=(),
    )

    assert gate_res.passed is False
    assert gate_res.gates["refactor_substantive"] is False
    assert any("cosmetic-only" in r for r in gate_res.reasons)
