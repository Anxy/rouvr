"""C040 planner proof tests."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.infrastructure.model_gateway.hyeve_integration_client import ChatCompletionResultV1
from backend.aix_core.planner_proof.adversarial import ADVERSARIAL_CASES, assert_adversarial_refusal
from backend.aix_core.planner_proof.fallback_policy import apply_fallback_policy
from backend.aix_core.planner_proof.fixtures import representative_multi_obligation_fixture
from backend.aix_core.planner_proof.live_runner import run_planner_proof
from backend.aix_core.planner_proof.provenance import (
    PLANNER_PROMPT_ID,
    body_hash,
    capture_prompt_provenance,
)
from backend.aix_core.runtime.planner.step_contract_planner import propose_step_contract


def test_prompt_provenance_captures_hash_model_and_usage() -> None:
    body = "system prompt body"
    prov = capture_prompt_provenance(
        prompt_body=body,
        model_ref="planner-model",
        provider="test",
        resolved_provider="test",
        usage={"prompt_tokens": 10, "completion_tokens": 20, "cost_usd": 0.01},
    )
    assert prov.prompt_id == PLANNER_PROMPT_ID
    assert prov.prompt_body_hash == body_hash(body)
    assert prov.usage["cost_usd"] == 0.01


def test_fallback_never_allows_synthetic_plan() -> None:
    d = apply_fallback_policy(reason="PLANNER_MODEL_ERROR:timeout", plan_ok=False, retry_count=0)
    assert d.allow_synthetic_plan is False
    assert d.disposition == "FAIL_CLOSED"
    d2 = apply_fallback_policy(reason="NEEDS_MORE_EVIDENCE", plan_ok=False, retry_count=0)
    assert d2.disposition == "RETRY_BOUNDED"
    d3 = apply_fallback_policy(reason="NEEDS_MORE_EVIDENCE", plan_ok=False, retry_count=1)
    assert d3.disposition == "REQUIRE_REPLAN"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ADVERSARIAL_CASES, ids=[c["id"] for c in ADVERSARIAL_CASES])
async def test_adversarial_schema_refusals(monkeypatch: pytest.MonkeyPatch, case: dict[str, Any]) -> None:
    fx = representative_multi_obligation_fixture()

    async def _fake(**_kwargs: Any) -> ChatCompletionResultV1:
        text = case["payload"]
        if not isinstance(text, str):
            text = json.dumps(text)
        return ChatCompletionResultV1(
            output_text=text,
            provider="test",
            resolved_provider="test",
            model="test-model",
            usage={"prompt_tokens": 1},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        _fake,
    )
    proposal = await propose_step_contract(
        objective=fx.objective,
        definition_of_done=fx.definition_of_done,
        understanding=fx.understanding,
        constraints=fx.constraints,
        tenant_id=fx.tenant_id,
        cycle_id=fx.cycle_id,
        step_index=0,
        assigned_role="executor",
        step_budget=fx.budget,
        actor_id="human-1",
        authorization_header="Bearer t",
        timeout_seconds=5.0,
        obligations=fx.obligations,
        objective_id=fx.objective_id,
    )
    assert_adversarial_refusal(case_id=case["id"], reason=proposal.reason)
    assert proposal.provenance is not None
    assert proposal.provenance.prompt_body_hash


@pytest.mark.asyncio
async def test_production_path_multi_step_plan_with_cost_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production integration path: real propose_step_contract + quality gate + evidence.

    Uses a recorded Integration-shaped reply (same client boundary as live) so CI
    proves the production stack without requiring a live LLM. Opt into real live
    calls with AIX_LIVE_PLANNER_PROOF=1 separately.
    """
    fx = representative_multi_obligation_fixture()

    async def _fake(**_kwargs: Any) -> ChatCompletionResultV1:
        return ChatCompletionResultV1(
            output_text=json.dumps(fx.recorded_multi_step_payload),
            provider="integration",
            resolved_provider="integration",
            model="planner-live-fixture",
            usage={"prompt_tokens": 120, "completion_tokens": 80, "cost_usd": 0.02},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        _fake,
    )
    result = await run_planner_proof(fixture=fx)
    assert result.proposal.plan is not None
    assert len(result.proposal.plan.steps) >= 2
    assert result.quality_ok is True
    assert result.fallback.disposition == "ACCEPT_PLAN"
    assert result.fallback.allow_synthetic_plan is False
    evidence = result.evidence
    assert evidence["schema"] == "aix.planner_proof.evidence.v1"
    assert evidence["plan_step_count"] >= 2
    assert evidence["provenance"]["model_ref"] == "planner-live-fixture"
    assert evidence["provenance"]["usage"]["cost_usd"] == 0.02
    assert evidence["provenance"]["prompt_id"] == PLANNER_PROMPT_ID
