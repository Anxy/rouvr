"""Greenfield HTML create uses the LLM planner + content.generate_html_css path."""
from __future__ import annotations

import json
from typing import Any

import pytest

from backend.aix_core.runtime.executor.real_execute import (
    _step_requests_html_css_generation,
    looks_like_page_generation_objective,
)
from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
from backend.aix_core.runtime.objective.contracts import (
    ActionRequestV1,
    BudgetV1,
    StepContractV1,
    UnderstandingEvidenceV1,
)
from backend.infrastructure.model_gateway.hyeve_integration_client import ChatCompletionResultV1
from backend.infrastructure.model_gateway.intelligence_layer.prompts import (
    build_step_contract_planner_evidence,
)
from backend.aix_core.runtime.planner.step_contract_planner import (
    _is_cycle_workspace_proposal_path,
    _target_scope_grounded,
    propose_step_contract,
)

OBJECTIVE = "Create an HTML login page for AIX."


def test_html_login_page_is_page_generation() -> None:
    assert looks_like_page_generation_objective(OBJECTIVE) is True


def test_content_generate_html_css_is_registered() -> None:
    resolved = resolve_capability("content", "generate_html_css")
    assert resolved is not None
    assert resolved[0] == "content"
    assert resolved[1] == "generate_html_css"


def test_proposed_paths_are_not_auto_grounded() -> None:
    assert _is_cycle_workspace_proposal_path("proposed") is True
    assert _is_cycle_workspace_proposal_path("proposed/index.html") is True
    assert _is_cycle_workspace_proposal_path("src/app/login.tsx") is False
    assert (
        _target_scope_grounded(
            "proposed",
            files_read=(),
            symbols_found=(),
            constraints=[],
            file_excerpts={},
        )
        is False
    )
    assert (
        _target_scope_grounded(
            "proposed/index.html",
            files_read=(),
            plan_target_paths=("proposed/index.html",),
        )
        is True
    )


def test_legacy_evidence_bag_does_not_flag_greenfield() -> None:
    evidence = build_step_contract_planner_evidence(
        objective=OBJECTIVE,
        definition_of_done=[OBJECTIVE],
        files_read=[],
        symbols_found=[],
        observed_problem=(
            "GREENFIELD_PAGE_GENERATION: No repository source file is bound. "
            "Create via content.generate_html_css into proposed/."
        ),
        constraints=[],
    )
    assert "greenfield_page_generation" not in evidence
    assert "proposal_output_root" not in evidence


def test_step_with_generate_action_routes_to_llm_module() -> None:
    contract = StepContractV1(
        cycle_id="cyc-1",
        tenant_id="t1",
        step_index=0,
        purpose="Generate HTML/CSS login page into proposed/",
        target_scope=("proposed",),
        expected_output_schema_ref="aix.step_contract.v1.html_css_proposal",
        acceptance_criteria=("proposed/index.html exists",),
        validation_commands=("echo ok",),
        assigned_role="executor",
        step_budget=BudgetV1(
            time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1
        ),
        actions=(
            ActionRequestV1(
                capability="content",
                operation="generate_html_css",
                arguments={},
            ),
        ),
        status="PENDING",
    )
    assert _step_requests_html_css_generation(contract, OBJECTIVE) is True


@pytest.mark.asyncio
async def test_planner_without_plan_targets_rejects_proposed_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CREATE of proposed/ requires an explicit PlanTarget. Parser will not auto-ground it."""

    payload = {
        "reply": "Greenfield HTML login page via LLM codegen into proposed/.",
        "needs_more_evidence": False,
        "steps": [
            {
                "purpose": "Generate HTML/CSS AIX login page into cycle workspace proposed/",
                "target_scope": ["proposed"],
                "acceptance_criteria": [
                    "proposed/index.html exists",
                    "proposed/styles.css exists",
                ],
                "validation_commands": ["echo status-check-ok"],
                "prohibited_actions": ["do not mutate repository source"],
                "obligation_refs": [],
                "actions": [
                    {
                        "capability": "content",
                        "operation": "generate_html_css",
                        "arguments": {},
                    }
                ],
                "required_effects": [],
                "depends_on": [],
                "risk_tier": "LOW",
            }
        ],
    }

    async def _fake_chat_completion(**_kwargs: Any) -> ChatCompletionResultV1:
        return ChatCompletionResultV1(
            output_text=json.dumps(payload),
            provider="test",
            resolved_provider="test",
            model="test-model",
            usage={},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        _fake_chat_completion,
    )

    understanding = UnderstandingEvidenceV1(
        objective_id="obj-html",
        cycle_id="cyc-html",
        files_read=(),
        observed_problem=(
            "GREENFIELD_PAGE_GENERATION: No repository source file is bound. "
            "Create via content.generate_html_css into proposed/."
        ),
        understanding_complete=True,
    )
    proposal = await propose_step_contract(
        objective=OBJECTIVE,
        definition_of_done=[OBJECTIVE],
        understanding=understanding,
        constraints=[],
        tenant_id="t1",
        cycle_id="cyc-html",
        step_index=0,
        assigned_role="executor",
        step_budget=BudgetV1(
            time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1
        ),
        actor_id="actor",
        authorization_header="",
        timeout_seconds=30.0,
    )
    assert proposal.contract is None
    assert proposal.reason is not None
    assert "TARGET_SCOPE_NOT_GROUNDED_IN_EVIDENCE" in proposal.reason
