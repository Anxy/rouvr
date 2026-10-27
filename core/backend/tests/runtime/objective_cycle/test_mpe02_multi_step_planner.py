"""AIX-MPE-003 Packet MPE-02: planner emits PlanRevisionV1 with StepContractV1[]."""
from __future__ import annotations

import json
from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, ObjectiveObligationV1, UnderstandingEvidenceV1
from backend.infrastructure.model_gateway.hyeve_integration_client import ChatCompletionResultV1
from backend.aix_core.runtime.planner.step_contract_planner import propose_step_contract


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1)


def _understanding() -> UnderstandingEvidenceV1:
    return UnderstandingEvidenceV1(
        objective_id="obj-1",
        cycle_id="cyc-1",
        files_read=("CronJobController.php",),
        symbols_found=("runBillingCron",),
        observed_problem="refactor CronJobController.php",
        understanding_complete=True,
    )


async def _call(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any], **kwargs: Any) -> Any:
    async def _fake_chat_completion(**_kwargs: Any) -> ChatCompletionResultV1:
        return ChatCompletionResultV1(
            output_text=json.dumps(payload), provider="test", resolved_provider="test",
            model="test-model", usage={},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion", _fake_chat_completion
    )
    return await propose_step_contract(
        objective="Refactor CronJobController.php: group methods, extract helpers, remove unused imports",
        definition_of_done=["group methods", "extract helpers", "remove unused imports"],
        understanding=_understanding(),
        constraints=["do not rename public methods"],
        tenant_id="tenant-1",
        cycle_id="cyc-1",
        step_index=0,
        assigned_role="executor",
        step_budget=_budget(),
        actor_id="human-1",
        authorization_header="",
        timeout_seconds=5.0,
        objective_id="obj-1",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_legacy_single_object_still_becomes_a_one_step_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    proposal = await _call(
        monkeypatch,
        {
            "reply": "one step",
            "needs_more_evidence": False,
            "purpose": "group public cron methods by domain",
            "target_scope": ["CronJobController.php"],
            "acceptance_criteria": ["grouped"],
            "validation_commands": ["php -l CronJobController.php"],
            "prohibited_actions": ["do not rename public methods"],
            "risk_tier": "LOW",
        },
    )
    assert proposal.contract is not None
    assert proposal.plan is not None
    assert len(proposal.plan.steps) == 1
    assert proposal.plan.steps[0].step_index == 0
    assert proposal.plan.steps[0].id == proposal.contract.id


@pytest.mark.asyncio
async def test_steps_array_builds_a_real_multi_step_plan_with_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    obs = (
        ObjectiveObligationV1(objective_id="obj-1", description="group methods", required=True),
        ObjectiveObligationV1(objective_id="obj-1", description="extract helpers", required=True),
        ObjectiveObligationV1(objective_id="obj-1", description="remove unused imports", required=True),
    )
    proposal = await _call(
        monkeypatch,
        {
            "reply": "decomposed",
            "needs_more_evidence": False,
            "steps": [
                {
                    "purpose": "inventory public cron actions",
                    "target_scope": ["CronJobController.php"],
                    "acceptance_criteria": ["inventory exists"],
                    "validation_commands": ["php -l CronJobController.php"],
                    "obligation_refs": [obs[0].id],
                    "depends_on": [],
                },
                {
                    "purpose": "group public methods by domain",
                    "target_scope": ["CronJobController.php"],
                    "acceptance_criteria": ["grouped by domain"],
                    "validation_commands": ["php -l CronJobController.php"],
                    "obligation_refs": [obs[0].id],
                    "depends_on": [0],
                },
                {
                    "purpose": "extract repeated DB helpers",
                    "target_scope": ["CronJobController.php"],
                    "acceptance_criteria": ["helpers extracted"],
                    "validation_commands": ["php -l CronJobController.php"],
                    "obligation_refs": [obs[1].id],
                    "depends_on": [0],
                },
                {
                    "purpose": "remove unused imports and verify HTTP names",
                    "target_scope": ["CronJobController.php"],
                    "acceptance_criteria": ["unused imports gone"],
                    "validation_commands": ["php -l CronJobController.php"],
                    "obligation_refs": [obs[2].id],
                    "depends_on": [1, 2],
                },
            ],
        },
        obligations=obs,
    )
    assert proposal.plan is not None
    assert len(proposal.plan.steps) == 4
    s1, s2, s3, s4 = proposal.plan.steps
    assert s1.step_index == 0
    assert s2.depends_on == (s1.id,)
    assert s3.depends_on == (s1.id,)
    assert set(s4.depends_on) == {s2.id, s3.id}
    assert proposal.contract is not None
    assert proposal.contract.id == s1.id
