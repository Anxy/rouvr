"""Planner → PlanTargets → provenance contract under model failure."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, UnderstandingEvidenceV1
from backend.aix_core.runtime.objective.provenance.admit import freeze_user_intent
from backend.aix_core.runtime.objective.provenance.contracts import (
    ClaimCite,
    EvidenceProvenance,
    ExplorationEvidence,
    ExplorationNeed,
    UnderstandingClaim,
    UnderstandingRevision,
)
from backend.aix_core.runtime.objective.provenance.plan_request import build_plan_request, default_governance
from backend.aix_core.runtime.planner.step_contract_planner import propose_step_contract
from backend.infrastructure.model_gateway.hyeve_integration_client import ChatCompletionResultV1


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=4, concurrency=1)


def _cite(kind: str, ref: str) -> ClaimCite:
    return ClaimCite(kind=kind, ref=ref)  # type: ignore[arg-type]


def _prov() -> EvidenceProvenance:
    return EvidenceProvenance(tool="read_file", query="config.py", fingerprint="fp_cfg")


def _refactor_chain() -> tuple[Any, ...]:
    intent = freeze_user_intent(
        objective_id="obj_refactor",
        text=(
            "Add a configurable retry limit, make the service consume it, "
            "expose it through the existing jobs API, add tests, and "
            "preserve the existing public interface."
        ),
    )
    outcome = UnderstandingClaim(
        id="cl_outcome",
        kind="OUTCOME",
        text="Retry limit is configurable end-to-end",
        standing="ASSERTED",
        status="ACTIVE",
        cites=(_cite("INTENT_SPAN", intent.id),),
    )
    missing = UnderstandingClaim(
        id="cl_missing",
        kind="MISSING_INFO",
        text="Where is retry configuration implemented?",
        standing="ASSERTED",
        status="ACTIVE",
        cites=(_cite("CLAIM", outcome.id),),
    )
    understanding = UnderstandingRevision(
        objective_id="obj_refactor",
        intent_id=intent.id,
        scope_mode="OPEN_IMPLEMENTATION",
        claims=(outcome, missing),
    )
    need = ExplorationNeed(
        objective_id="obj_refactor",
        understanding_revision_id=understanding.id,
        claim_ids=(missing.id,),
        purpose="locate retry configuration",
        question="where",
        source="repository",
        result_type="file_content",
        budget_files=8,
    )
    evidence = (
        ExplorationEvidence(
            id="ev_config",
            need_id=need.id,
            objective_id="obj_refactor",
            source="repository",
            location="config.py",
            observation="Repository read grounded planner evidence for config.py",
            exists=True,
            provenance=_prov(),
        ),
        ExplorationEvidence(
            id="ev_service",
            need_id=need.id,
            objective_id="obj_refactor",
            source="repository",
            location="service.py",
            observation="Repository read grounded planner evidence for service.py",
            exists=True,
            provenance=_prov(),
        ),
        ExplorationEvidence(
            id="ev_router",
            need_id=need.id,
            objective_id="obj_refactor",
            source="repository",
            location="router.py",
            observation="Repository read grounded planner evidence for router.py",
            exists=True,
            provenance=_prov(),
        ),
        ExplorationEvidence(
            id="ev_tests",
            need_id=need.id,
            objective_id="obj_refactor",
            source="repository",
            location="tests/test_service.py",
            observation="Repository read grounded planner evidence for tests/test_service.py",
            exists=True,
            provenance=_prov(),
        ),
    )
    gov = default_governance(objective_id="obj_refactor", task_class="code_change", budgets=_budget())
    request = build_plan_request(
        cycle_id="cyc_refactor",
        intent=intent,
        understanding=understanding,
        evidence=evidence,
        governance=gov,
    )
    snapshot = UnderstandingEvidenceV1(
        objective_id="obj_refactor",
        cycle_id="cyc_refactor",
        files_read=tuple(item.location for item in evidence),
        observed_problem="OUTCOME: retry limit objective; evidence grounded",
        understanding_complete=True,
    )
    return intent, understanding, need, evidence, gov, request, snapshot


@pytest.mark.asyncio
async def test_model_error_compiles_existing_evidence_plan_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refactor objectives must not BLOCK on PROVENANCE_SEED_FORBIDDEN when evidence exists."""
    intent, understanding, need, evidence, gov, request, snapshot = _refactor_chain()
    calls = {"n": 0}

    async def _bad_chat_completion(**_kwargs: Any) -> ChatCompletionResultV1:
        calls["n"] += 1
        return ChatCompletionResultV1(
            output_text="not-json",
            provider="test",
            resolved_provider="test",
            model="test-model",
            usage={},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        _bad_chat_completion,
    )
    proposal = await propose_step_contract(
        objective=intent.text,
        definition_of_done=["Retry limit is configurable and tested"],
        understanding=snapshot,
        constraints=[],
        tenant_id="t1",
        cycle_id="cyc_refactor",
        step_index=0,
        assigned_role="executor",
        step_budget=_budget(),
        actor_id="actor",
        authorization_header="",
        timeout_seconds=5.0,
        plan_request=request,
        intent=intent,
        understanding_revision=understanding,
        needs=(need,),
        exploration_evidence=evidence,
        governance=gov,
    )
    assert calls["n"] == 0, "deterministic existing-evidence plan skips LLM when evidence is grounded"
    assert proposal.reason is None, proposal.reason
    assert proposal.provenance_decision == "ACCEPT"
    assert proposal.contract is not None
    assert proposal.provenance_plan is not None
    target_paths = {t.path for t in proposal.provenance_plan.targets}
    assert target_paths == {
        "config.py",
        "service.py",
        "router.py",
        "tests/test_service.py",
    }
    assert all(t.kind == "EXISTING" for t in proposal.provenance_plan.targets)
    assert proposal.planner_call is not None
    assert proposal.planner_call.get("existing_evidence_plan_bootstrap") is True
    assert not str(proposal.reason or "").startswith("PROVENANCE_SEED_FORBIDDEN")


