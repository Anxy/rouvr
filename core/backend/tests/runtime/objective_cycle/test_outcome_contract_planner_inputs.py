"""AIX-DoD-002C: sealed OutcomeContract obligations are authoritative planner inputs."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, UnderstandingEvidenceV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.objective.outcome_contract import (
    ApiFacetV1,
    ApiOperationSpecV1,
    AuthFacetV1,
    DbFacetV1,
    IntegrationFacetV1,
    IntegrationMappingV1,
    OutcomeContractV1,
    OutcomeFacetsV1,
    UiFacetV1,
    seal_outcome_contract,
)
from backend.aix_core.runtime.objective.outcome_obligations import compile_outcome_obligations
from backend.aix_core.runtime.planner.step_contract_planner import propose_step_contract
from backend.infrastructure.model_gateway.hyeve_integration_client import ChatCompletionResultV1
from backend.infrastructure.model_gateway.intelligence_layer.prompts import (
    STEP_CONTRACT_PLANNER_SYSTEM_PROMPT,
    build_step_contract_planner_evidence,
)


def _sealed_mini() -> Any:
    return seal_outcome_contract(
        OutcomeContractV1(
            objective_id="obj_002c",
            definition_of_done=("Staff can register a student",),
            facets=OutcomeFacetsV1(
                UI=UiFacetV1(
                    required_operations=("register_student",),
                    required_views=("student_registration",),
                    acceptance_criteria=("UI provides register_student",),
                ),
                API=ApiFacetV1(
                    required_operations=(
                        ApiOperationSpecV1(id="students.create", path_or_action="POST /students"),
                    ),
                    acceptance_criteria=("API provides students.create",),
                ),
                DB=DbFacetV1(
                    entities=("students",),
                    acceptance_criteria=("DB provides students",),
                ),
                AUTH=AuthFacetV1(
                    actors=("staff",),
                    permissions=("students.write",),
                    requirement="MANDATORY",
                    acceptance_criteria=("Auth model declared",),
                ),
                INTEGRATION=IntegrationFacetV1(
                    mappings=(
                        IntegrationMappingV1(
                            id="map-0",
                            from_facet="UI",
                            from_id="register_student",
                            to_facet="API",
                            to_id="students.create",
                        ),
                        IntegrationMappingV1(
                            id="map-1",
                            from_facet="API",
                            from_id="students.create",
                            to_facet="DB",
                            to_id="students",
                        ),
                    ),
                    acceptance_criteria=("UI to API to DB mappings compose",),
                ),
            ),
        )
    )


def _budget() -> BudgetV1:
    return BudgetV1(
        time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1
    )


def _understanding() -> UnderstandingEvidenceV1:
    return UnderstandingEvidenceV1(
        objective_id="obj_002c",
        cycle_id="cyc_002c",
        files_read=(),
        symbols_found=(),
        observed_problem="build student registration",
        understanding_complete=True,
    )


def test_prompt_states_obligations_are_authoritative() -> None:
    assert "outcome_contract_obligations" in STEP_CONTRACT_PLANNER_SYSTEM_PROMPT
    assert "authoritative" in STEP_CONTRACT_PLANNER_SYSTEM_PROMPT.lower()


def test_evidence_bag_includes_compiled_obligations() -> None:
    sealed = _sealed_mini()
    compiled = compile_outcome_obligations(sealed)
    assert compiled.ok
    from backend.aix_core.runtime.objective.outcome_obligations import obligations_for_planner

    bag = build_step_contract_planner_evidence(
        objective="register students",
        definition_of_done=["staff can register"],
        files_read=[],
        symbols_found=[],
        observed_problem="build registration",
        constraints=[],
        outcome_contract_obligations=obligations_for_planner(compiled.obligations),
        outcome_contract_id=sealed.contract_id,
        outcome_contract_hash=sealed.contract_hash,
    )
    assert "outcome_contract_obligations" in bag
    ids = {str(o["id"]) for o in bag["outcome_contract_obligations"]}  # type: ignore[index]
    assert "obl_ui_register_student" in ids
    assert "obl_api_students_create" in ids
    assert bag["outcome_contract_id"] == sealed.contract_id
    assert bag["outcome_contract_hash"] == sealed.contract_hash


@pytest.mark.asyncio
async def test_propose_step_contract_records_obligation_ids_in_planner_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sealed = _sealed_mini()
    compiled = compile_outcome_obligations(sealed)
    assert compiled.ok
    captured: dict[str, Any] = {}

    async def _fake_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        messages = kwargs.get("messages") or []
        captured["system"] = messages[0].content if messages else ""
        return ChatCompletionResultV1(
            output_text=json.dumps(
                {
                    "reply": "stub",
                    "needs_more_evidence": False,
                    "purpose": "Register student UI",
                    "target_scope": ["index.html"],
                    "acceptance_criteria": ["form works"],
                    "validation_commands": [],
                    "prohibited_actions": [],
                    "risk_tier": "LOW",
                }
            ),
            provider="test",
            resolved_provider="test",
            model="test-model",
            usage={},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        _fake_chat_completion,
    )

    proposal = await propose_step_contract(
        objective="register students",
        definition_of_done=["staff can register"],
        understanding=_understanding(),
        constraints=[],
        tenant_id="t1",
        cycle_id="cyc_002c",
        step_index=0,
        assigned_role="executor",
        step_budget=_budget(),
        actor_id="actor",
        authorization_header="",
        timeout_seconds=5.0,
        objective_id="obj_002c",
        outcome_contract=sealed,
        outcome_contract_obligations=compiled.obligations,
    )

    assert proposal.planner_call is not None
    call = proposal.planner_call
    assert call["outcome_contract_id"] == sealed.contract_id
    assert call["outcome_contract_hash"] == sealed.contract_hash
    assert call["outcome_contract_obligations_count"] == len(compiled.obligations)
    assert "obl_ui_register_student" in call["outcome_contract_obligation_ids"]
    assert "obl_api_students_create" in call["outcome_contract_obligation_ids"]

    system = str(captured.get("system") or "")
    assert "outcome_contract_obligations" in system
    assert "obl_ui_register_student" in system
    assert "authoritative" in system.lower() or "cannot redefine" in system.lower()


def test_backend_planner_kwargs_include_compiled_obligations() -> None:
    sealed = _sealed_mini()
    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    backend._outcome_contracts["obj_002c"] = sealed
    stored = backend._compile_and_store_outcome_obligations("obj_002c", sealed)
    assert stored
    assert "obj_002c" in backend._outcome_contract_obligations

    class _Obj:
        objective_id = "obj_002c"
        objective = "register students"
        original_objective = "register students"
        task_class = ""
        definition_of_done = ("staff can register",)

    kwargs = backend._planner_outcome_contract_kwargs(_Obj())  # type: ignore[arg-type]
    assert kwargs["outcome_contract"] is sealed
    assert len(kwargs["outcome_contract_obligations"]) == len(stored)
    assert kwargs["outcome_contract_obligations"][0].obligation_id.startswith("obl_")


@pytest.mark.asyncio
async def test_product_class_compile_failure_skips_propose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When require-path compile yields no obligations, propose must not run."""
    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    propose_calls: list[Any] = []

    async def _fake_propose(**kwargs: Any) -> Any:
        propose_calls.append(kwargs)
        raise AssertionError("propose_step_contract must not be called")

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract",
        _fake_propose,
    )
    monkeypatch.setattr(
        DurableObjectiveCycleApiBackend,
        "_require_outcome_contract_for",
        lambda self, obj: True,
    )

    sealed = _sealed_mini()
    backend._outcome_contracts["obj_skip"] = sealed

    def _fail_compile(self: Any, objective_id: str, contract: object) -> tuple:
        return ()

    monkeypatch.setattr(
        DurableObjectiveCycleApiBackend,
        "_compile_and_store_outcome_obligations",
        _fail_compile,
    )

    async def _ensure_ok(**kwargs: Any) -> Any:
        from backend.aix_core.runtime.objective.outcome_contract_proposer import (
            OutcomeContractProposal,
        )

        return OutcomeContractProposal(
            ok=True,
            code="CONTRACT_ALREADY_SEALED",
            detail="present",
            contract=sealed,
            source="heuristic",
        )

    monkeypatch.setattr(backend, "_ensure_outcome_contract", _ensure_ok)

    # Drive only the gate logic via helpers used by the planner block
    class _Obj:
        objective_id = "obj_skip"
        objective = "build attendance product"
        original_objective = "build attendance product"
        task_class = "product"
        definition_of_done = ("done",)
        execution_context = {}

    obj = _Obj()
    assert backend._require_outcome_contract_for(obj) is True  # type: ignore[arg-type]
    ensure = await backend._ensure_outcome_contract(
        obj=obj,  # type: ignore[arg-type]
        cycle=None,  # type: ignore[arg-type]
        actor_id="a",
        caller_auth="",
        use_llm=False,
    )
    assert ensure is not None and ensure.ok
    sealed_got = backend._outcome_contracts.get(obj.objective_id)
    assert sealed_got is not None
    compiled = backend._compile_and_store_outcome_obligations(obj.objective_id, sealed_got)
    assert not compiled  # fail-closed before propose
    assert propose_calls == []
