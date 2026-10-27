"""AIX-MPE-003 Packets MPE-06–MPE-09 / MPE-16: one READY step, workspace ids, executor package."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, PlanRevisionV1, RiskTier, StepContractV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.planner.step_contract_planner import StepContractProposalV1

_BUDGET = BudgetV1(time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1)


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


def _step(*, step_id: str, index: int, depends_on: tuple[str, ...] = (), purpose: str) -> StepContractV1:
    return StepContractV1(
        id=step_id,
        cycle_id="placeholder",
        tenant_id="tenant-1",
        step_index=index,
        purpose=purpose,
        depends_on=depends_on,
        target_scope=("CronJobController.php",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("methods are grouped by domain in CronJobController.php",),
        validation_commands=("php -l CronJobController.php",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        status="PENDING",
    )


async def _admit_start_approve(backend: DurableObjectiveCycleApiBackend) -> dict[str, Any]:
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={
            "workhub_task_id": "wh-mpe06",
            "objective": "refactor CronJobController.php: group methods then extract helpers",
            "task_class": "single_file_helper",
            "definition_of_done": ("group methods", "extract helpers"),
            "repository_id": "visions",
            "allowed_files": ("CronJobController.php",),
        },
        idempotency_key="admit-mpe06", expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 5}},
        idempotency_key="start-mpe06", expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/mpe06"},
        idempotency_key="approve-mpe06", expected_version=started["version"],
    )
    return {"admit": admit, "started": started, "approved": approved}


@pytest.mark.asyncio
async def test_three_step_plan_uses_distinct_workspaces_and_one_step_packages(
    cron_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"visions": "{cron_repo.as_posix()}"}}'
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED", True
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", False
    )
    captured: list[dict[str, Any]] = []

    async def _fake_execute(**kwargs: Any) -> RealExecuteResult:
        captured.append(
            {
                "workspace_step_id": kwargs.get("workspace_step_id"),
                "workspace_attempt_id": kwargs.get("workspace_attempt_id"),
                "purpose": None if kwargs.get("step_contract") is None else kwargs["step_contract"].purpose,
            }
        )
        return RealExecuteResult(
            ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=True,
            artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
            objective_status="COMPLETED", real_operations=1, target_files_modified=1,
            diff_text=f"+ // {kwargs.get('workspace_step_id')}\n",
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _fake_execute
    )

    async def _fake_propose(**kwargs: Any) -> StepContractProposalV1:
        s1 = _step(step_id="step-s1", index=0, purpose="inventory public methods")
        s2 = _step(step_id="step-s2", index=1, depends_on=("step-s1",), purpose="group methods by domain")
        s3 = _step(step_id="step-s3", index=2, depends_on=("step-s2",), purpose="extract DB helpers")
        data = [s.model_dump() for s in (s1, s2, s3)]
        for item in data:
            item["cycle_id"] = kwargs["cycle_id"]
            item["obligation_refs"] = [o.id for o in kwargs["obligations"]]
        steps = tuple(StepContractV1.model_validate(item) for item in data)
        plan = PlanRevisionV1(
            objective_id=kwargs["objective_id"],
            cycle_id=kwargs["cycle_id"],
            revision=1,
            steps=steps,
        )
        return StepContractProposalV1(contract=steps[0], reply="decomposed", reason=None, plan=plan)

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract", _fake_propose
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    result = await _admit_start_approve(backend)
    assert result["approved"]["ok"] is True, result["approved"]
    assert result["approved"]["phase"] == "NEXT_STEP_READY"
    assert [c["workspace_step_id"] for c in captured] == ["step-1"]

    for dispatch_index in (2, 3):
        dispatched = await backend.command(
            operation="dispatch_ready_step", tenant_id="tenant-1", actor_id="dispatcher",
            resource_id=result["started"]["cycle_id"], body={},
            idempotency_key=f"dispatch-mpe06-{dispatch_index}", expected_version=None,
        )
        assert dispatched["ok"] is True, dispatched

    assert [c["workspace_step_id"] for c in captured] == ["step-1", "step-2", "step-3"]
    attempt_ids = [c["workspace_attempt_id"] for c in captured]
    assert all(str(attempt_id).startswith("att_") for attempt_id in attempt_ids)
    assert len(set(attempt_ids)) == 3
    assert [c["purpose"] for c in captured] == [
        "inventory public methods",
        "group methods by domain",
        "extract DB helpers",
    ]

    objective_id = result["admit"]["objective_id"]
    packages = backend._executor_packages[objective_id]  # noqa: SLF001
    assert len(packages) == 3
    assert [p.step_contract_id for p in packages] == ["step-s1", "step-s2", "step-s3"]
    assert all(p.plan_id for p in packages)
    assert all(p.plan_revision == 1 for p in packages)
    assert all(p.executor_profile_id == "aix.real_execute.v1" for p in packages)
    assert all("Previous attempt failed verification" not in p.objective_boundary for p in packages)
    assert all(
        p.objective_boundary == "refactor CronJobController.php: group methods then extract helpers"
        for p in packages
    )
