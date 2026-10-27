"""AIX Closed-Loop Execution Plan, Sprint W10: replan.py (built, previously unwired)
wired into the one verdict retry_repair.py was never meant to handle -- a semantic
verifier REPLAN verdict, meaning the plan itself looks wrong, not just incomplete.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1, UnderstandingEvidenceV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.infrastructure.model_gateway.hyeve_integration_client import ChatCompletionResultV1
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult


async def _run_real_execute_ok(**kwargs: Any) -> RealExecuteResult:
    return RealExecuteResult(
        ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=True,
        artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
        objective_status="COMPLETED", real_operations=1, target_files_modified=1,
        completion_gates={"gates": {"diff_exists": True, "verification_passed": True}, "verification_verdict": "PASSED"},
        diff_text="+ // wrong file entirely\n",
    )


def _seed_contract_and_understanding(backend: DurableObjectiveCycleApiBackend, objective_id: str) -> None:
    backend._understanding_evidence[objective_id] = UnderstandingEvidenceV1(  # noqa: SLF001
        objective_id=objective_id, cycle_id=None, files_read=("FeeDue.php",),
        observed_problem="test evidence", understanding_complete=True,
    )
    backend._step_contracts[objective_id] = StepContractV1(  # noqa: SLF001
        cycle_id="placeholder", tenant_id="tenant-1", step_index=0, purpose="fix the N+1",
        target_scope=("FeeDue.php",), expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("loop query count decreases",), validation_commands=("php -l FeeDue.php",),
        assigned_role="executor",
        step_budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1),
        # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P3: cover every real obligation this
        # objective admitted with, or the coverage gate refuses with PLAN_INCOMPLETE
        # before this test's own replan scenario ever runs.
        obligation_refs=tuple(o.id for o in backend._obligations.get(objective_id, ())),  # noqa: SLF001
        status="PENDING",
    )


@pytest.mark.asyncio
async def test_replan_verdict_produces_a_decision_and_returns_to_waiting_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", True
    )

    async def _fake_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        return ChatCompletionResultV1(
            output_text=json.dumps({
                "verdict": "REPLAN",
                "reasons": ["the diff touches the wrong file entirely -- understanding was wrong"],
                "criteria_results": [],
            }),
            provider="test", resolved_provider="test", model="test-model", usage={},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.verification.semantic_verifier.chat_completion", _fake_chat_completion
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={
            "workhub_task_id": "task-101", "objective": "refactor and optimise FeeDue.php",
            "task_class": "single_file_helper", "definition_of_done": ("done",),
            "repository_id": "aix-frontend", "allowed_files": ("FeeDue.php",), "project_id": "proj-1",
        },
        idempotency_key="admit-1", expected_version=None,
    )
    objective_id = admit["objective_id"]
    _seed_contract_and_understanding(backend, objective_id)

    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=objective_id,
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-1",
        expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-1", expected_version=started["version"],
    )

    # A REPLAN verdict must not silently complete the cycle as if nothing happened.
    assert approved["phase"] != "COMPLETED_FOR_REVIEW"
    assert approved["phase"] == "WAITING_APPROVAL"

    decisions = backend._replan_decisions.get(objective_id)  # noqa: SLF001
    assert decisions is not None and len(decisions) == 1
    decision = decisions[0]
    # AL-10: this decision has no added_step_ids (no automatic re-planning pipeline
    # proposes new scope yet), so requires_approval is correctly False per
    # ReplanDecisionV1's own rule -- but the cycle still lands in WAITING_APPROVAL
    # here because INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED is off in this test (the
    # separate, additional gate that actually authorizes unattended continuation).
    assert decision.requires_approval is False
    assert "wrong file" in decision.reason

    cycle = backend._cycles[started["cycle_id"]]  # noqa: SLF001
    assert cycle.replan_budget_remaining == 1  # started at 2, one consumed
    assert cycle.plan_revision == 2  # bumped, so a stale approval can never silently reuse the old gate


@pytest.mark.asyncio
async def test_replan_budget_exhaustion_denies_further_replans(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", True
    )

    async def _fake_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        return ChatCompletionResultV1(
            output_text=json.dumps({"verdict": "REPLAN", "reasons": ["still wrong"], "criteria_results": []}),
            provider="test", resolved_provider="test", model="test-model", usage={},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.verification.semantic_verifier.chat_completion", _fake_chat_completion
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={
            "workhub_task_id": "task-101", "objective": "refactor and optimise FeeDue.php",
            "task_class": "single_file_helper", "definition_of_done": ("done",),
            "repository_id": "aix-frontend", "allowed_files": ("FeeDue.php",), "project_id": "proj-1",
        },
        idempotency_key="admit-1", expected_version=None,
    )
    objective_id = admit["objective_id"]
    _seed_contract_and_understanding(backend, objective_id)
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=objective_id,
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-1",
        expected_version=None,
    )
    backend._cycles[started["cycle_id"]].replan_budget_remaining = 0  # noqa: SLF001

    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-1", expected_version=started["version"],
    )
    # Denied replan must not be silently treated as authorized -- no decision recorded.
    assert objective_id not in backend._replan_decisions  # noqa: SLF001
