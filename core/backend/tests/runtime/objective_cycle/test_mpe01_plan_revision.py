"""AIX-MPE-003 Packet MPE-01: Introduce versioned multi-step plan.

Proves the packet's own acceptance test: a planner can persist

    Plan R1
      S1
      S2 depends S1
      S3 depends S1
      S4 depends S2, S3

and reload the exact plan later -- plus the required invariants (unique step ids,
dependencies reference a real step in the same plan, no dependency cycles) and the
live insertion point in `durable_api_backend.py`: a successful single-step planner
proposal is now also mirrored into the new `PlanRevisionV1` store, not just the
pre-existing `_step_contracts` cache.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    PlanRevisionV1,
    RiskTier,
    StepContractV1,
)
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.planner.step_contract_planner import StepContractProposalV1

_BUDGET = BudgetV1(time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1)


def _step(step_id_suffix: str, *, depends_on: tuple[str, ...] = ()) -> StepContractV1:
    return StepContractV1(
        id=f"step-{step_id_suffix}",
        cycle_id="cycle-1",
        tenant_id="tenant-1",
        step_index=int(step_id_suffix[1:]) - 1,
        purpose=f"do part {step_id_suffix}",
        depends_on=depends_on,
        target_scope=("file.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("methods are grouped by domain in CronJobController.php",),
        validation_commands=("true",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        status="PENDING",
    )


def _four_step_plan() -> PlanRevisionV1:
    s1 = _step("s1")
    s2 = _step("s2", depends_on=("step-s1",))
    s3 = _step("s3", depends_on=("step-s1",))
    s4 = _step("s4", depends_on=("step-s2", "step-s3"))
    return PlanRevisionV1(
        objective_id="obj-1",
        cycle_id="cycle-1",
        revision=1,
        steps=(s1, s2, s3, s4),
    )


def test_plan_with_real_dependency_structure_persists_and_reloads_exactly() -> None:
    plan = _four_step_plan()

    reloaded = PlanRevisionV1.model_validate(plan.model_dump())

    assert reloaded == plan
    assert [s.id for s in reloaded.steps] == ["step-s1", "step-s2", "step-s3", "step-s4"]
    assert reloaded.steps[1].depends_on == ("step-s1",)
    assert reloaded.steps[2].depends_on == ("step-s1",)
    assert reloaded.steps[3].depends_on == ("step-s2", "step-s3")


def test_duplicate_step_ids_are_rejected() -> None:
    s1 = _step("s1")
    s1_dup = _step("s1")
    with pytest.raises(ValidationError, match="unique step ids"):
        PlanRevisionV1(objective_id="obj-1", cycle_id="cycle-1", revision=1, steps=(s1, s1_dup))


def test_dependency_on_unknown_step_is_rejected() -> None:
    s1 = _step("s1", depends_on=("step-does-not-exist",))
    with pytest.raises(ValidationError, match="not present in this plan"):
        PlanRevisionV1(objective_id="obj-1", cycle_id="cycle-1", revision=1, steps=(s1,))


def test_circular_dependency_is_rejected() -> None:
    s1 = _step("s1", depends_on=("step-s2",))
    s2 = _step("s2", depends_on=("step-s1",))
    with pytest.raises(ValidationError, match="circular dependency"):
        PlanRevisionV1(objective_id="obj-1", cycle_id="cycle-1", revision=1, steps=(s1, s2))


def test_a_step_cannot_depend_on_itself() -> None:
    with pytest.raises(ValidationError):
        _step("s1", depends_on=("step-s1",))


def test_plan_requires_at_least_one_step() -> None:
    with pytest.raises(ValidationError):
        PlanRevisionV1(objective_id="obj-1", cycle_id="cycle-1", revision=1, steps=())


# --- Live insertion in durable_api_backend.py -------------------------------------


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
async def test_successful_proposal_is_mirrored_into_the_plan_revision_store(
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

    async def _fake_propose(**kwargs: Any) -> StepContractProposalV1:
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
        idempotency_key="admit-mpe01", expected_version=None,
    )
    assert admit["ok"] is True
    objective_id = admit["objective_id"]

    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=objective_id,
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-mpe01",
        expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/mpe01"},
        idempotency_key="approve-mpe01", expected_version=started["version"],
    )
    assert approved["ok"] is True, approved

    # AIX-MPE-003 Packet MPE-01: the new revisioned plan store now holds the same
    # step_contract too, wrapped as revision 1 of a real PlanRevisionV1 -- not a
    # second, competing owner of plan state.
    plan = backend._active_plan_revision(objective_id)  # noqa: SLF001
    assert plan is not None
    assert plan.revision == 1
    assert plan.objective_id == objective_id
    assert len(plan.steps) == 1
    assert plan.steps[0].id == backend._step_contracts[objective_id].id  # noqa: SLF001

    reloaded = PlanRevisionV1.model_validate(plan.model_dump())
    assert reloaded == plan

    plan_response = await backend.query(
        operation="get_plan",
        tenant_id="tenant-1",
        resource_id=started["cycle_id"],
        child_id=None,
        filters={},
    )
    assert plan_response["ok"] is True
    assert plan_response["plan_id"] == plan.plan_id
    assert len(plan_response["steps"]) == 1

    steps_response = await backend.query(
        operation="get_steps",
        tenant_id="tenant-1",
        resource_id=started["cycle_id"],
        child_id=None,
        filters={},
    )
    assert steps_response["ok"] is True
    assert steps_response["count"] == 1
    assert steps_response["steps"][0]["status"] == "PASS"
