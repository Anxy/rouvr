"""AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P4: Make the Real Planner Canonical.

Pins a real, previously-undiscovered ordering bug found while implementing this
packet: `_maybe_real_execute`'s planner block only calls `propose_step_contract` when
`UnderstandingEvidenceV1` is already cached, but that cache was only ever populated BY
the exploration gate -- which used to run *after* the planner block. Every existing
flag-on test masked this by pre-seeding `backend._understanding_evidence` directly
(`_seed_understanding`/`_seed_contract_and_understanding` helpers across
`test_w6_governance_scope.py`, `test_al09_auto_repair.py`, etc.) -- none of them prove
a real, non-seeded single-approval flow ever reaches the planner at all. This test
does: real temp git repo, real exploration gate (Packet P5's bound-target discovery),
only the live model call itself stubbed (no live LLM in this environment).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    RiskTier,
    StepContractV1,
    UnderstandingEvidenceV1,
)
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.planner.step_contract_planner import StepContractProposalV1


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture
def cron_repo(tmp_path: Path) -> Path:
    (tmp_path / "CronJobController.php").write_text(
        "<?php\nclass CronJobController {\n    public function runBillingCron() { return true; }\n}\n",
        encoding="utf-8", newline="\n",
    )
    _init_git_repo(tmp_path)
    return tmp_path


async def _run_real_execute_ok(**kwargs: Any) -> RealExecuteResult:
    return RealExecuteResult(
        ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=True,
        artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
        objective_status="COMPLETED", real_operations=1, target_files_modified=1,
    )


@pytest.mark.asyncio
async def test_planner_receives_real_understanding_on_the_first_approval_call(
    cron_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"visions": "{cron_repo.as_posix()}"}}'
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED", True
    )

    calls: list[dict[str, Any]] = []

    async def _fake_propose(**kwargs: Any) -> StepContractProposalV1:
        calls.append(kwargs)
        contract = StepContractV1(
            cycle_id=kwargs["cycle_id"], tenant_id=kwargs["tenant_id"], step_index=0,
            purpose="group public cron methods by domain", target_scope=("CronJobController.php",),
            expected_output_schema_ref="aix.step_contract.v1.generic_edit",
            acceptance_criteria=("grouped",), validation_commands=("php -l CronJobController.php",),
            assigned_role=kwargs["assigned_role"], risk_tier=RiskTier.LOW, step_budget=kwargs["step_budget"],
            obligation_refs=tuple(o.id for o in kwargs["obligations"]),
            status="PENDING",
        )
        return StepContractProposalV1(contract=contract, reply="proposed", reason=None)

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract", _fake_propose
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": "refactor CronJobController.php: group public cron methods by domain",
            "task_class": "single_file_helper",
            "definition_of_done": ("group public methods by domain",),
            "repository_id": "visions",
            "allowed_files": ("CronJobController.php",),
        },
        idempotency_key="admit-p4", expected_version=None,
    )
    assert admit["ok"] is True
    objective_id = admit["objective_id"]
    # Deliberately NOT pre-seeding backend._understanding_evidence or
    # backend._step_contracts -- proving the real, unassisted single-call flow.

    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=objective_id,
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-p4",
        expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/p4"},
        idempotency_key="approve-p4", expected_version=started["version"],
    )

    assert approved["ok"] is True, approved
    assert approved["phase"] == "COMPLETED_FOR_REVIEW"

    # The core assertion: the planner was actually called, on the first and only
    # approval call, with real (non-empty) UnderstandingEvidenceV1 -- not skipped
    # because understanding wasn't ready yet.
    assert len(calls) == 1, "propose_step_contract must be called exactly once, on the first approval"
    understanding: UnderstandingEvidenceV1 = calls[0]["understanding"]
    assert understanding.files_read, "planner must receive real evidence, not an empty snapshot"
    assert "CronJobController.php" in understanding.files_read
    assert len(calls[0]["obligations"]) == 1

    assert objective_id in backend._step_contracts  # noqa: SLF001
