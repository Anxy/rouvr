"""AIX Closed-Loop Execution Plan, Sprint W9: the work-order-aware independent verifier.

Model calls are stubbed (no live LLM in this test environment, same as W5/W6). What's
under test is real: mechanical-first short-circuiting (no model call when mechanical
checks already fail -- cheaper and matches "reserve budget for verification"), budget-
aware REPAIR-vs-HALT, fail-closed behavior when the model call itself errors, and that
disabling the semantic layer (the default) reproduces exactly the old PASS-on-mechanical
behavior with zero model calls.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1
from backend.infrastructure.model_gateway.hyeve_integration_client import ChatCompletionResultV1
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.verification.semantic_verifier import verify_step


def _contract(**overrides: Any) -> StepContractV1:
    base = dict(
        cycle_id="cyc-1", tenant_id="tenant-1", step_index=0, purpose="batch the N+1 queries",
        target_scope=("FeeDue.php",), expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("loop query count decreases",), validation_commands=("php -l FeeDue.php",),
        assigned_role="executor",
        step_budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1),
        status="PENDING",
    )
    base.update(overrides)
    return StepContractV1(**base)  # type: ignore[arg-type]


def _result(**overrides: Any) -> RealExecuteResult:
    base = dict(
        ok=True, code="REAL_EXECUTE_OK", workspace="ws", objective_status="COMPLETED",
        completion_gates={"gates": {"diff_exists": True, "verification_passed": True}, "verification_verdict": "PASSED"},
        diff_text="+ $installmentCountByFsmId = array();\n",
    )
    base.update(overrides)
    return RealExecuteResult(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_mechanical_failure_short_circuits_no_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def _should_not_be_called(**kwargs: Any) -> Any:
        nonlocal called
        called = True
        raise AssertionError("model must not be called when mechanical checks already failed")

    monkeypatch.setattr("backend.aix_core.runtime.verification.semantic_verifier.chat_completion", _should_not_be_called)
    outcome = await verify_step(
        result=_result(completion_gates={"gates": {"diff_exists": False}, "verification_verdict": "FAILED"}),
        step_contract=_contract(), repair_budget_remaining=2, semantic_verifier_enabled=True,
    )
    assert called is False
    assert outcome.verdict == "REPAIR"
    assert outcome.mechanical_passed is False


@pytest.mark.asyncio
async def test_mechanical_failure_with_no_budget_halts() -> None:
    outcome = await verify_step(
        result=_result(completion_gates={"gates": {"diff_exists": False}, "verification_verdict": "FAILED"}),
        step_contract=_contract(), repair_budget_remaining=0, semantic_verifier_enabled=True,
    )
    assert outcome.verdict == "HALT"


@pytest.mark.asyncio
async def test_semantic_disabled_default_behavior_is_pass_on_mechanical_success() -> None:
    outcome = await verify_step(
        result=_result(), step_contract=_contract(), repair_budget_remaining=2,
        semantic_verifier_enabled=False,
    )
    assert outcome.verdict == "PASS"
    assert outcome.semantic_checked is False


@pytest.mark.asyncio
async def test_exact_literal_diff_is_independently_verified_without_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _should_not_be_called(**kwargs: Any) -> Any:
        raise AssertionError("exact literal diff must not wait on semantic model transport")

    monkeypatch.setattr(
        "backend.aix_core.runtime.verification.semantic_verifier.chat_completion",
        _should_not_be_called,
    )
    contract = _contract(
        purpose='Replace the exact string "durable event spine" with "durable verified event spine".',
        target_scope=("EVENT_SPINE.md",),
        acceptance_criteria=(
            'EVENT_SPINE.md contains "durable verified event spine" in place of the exact string "durable event spine".',
            "Only EVENT_SPINE.md is changed.",
            "No HTML, CSS, or proposal artifacts are created.",
        ),
        # Production planners can omit the typed effect; exact replacement prose
        # plus scoped diff evidence must still avoid a redundant model repair.
        required_effects=(),
    )
    result = _result(
        changed_source_files=({"path": "EVENT_SPINE.md"},),
        diff_text=(
            "@@ -1 +1 @@\n"
            "-durable event spine, matching the pattern\n"
            "+durable verified event spine, matching the pattern\n"
        )
    )

    outcome = await verify_step(
        result=result,
        step_contract=contract,
        repair_budget_remaining=2,
        semantic_verifier_enabled=True,
    )

    assert outcome.verdict == "PASS"
    assert outcome.mechanical_passed is True
    assert outcome.semantic_checked is False


@pytest.mark.asyncio
async def test_semantic_layer_catches_stub_pattern_and_requests_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        return ChatCompletionResultV1(
            output_text=json.dumps({
                "verdict": "REPAIR",
                "reasons": ["installmentCountByFsmId is declared but never used to change behavior"],
                "criteria_results": [
                    {"criterion": "loop query count decreases", "satisfied": False, "detail": "stub map unused"}
                ],
            }),
            provider="test", resolved_provider="test", model="test-model", usage={},
        )

    monkeypatch.setattr("backend.aix_core.runtime.verification.semantic_verifier.chat_completion", _fake_chat_completion)
    outcome = await verify_step(
        result=_result(), step_contract=_contract(), repair_budget_remaining=2,
        semantic_verifier_enabled=True,
    )
    assert outcome.verdict == "REPAIR"
    assert outcome.semantic_checked is True
    assert "never used" in outcome.reasons[0]
    assert outcome.criteria_results[0]["satisfied"] is False


@pytest.mark.asyncio
async def test_semantic_layer_pass_when_model_says_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        return ChatCompletionResultV1(
            output_text=json.dumps({"verdict": "PASS", "reasons": [], "criteria_results": []}),
            provider="test", resolved_provider="test", model="test-model", usage={},
        )

    monkeypatch.setattr("backend.aix_core.runtime.verification.semantic_verifier.chat_completion", _fake_chat_completion)
    outcome = await verify_step(
        result=_result(), step_contract=_contract(), repair_budget_remaining=2,
        semantic_verifier_enabled=True,
    )
    assert outcome.verdict == "PASS"
    assert outcome.semantic_checked is True


@pytest.mark.asyncio
async def test_governed_mkdir_action_bypasses_semantic_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _should_not_be_called(**kwargs: Any) -> Any:
        raise AssertionError("governed mkdir verification must not require semantic model")

    monkeypatch.setattr("backend.aix_core.runtime.verification.semantic_verifier.chat_completion", _should_not_be_called)
    contract = _contract(
        purpose="Create empty directory marker gaef001-scratch/new_module_e/.gitkeep",
        target_scope=("gaef001-scratch/new_module_e/.gitkeep",),
        acceptance_criteria=(
            "gaef001-scratch/new_module_e/.gitkeep exists",
            "gaef001-scratch/new_module_e is a directory",
            "no existing source files are modified",
        ),
        actions=(
            {
                "capability": "filesystem",
                "operation": "mkdir",
                "arguments": {"path": "gaef001-scratch/new_module_e/.gitkeep", "marker": True},
                "expected_observation": {"exists": True},
            },
        ),
    )
    result = _result(
        executor="objective_cycle.real_execute.action_request.v1",
        changed_source_files=({"path": "gaef001-scratch/new_module_e/.gitkeep"},),
        diff_text="--- /dev/null\n+++ b/gaef001-scratch/new_module_e/.gitkeep\n",
    )
    outcome = await verify_step(
        result=result,
        step_contract=contract,
        repair_budget_remaining=2,
        semantic_verifier_enabled=True,
    )
    assert outcome.verdict == "PASS"
    assert outcome.semantic_checked is False


@pytest.mark.asyncio
async def test_governed_rename_action_bypasses_semantic_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _should_not_be_called(**kwargs: Any) -> Any:
        raise AssertionError("governed rename verification must not require semantic model")

    monkeypatch.setattr("backend.aix_core.runtime.verification.semantic_verifier.chat_completion", _should_not_be_called)
    contract = _contract(
        purpose="Rename gaef001-scratch/banner_d.png to gaef001-scratch/test_d.png",
        target_scope=("gaef001-scratch/banner_d.png", "gaef001-scratch/test_d.png"),
        acceptance_criteria=(
            "gaef001-scratch/test_d.png exists",
            "gaef001-scratch/banner_d.png no longer exists",
            "no other files are modified",
        ),
        actions=(
            {
                "capability": "filesystem",
                "operation": "rename",
                "arguments": {
                    "source": "gaef001-scratch/banner_d.png",
                    "destination": "gaef001-scratch/test_d.png",
                },
                "expected_observation": {"source_exists": False, "destination_exists": True},
            },
        ),
    )
    result = _result(
        executor="objective_cycle.real_execute.action_request.v1",
        changed_source_files=(
            {"path": "gaef001-scratch/banner_d.png"},
            {"path": "gaef001-scratch/test_d.png"},
        ),
        diff_text=(
            "--- /dev/null\n+++ b/gaef001-scratch/test_d.png\n"
            "--- a/gaef001-scratch/banner_d.png\n+++ /dev/null\n"
        ),
    )
    outcome = await verify_step(
        result=result,
        step_contract=contract,
        repair_budget_remaining=2,
        semantic_verifier_enabled=True,
    )
    assert outcome.verdict == "PASS"
    assert outcome.semantic_checked is False


@pytest.mark.asyncio
async def test_model_error_fails_closed_not_silent_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _bad_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        return ChatCompletionResultV1(
            output_text="not json", provider="test", resolved_provider="test", model="test-model", usage={},
        )

    monkeypatch.setattr("backend.aix_core.runtime.verification.semantic_verifier.chat_completion", _bad_chat_completion)
    outcome = await verify_step(
        result=_result(), step_contract=_contract(), repair_budget_remaining=2,
        semantic_verifier_enabled=True,
    )
    assert outcome.verdict != "PASS"
    assert outcome.verdict == "REPAIR"
    assert outcome.semantic_checked is False


@pytest.mark.asyncio
async def test_replan_verdict_passes_through_uncapped_by_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        return ChatCompletionResultV1(
            output_text=json.dumps({
                "verdict": "REPLAN", "reasons": ["wrong file entirely"], "criteria_results": [],
            }),
            provider="test", resolved_provider="test", model="test-model", usage={},
        )

    monkeypatch.setattr("backend.aix_core.runtime.verification.semantic_verifier.chat_completion", _fake_chat_completion)
    outcome = await verify_step(
        result=_result(), step_contract=_contract(), repair_budget_remaining=0,
        semantic_verifier_enabled=True,
    )
    assert outcome.verdict == "REPLAN"


@pytest.mark.asyncio
async def test_read_only_empty_diff_passes_without_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _should_not_be_called(**kwargs: Any) -> Any:
        raise AssertionError("read-only evidence must not be judged as an empty mutation diff")

    monkeypatch.setattr(
        "backend.aix_core.runtime.verification.semantic_verifier.chat_completion",
        _should_not_be_called,
    )
    contract = _contract(
        purpose="Read-only summary of backend/",
        target_scope=(".",),
        expected_output_schema_ref="text",
        acceptance_criteria=("Return factual summary for hey can you give me summary of backend",),
        required_effects=(),
    )
    result = _result(
        diff_text="",
        completion_gates=None,
        artifacts={"directory_listing": "listing.json"},
        evidence_refs=["file://listing.json"],
        target_files_modified=0,
        real_operations=1,
        detail="real directory inspection: . (12 entries)",
    )
    outcome = await verify_step(
        result=result,
        step_contract=contract,
        repair_budget_remaining=2,
        semantic_verifier_enabled=True,
    )
    assert outcome.verdict == "PASS"
    assert outcome.semantic_checked is False
    assert "no mutation diff required" in " ".join(outcome.reasons)


@pytest.mark.asyncio
async def test_mutation_empty_diff_still_fails_without_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _should_not_be_called(**kwargs: Any) -> Any:
        raise AssertionError("empty mutation diffs must fail mechanically, not via the model")

    monkeypatch.setattr(
        "backend.aix_core.runtime.verification.semantic_verifier.chat_completion",
        _should_not_be_called,
    )
    outcome = await verify_step(
        result=_result(diff_text="", target_files_modified=0, completion_gates=None),
        step_contract=_contract(),
        repair_budget_remaining=2,
        semantic_verifier_enabled=True,
    )
    assert outcome.verdict == "REPAIR"
    assert outcome.mechanical_passed is False
    assert "empty diff" in " ".join(outcome.reasons).lower()
