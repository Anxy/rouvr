"""AIX-DoD-002D: coherent StepContracts from sealed outcome obligations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, UnderstandingEvidenceV1
from backend.aix_core.runtime.objective.obligation_step_seed import (
    compile_obligation_step_seed,
    plan_omits_ui_obligations,
    should_prefer_obligation_seed,
)
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
from backend.aix_core.runtime.objective.outcome_contract_proposer import seal_from_raw_proposal
from backend.aix_core.runtime.objective.outcome_obligations import compile_outcome_obligations
from backend.aix_core.runtime.planner.step_contract_planner import (
    materialize_obligation_seed_steps,
    propose_step_contract,
)
from backend.infrastructure.model_gateway.hyeve_integration_client import ChatCompletionResultV1


def _attendance_sealed() -> OutcomeContractV1:
    artifact = (
        Path(__file__).resolve().parents[3]
        / ".tmp_llm_vs_heuristic_attendance.json"
    )
    if artifact.is_file():
        raw = json.loads(artifact.read_text(encoding="utf-8"))["llm_no_fallback"]["raw"]
        proposal = seal_from_raw_proposal(
            objective_id="obj_att_002d",
            raw=raw,
            source="llm",
            need_auth=True,
        )
        assert proposal.ok and proposal.contract is not None
        return proposal.contract

    m1 = IntegrationMappingV1(
        id="map-0", from_facet="UI", from_id="register_student", to_facet="API", to_id="students.create"
    )
    m2 = IntegrationMappingV1(
        id="map-1", from_facet="UI", from_id="view_attendance", to_facet="API", to_id="attendance.list"
    )
    m3 = IntegrationMappingV1(
        id="map-2", from_facet="UI", from_id="submit_attendance", to_facet="API", to_id="attendance.create"
    )
    m4 = IntegrationMappingV1(
        id="map-3", from_facet="API", from_id="students.create", to_facet="DB", to_id="students"
    )
    m5 = IntegrationMappingV1(
        id="map-4", from_facet="API", from_id="attendance.list", to_facet="DB", to_id="attendance_records"
    )
    m6 = IntegrationMappingV1(
        id="map-5", from_facet="API", from_id="attendance.create", to_facet="DB", to_id="attendance_records"
    )
    return seal_outcome_contract(
        OutcomeContractV1(
            objective_id="obj_att_002d",
            definition_of_done=("Staff can register students and submit attendance",),
            facets=OutcomeFacetsV1(
                UI=UiFacetV1(
                    required_operations=("register_student", "view_attendance", "submit_attendance"),
                    required_views=("student_registration", "attendance_view", "attendance_submission"),
                    acceptance_criteria=("UI provides declared operations",),
                ),
                API=ApiFacetV1(
                    required_operations=(
                        ApiOperationSpecV1(id="students.create", path_or_action="POST /students"),
                        ApiOperationSpecV1(id="attendance.list", path_or_action="GET /attendance"),
                        ApiOperationSpecV1(id="attendance.create", path_or_action="POST /attendance"),
                    ),
                    acceptance_criteria=("API provides declared operations",),
                ),
                DB=DbFacetV1(
                    entities=("students", "attendance_records"),
                    acceptance_criteria=("DB provides declared entities",),
                ),
                AUTH=AuthFacetV1(
                    actors=("staff",),
                    permissions=("students.write", "attendance.read", "attendance.write"),
                    requirement="MANDATORY",
                    acceptance_criteria=("Auth model declared",),
                ),
                INTEGRATION=IntegrationFacetV1(
                    mappings=(m1, m2, m3, m4, m5, m6),
                    acceptance_criteria=("UI to API to DB mappings compose",),
                ),
            ),
        )
    )


def test_seed_compiles_coherent_attendance_units() -> None:
    sealed = _attendance_sealed()
    compiled = compile_outcome_obligations(sealed)
    assert compiled.ok
    seed = compile_obligation_step_seed(compiled.obligations)
    assert seed.ok, seed.detail
    assert seed.code == "OBLIGATION_STEPS_COMPILED"
    assert len(seed.steps) >= 4  # 3 UI+API units + persistence (+ auth)

    purposes = [s.purpose.lower() for s in seed.steps]
    assert any("register" in p for p in purposes)
    assert any("view" in p for p in purposes)
    assert any("submit" in p for p in purposes)
    assert any("persistence" in p for p in purposes)

    ui_steps = [s for s in seed.steps if "UI" in s.facet_refs]
    assert len(ui_steps) == 3
    for s in ui_steps:
        assert "API" in s.facet_refs
        assert len([r for r in s.obligation_refs if r.startswith("obl_ui_")]) == 1
        assert any(r.startswith("obl_api_") for r in s.obligation_refs)

    persist = next(s for s in seed.steps if s.step_key == "persistence")
    assert "obl_db_students" in persist.obligation_refs
    assert "obl_db_attendance_records" in persist.obligation_refs

    # No single mega-step owns all UI ops
    for s in seed.steps:
        ui_refs = [r for r in s.obligation_refs if r.startswith("obl_ui_")]
        assert len(ui_refs) <= 1

    # Full coverage
    covered = set(seed.covered_obligation_ids)
    assert covered == {o.obligation_id for o in compiled.obligations}


def test_seed_is_deterministic() -> None:
    sealed = _attendance_sealed()
    compiled = compile_outcome_obligations(sealed)
    a = compile_obligation_step_seed(compiled.obligations)
    b = compile_obligation_step_seed(compiled.obligations)
    assert a.ok and b.ok
    assert [s.step_key for s in a.steps] == [s.step_key for s in b.steps]
    assert [s.obligation_refs for s in a.steps] == [s.obligation_refs for s in b.steps]


def test_materialize_seed_preserves_obligation_refs() -> None:
    sealed = _attendance_sealed()
    compiled = compile_outcome_obligations(sealed)
    seed = compile_obligation_step_seed(compiled.obligations)
    assert seed.ok
    steps = materialize_obligation_seed_steps(
        seed,
        tenant_id="t1",
        cycle_id="cyc1",
        assigned_role="executor",
        step_budget=BudgetV1(
            time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=10, concurrency=1
        ),
        outcome_contract_id=sealed.contract_id,
        outcome_contract_hash=sealed.contract_hash,
    )
    assert len(steps) == len(seed.steps)
    assert not plan_omits_ui_obligations(
        step_obligation_refs=[c.obligation_refs for c in steps],
        obligations=compiled.obligations,
    )


def test_mega_collapse_prefers_seed() -> None:
    sealed = _attendance_sealed()
    compiled = compile_outcome_obligations(sealed)
    all_ids = tuple(o.obligation_id for o in compiled.obligations)
    assert should_prefer_obligation_seed(
        step_count=1,
        step_obligation_refs=[all_ids],
        obligations=compiled.obligations,
        used_legacy_one_step=True,
    )


@pytest.mark.asyncio
async def test_propose_prefers_seed_over_legacy_mega_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sealed = _attendance_sealed()
    compiled = compile_outcome_obligations(sealed)
    assert compiled.ok

    async def _fake_chat(**_kwargs: Any) -> ChatCompletionResultV1:
        # Smoking-gun shaped: one mega-step, all files, no steps[]
        return ChatCompletionResultV1(
            output_text=json.dumps(
                {
                    "reply": "build the whole app",
                    "needs_more_evidence": False,
                    "purpose": "Implement entire attendance product",
                    "target_scope": ["index.html", "api.php", "database.sql"],
                    "acceptance_criteria": ["done"],
                    "validation_commands": ["true"],
                    "obligation_refs": [o.obligation_id for o in compiled.obligations],
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
        _fake_chat,
    )

    proposal = await propose_step_contract(
        objective="Build attendance management for staff",
        definition_of_done=["register", "view", "submit"],
        understanding=UnderstandingEvidenceV1(
            objective_id="obj_att_002d",
            cycle_id="cyc_002d",
            files_read=(),
            symbols_found=(),
            observed_problem="product attendance",
            understanding_complete=True,
        ),
        constraints=[],
        tenant_id="t1",
        cycle_id="cyc_002d",
        step_index=0,
        assigned_role="executor",
        step_budget=BudgetV1(
            time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=20, concurrency=1
        ),
        actor_id="actor",
        authorization_header="",
        timeout_seconds=5.0,
        objective_id="obj_att_002d",
        outcome_contract=sealed,
        outcome_contract_obligations=compiled.obligations,
    )

    assert proposal.reason is None, proposal.reason
    assert proposal.plan is not None
    assert proposal.planner_call is not None
    assert proposal.planner_call.get("obligation_seed_applied") is True
    assert len(proposal.plan.steps) >= 4
    assert not plan_omits_ui_obligations(
        step_obligation_refs=[s.obligation_refs for s in proposal.plan.steps],
        obligations=compiled.obligations,
    )
    # Not one mega-step
    assert len(proposal.plan.steps) > 1
    ui_counts = [
        len([r for r in s.obligation_refs if str(r).startswith("obl_ui_")])
        for s in proposal.plan.steps
    ]
    assert max(ui_counts) <= 1


@pytest.mark.asyncio
async def test_propose_keeps_coherent_llm_multi_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sealed = _attendance_sealed()
    compiled = compile_outcome_obligations(sealed)
    seed = compile_obligation_step_seed(compiled.obligations)
    assert seed.ok

    steps_payload = []
    for cand in seed.steps:
        steps_payload.append(
            {
                "purpose": cand.purpose,
                "target_scope": list(cand.target_scope_hints),
                "acceptance_criteria": list(cand.acceptance_criteria),
                "validation_commands": ["echo ok"],
                "obligation_refs": list(cand.obligation_refs),
                "risk_tier": "LOW",
                "depends_on": [],
            }
        )

    async def _fake_chat(**_kwargs: Any) -> ChatCompletionResultV1:
        return ChatCompletionResultV1(
            output_text=json.dumps(
                {
                    "reply": "coherent units",
                    "needs_more_evidence": False,
                    "steps": steps_payload,
                }
            ),
            provider="test",
            resolved_provider="test",
            model="test-model",
            usage={},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        _fake_chat,
    )

    # Put seed paths into files_read so grounding passes without seed fallback
    hints = tuple(dict.fromkeys(h for s in seed.steps for h in s.target_scope_hints))
    proposal = await propose_step_contract(
        objective="Build attendance",
        definition_of_done=["done"],
        understanding=UnderstandingEvidenceV1(
            objective_id="obj_att_002d",
            cycle_id="cyc_002d",
            files_read=hints,
            symbols_found=(),
            observed_problem="attendance",
            understanding_complete=True,
        ),
        constraints=[],
        tenant_id="t1",
        cycle_id="cyc_002d",
        step_index=0,
        assigned_role="executor",
        step_budget=BudgetV1(
            time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=20, concurrency=1
        ),
        actor_id="actor",
        authorization_header="",
        timeout_seconds=5.0,
        objective_id="obj_att_002d",
        outcome_contract=sealed,
        outcome_contract_obligations=compiled.obligations,
    )
    assert proposal.reason is None, proposal.reason
    assert proposal.plan is not None
    assert proposal.planner_call is not None
    # Coherent LLM plan covering UI should not force seed overwrite
    assert proposal.planner_call.get("obligation_seed_applied") is not True
    assert len(proposal.plan.steps) >= 4
