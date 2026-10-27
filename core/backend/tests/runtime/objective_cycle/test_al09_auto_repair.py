"""Autonomous Closed-Loop Lane, Packet AL-09: automatic structured repair.

'First implementation deliberately fails. Verifier returns REPAIR. Coder repairs it.
Verifier re-runs. Second attempt passes. No human intervention.' -- a single
`validate_approval_reference` call must complete the whole loop; nothing but this test's
own assertions calls `retry_step`/`resume`.
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
        diff_text="+ $installmentCountByFsmId = array();\n",
    )


def _seed_contract_and_understanding(backend: DurableObjectiveCycleApiBackend, objective_id: str) -> None:
    backend._understanding_evidence[objective_id] = UnderstandingEvidenceV1(  # noqa: SLF001
        objective_id=objective_id, cycle_id=None, files_read=("FeeDue.php",),
        observed_problem="test evidence", understanding_complete=True,
    )
    backend._step_contracts[objective_id] = StepContractV1(  # noqa: SLF001
        cycle_id="placeholder", tenant_id="tenant-1", step_index=0, purpose="batch the N+1",
        target_scope=("FeeDue.php",), expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("loop query count decreases",), validation_commands=("php -l FeeDue.php",),
        assigned_role="executor",
        step_budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1),
        # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P3: cover every real obligation this
        # objective admitted with, or Packet P3's coverage gate refuses dispatch with
        # PLAN_INCOMPLETE before this test's own repair scenario ever gets to run --
        # not what this test is about, so declare full coverage here.
        obligation_refs=tuple(o.id for o in backend._obligations.get(objective_id, ())),  # noqa: SLF001
        status="PENDING",
    )


@pytest.mark.asyncio
async def test_first_attempt_fails_second_attempt_passes_no_human_intervention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", True
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED", True
    )

    call_count = {"n": 0}
    captured_second_objective: dict[str, Any] = {}

    async def _fake_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return ChatCompletionResultV1(
                output_text=json.dumps({
                    "verdict": "REPAIR",
                    "reasons": ["installmentCountByFsmId is declared but never used to change behavior"],
                    "criteria_results": [],
                }),
                provider="test", resolved_provider="test", model="test-model", usage={},
            )
        return ChatCompletionResultV1(
            output_text=json.dumps({"verdict": "PASS", "reasons": [], "criteria_results": []}),
            provider="test", resolved_provider="test", model="test-model", usage={},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.verification.semantic_verifier.chat_completion", _fake_chat_completion
    )

    # Fail assertion if anything calls retry_step/resume -- the whole point is that
    # the loop closes without it.
    original_command = DurableObjectiveCycleApiBackend.command

    async def _guarded_command(self: DurableObjectiveCycleApiBackend, *, operation: str, **kwargs: Any) -> Any:
        assert operation != "retry_step", "AL-09 must not require a human-initiated retry_step call"
        return await original_command(self, operation=operation, **kwargs)

    monkeypatch.setattr(DurableObjectiveCycleApiBackend, "command", _guarded_command)

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

    # The human approval executes one claimed attempt.  Repair is persisted as a
    # fresh dispatcher boundary and must be claimed by the durable dispatcher.
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-1", expected_version=started["version"],
    )

    assert approved["ok"] is True, approved
    assert approved["phase"] == "STEP_READY"
    completed = await backend.command(
        operation="dispatch_ready_step", tenant_id="tenant-1", actor_id="dispatcher-1",
        resource_id=started["cycle_id"], body={}, idempotency_key="dispatch-repair-1",
        expected_version=approved["version"],
    )
    assert completed["phase"] == "COMPLETED_FOR_REVIEW"
    assert call_count["n"] >= 2, "verifier must have been asked at least twice: REPAIR then PASS"

    verdicts = backend._verification_verdicts[objective_id]  # noqa: SLF001
    assert [v.verdict for v in verdicts][:2] == ["REPAIR", "PASS"]

    attempts = backend._step_attempts[objective_id]  # noqa: SLF001
    assert len(attempts) >= 2


@pytest.mark.asyncio
async def test_auto_repair_flag_off_still_blocks_and_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: AUTO_REPAIR_ENABLED defaults False -- CL2-P2's original
    block-and-wait behavior must be completely unaffected."""
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", True
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED", False
    )
    # AUTO_REPAIR_ENABLED forced False -- CL2-P2's original block-and-wait.

    async def _fake_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        return ChatCompletionResultV1(
            output_text=json.dumps({"verdict": "REPAIR", "reasons": ["still stubbed"], "criteria_results": []}),
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
    assert approved["ok"] is False
    assert approved["error"]["code"] == "VERIFICATION_REPAIR_REQUIRED"
    assert len(backend._verification_verdicts[objective_id]) == 1  # noqa: SLF001 -- exactly one attempt, no auto-loop