@pytest.mark.asyncio
async def test_model_retry_recovers_valid_plan_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    intent, understanding, need, evidence, gov, request, snapshot = _refactor_chain()
    valid = {
        "reply": "Implement retry limit across cited files",
        "needs_more_evidence": False,
        "targets": [
            {
                "kind": "EXISTING",
                "path": "config.py",
                "effect": "LITERAL_REPLACE",
                "rationale": "add retry limit setting",
                "evidence_id": "ev_config",
                "outcome_claim_ids": ["cl_outcome"],
            }
        ],
        "steps": [
            {
                "purpose": "Add retry limit configuration",
                "target_scope": ["config.py"],
                "acceptance_criteria": ["retry limit is configurable"],
                "validation_commands": ["pytest tests/test_service.py -q"],
                "required_effects": [{"kind": "LITERAL_REPLACE"}],
                "depends_on": [],
                "risk_tier": "LOW",
            }
        ],
    }
    payloads = ["{bad", json.dumps(valid)]

    async def _flaky_chat_completion(**_kwargs: Any) -> ChatCompletionResultV1:
        raw = payloads.pop(0)
        return ChatCompletionResultV1(
            output_text=raw,
            provider="test",
            resolved_provider="test",
            model="test-model",
            usage={},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        _flaky_chat_completion,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner._try_existing_evidence_plan_under_provenance",
        lambda **_kwargs: None,
    )
    proposal = await propose_step_contract(
        objective=intent.text,
        definition_of_done=[],
        understanding=snapshot,
        constraints=[],
        tenant_id="t1",
        cycle_id="cyc_refactor",
        step_index=0,
        assigned_role="executor",
        step_budget=_budget(),
        actor_id="actor",
        authorization_header="",
        timeout_seconds=5.0,
        plan_request=request,
        intent=intent,
        understanding_revision=understanding,
        needs=(need,),
        exploration_evidence=evidence,
        governance=gov,
    )
    assert proposal.reason is None, proposal.reason
    assert proposal.provenance_decision == "ACCEPT"
    assert proposal.contract is not None
    assert proposal.contract.target_scope == ("config.py",)
    ops = {t.get("op") for t in proposal.transformations}
    assert "planner_model_retry" in ops
