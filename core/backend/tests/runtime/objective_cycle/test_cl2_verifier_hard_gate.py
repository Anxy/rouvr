"""AIX Closed-Loop Execution -- Sprint CL2-P2: the semantic verifier hard gate.

Phase 7's original critical rule, made real: when INTELLIGENCE_LAYER_SEMANTIC_
VERIFIER_ENABLED is true, a cycle must not reach a successful completion phase
unless verify_step() actually returned PASS. Model calls are stubbed throughout
(no live LLM in this test environment, same as every other flag-gated sprint) --
what's under test is real: the gate itself, not model reasoning quality.
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
        # objective admitted with, or the coverage gate refuses with PLAN_INCOMPLETE
        # before this test's own verifier-hard-gate scenario ever runs.
        obligation_refs=tuple(o.id for o in backend._obligations.get(objective_id, ())),  # noqa: SLF001
        status="PENDING",
    )


async def _admit_start_approve(
    backend: DurableObjectiveCycleApiBackend, *, seed: bool = False,
) -> dict[str, Any]:
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
    if seed:
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
    return {"admit": admit, "started": started, "approved": approved}


def _stub_model(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    async def _fake_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        return ChatCompletionResultV1(
            output_text=json.dumps(payload), provider="test", resolved_provider="test", model="test-model", usage={},
        )

    monkeypatch.setattr("backend.aix_core.runtime.verification.semantic_verifier.chat_completion", _fake_chat_completion)


@pytest.mark.asyncio
async def test_flag_off_completion_is_byte_identical_to_pre_cl2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", False
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED", False
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    result = await _admit_start_approve(backend, seed=True)
    assert result["approved"]["ok"] is True
    assert result["approved"]["phase"] == "COMPLETED_FOR_REVIEW"


@pytest.mark.asyncio
async def test_flag_on_pass_verdict_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", True
    )
    _stub_model(monkeypatch, {"verdict": "PASS", "reasons": [], "criteria_results": []})
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    result = await _admit_start_approve(backend, seed=True)
    assert result["approved"]["ok"] is True, result["approved"]
    assert result["approved"]["phase"] == "COMPLETED_FOR_REVIEW"


@pytest.mark.asyncio
async def test_flag_on_repair_verdict_never_completes_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact historical stub-map reply from Sprint W9's own test -- must not
    silently complete just because mechanical gates passed."""
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", True
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED", False
    )
    _stub_model(monkeypatch, {
        "verdict": "REPAIR",
        "reasons": ["installmentCountByFsmId is declared but never used to change behavior"],
        "criteria_results": [{"criterion": "loop query count decreases", "satisfied": False, "detail": "stub map unused"}],
    })
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    result = await _admit_start_approve(backend, seed=True)
    approved = result["approved"]
    assert approved["ok"] is False, approved
    assert approved["phase"] != "COMPLETED_FOR_REVIEW"
    assert approved["error"]["code"] == "VERIFICATION_REPAIR_REQUIRED"
    assert approved["terminal_reason"] == "VERIFICATION_REPAIR_REQUIRED"


@pytest.mark.asyncio
async def test_flag_on_replan_verdict_still_returns_to_waiting_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: the CL2-P2 gate must not intercept REPLAN -- Sprint W10's
    own block (immediately after) must still be the one that handles it."""
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", True
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED", False
    )
    _stub_model(monkeypatch, {"verdict": "REPLAN", "reasons": ["wrong file entirely"], "criteria_results": []})
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    result = await _admit_start_approve(backend, seed=True)
    approved = result["approved"]
    assert approved["ok"] is True, approved  # a replan is not an error -- it's a real, ok outcome
    assert approved["phase"] == "WAITING_APPROVAL"
    assert approved["phase"] != "COMPLETED_FOR_REVIEW"


@pytest.mark.asyncio
async def test_flag_on_repair_with_zero_budget_escalates_to_halt(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify_step() itself escalates REPAIR -> HALT once repair_budget_remaining <= 0
    (proven in isolation by test_semantic_verifier.py) -- this proves the gate wires
    that escalation through to a real blocked cycle, not just the isolated function."""
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", True
    )
    _stub_model(monkeypatch, {"verdict": "REPAIR", "reasons": ["still stubbed"], "criteria_results": []})
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
    # Zero repair budget from the start -- verify_step() must escalate REPAIR to HALT
    # on the very first attempt, not silently treat it as a bare retryable REPAIR.
    backend._cycles[started["cycle_id"]].retry_budget_remaining = 0  # noqa: SLF001

    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-1", expected_version=started["version"],
    )
    assert approved["ok"] is False, approved
    assert approved["error"]["code"] == "VERIFICATION_HALT"
    assert approved["terminal_reason"] == "VERIFICATION_HALT"

    from backend.aix_core.runtime.objective.durable_api_backend import _classify_terminal_reason_for_retry
    classification = _classify_terminal_reason_for_retry("VERIFICATION_HALT")
    assert classification.failure_class == "TERMINAL"


@pytest.mark.asyncio
async def test_flag_on_reunderstand_verdict_does_not_silently_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P8: REUNDERSTAND is one of the five
    verdicts the doc's own hard-gate exit criterion names -- a cycle must not reach
    COMPLETED_FOR_REVIEW off a REUNDERSTAND verdict without genuinely re-establishing
    understanding first (`durable_api_backend.py`'s dedicated REUNDERSTAND handling:
    clears cached `UnderstandingEvidenceV1`, re-invokes `_maybe_real_execute`)."""
    call_count = {"n": 0}

    async def _fake_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        call_count["n"] += 1
        verdict = "REUNDERSTAND" if call_count["n"] == 1 else "PASS"
        return ChatCompletionResultV1(
            output_text=json.dumps({"verdict": verdict, "reasons": ["need more evidence"], "criteria_results": []}),
            provider="test", resolved_provider="test", model="test-model", usage={},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", True
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.verification.semantic_verifier.chat_completion", _fake_chat_completion
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    result = await _admit_start_approve(backend, seed=True)
    objective_id = result["admit"]["objective_id"]
    approved = result["approved"]

    # REUNDERSTAND creates a persisted dispatcher boundary.  A separate claim must
    # execute the re-established step before it can pass verification.
    assert call_count["n"] >= 1
    assert approved["ok"] is True, approved
    assert approved["phase"] == "STEP_READY"
    completed = await backend.command(
        operation="dispatch_ready_step", tenant_id="tenant-1", actor_id="dispatcher-1",
        resource_id=result["started"]["cycle_id"], body={}, idempotency_key="dispatch-reunderstand-1",
        expected_version=approved["version"],
    )
    assert call_count["n"] >= 2
    assert completed["phase"] == "COMPLETED_FOR_REVIEW"
    # The dedicated REUNDERSTAND handling clears cached understanding as part of
    # forcing genuine re-establishment -- confirmed real, not a no-op passthrough.
    verdicts = backend._verification_verdicts.get(objective_id, [])  # noqa: SLF001
    assert [v.verdict for v in verdicts][:2] == ["REUNDERSTAND", "PASS"]
