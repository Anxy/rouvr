"""AIX-MPE-003 Packet MPE-08: executor profile reaches the planner prompt."""
from __future__ import annotations

import json
from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, ExecutorProfileV1, UnderstandingEvidenceV1
from backend.aix_core.runtime.executor.executor_profile import default_executor_profile
from backend.infrastructure.model_gateway.hyeve_integration_client import ChatCompletionResultV1
from backend.aix_core.runtime.planner.step_contract_planner import propose_step_contract


def test_default_profile_is_model_independent() -> None:
    profile = default_executor_profile()
    assert isinstance(profile, ExecutorProfileV1)
    assert profile.executor_id == "aix.real_execute.v1"
    dumped = json.dumps(profile.model_dump())
    assert "gpt" not in dumped.lower()
    assert "claude" not in dumped.lower()
    assert "kimi" not in dumped.lower()


@pytest.mark.asyncio
async def test_planner_prompt_includes_executor_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    async def _fake_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        captured["system"] = kwargs["messages"][0].content
        return ChatCompletionResultV1(
            output_text=json.dumps({
                "reply": "ok", "needs_more_evidence": False,
                "purpose": "edit FeeDue.php", "target_scope": ["FeeDue.php"],
                "acceptance_criteria": ["x"], "validation_commands": ["php -l FeeDue.php"],
            }),
            provider="test", resolved_provider="test", model="test-model", usage={},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion", _fake_chat_completion
    )
    await propose_step_contract(
        objective="refactor FeeDue.php",
        definition_of_done=["batched queries"],
        understanding=UnderstandingEvidenceV1(
            objective_id="obj-1", cycle_id="cyc-1", files_read=("FeeDue.php",),
            observed_problem="n+1", understanding_complete=True,
        ),
        constraints=[],
        tenant_id="tenant-1", cycle_id="cyc-1", step_index=0, assigned_role="executor",
        step_budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1),
        actor_id="human-1", authorization_header="", timeout_seconds=5.0,
    )
    assert "executor_profile" in captured["system"]
    assert "aix.real_execute.v1" in captured["system"]
    assert "supports_multi_file" in captured["system"]
