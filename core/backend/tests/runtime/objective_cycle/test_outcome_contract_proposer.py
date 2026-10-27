"""Packet 2: OutcomeContract proposer + product-class enforcement."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, PlanRevisionV1, StepContractV1
from backend.aix_core.runtime.objective.outcome_contract_proposer import (
    bind_steps_to_contract,
    propose_and_seal_outcome_contract,
    propose_outcome_contract_heuristic,
    seal_from_raw_proposal,
)
from backend.aix_core.runtime.objective.product_class import (
    infer_greenfield_product_slug,
    is_product_class_objective,
    requires_outcome_contract,
)
from backend.aix_core.runtime.planner.plan_quality import evaluate_plan_quality
from backend.aix_core.runtime.verification.contract_coverage import evaluate_contract_coverage


ERP_OBJECTIVE = (
    "Create a complete greenfield ERP mini-system with HTML/CSS/JS frontend, "
    "PHP API backend, MySQL database schema, auth/login, dashboard, inventory, "
    "and invoices."
)


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=5, concurrency=1)


def _step(
    *,
    step_id: str | None = None,
    step_index: int,
    purpose: str,
    scope: tuple[str, ...],
) -> StepContractV1:
    del step_id
    return StepContractV1(
        cycle_id="cyc_1",
        tenant_id="tenant_test",
        step_index=step_index,
        purpose=purpose,
        target_scope=scope,
        acceptance_criteria=("artifact present and coherent",),
        validation_commands=("test -f README.md",),
        expected_output_schema_ref="text",
        assigned_role="executor",
        step_budget=_budget(),
        status="PENDING",
    )


def test_product_class_detects_erp_greenfield() -> None:
    assert is_product_class_objective(objective=ERP_OBJECTIVE) is True
    assert requires_outcome_contract(objective=ERP_OBJECTIVE) is True


def test_non_product_keeps_legacy_escape_hatch() -> None:
    obj = "Fix typo in README comment only"
    assert is_product_class_objective(objective=obj) is False
    assert requires_outcome_contract(objective=obj) is False


def test_global_flag_forces_contract() -> None:
    class _S:
        REQUIRE_OUTCOME_CONTRACT = True

    assert requires_outcome_contract(objective="rename variable foo", settings=_S()) is True


def test_heuristic_seals_erp_like_workhub_task() -> None:
    proposal = propose_outcome_contract_heuristic(
        objective_id="obj_erp",
        objective=ERP_OBJECTIVE,
        definition_of_done=("Working ERP mini-system with UI, API, DB",),
    )
    assert proposal.ok is True
    assert proposal.source == "heuristic"
    assert proposal.contract is not None
    assert proposal.contract.contract_hash
    facets = proposal.contract.facets
    assert facets.UI.requirement == "MANDATORY"
    assert facets.API.requirement == "MANDATORY"
    assert facets.DB.requirement == "MANDATORY"
    assert facets.INTEGRATION.requirement == "MANDATORY"
    assert facets.UI.required_operations
    assert facets.API.required_operations
    assert facets.DB.entities
    assert facets.INTEGRATION.mappings
    # Domain nouns from WorkHub-style objective become entities
    entity_blob = " ".join(facets.DB.entities)
    assert "invoice" in entity_blob or "inventory" in entity_blob or "product" in entity_blob


def test_invalid_llm_shaped_proposal_rejected_not_repaired() -> None:
    bad = seal_from_raw_proposal(
        objective_id="obj_x",
        raw={
            "definition_of_done": ["done"],
            "ui_operations": ["register"],
            "api_operations": [{"id": "students.create", "path_or_action": "POST /students"}],
            "db_entities": ["students"],
            # mapping cites unknown UI op → fail closed
            "mappings": [
                {
                    "id": "m1",
                    "from_facet": "UI",
                    "from_id": "missing_op",
                    "to_facet": "API",
                    "to_id": "students.create",
                }
            ],
        },
        source="llm",
    )
    assert bad.ok is False
    assert bad.code == "CONTRACT_INVALID"
    assert bad.contract is None


@pytest.mark.asyncio
async def test_parseable_invalid_llm_does_not_heuristic_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid LLM JSON must reject — never silently seal a heuristic substitute."""
    import json
    from types import SimpleNamespace

    import backend.infrastructure.model_gateway.hyeve_integration_client as hic
    import backend.infrastructure.model_gateway.intelligence_layer.model_registry as mr
    import backend.infrastructure.model_gateway.provider_routing as pr

    bad_content = json.dumps(
        {
            "definition_of_done": ["done"],
            "ui_operations": ["register"],
            "api_operations": [{"id": "students.create", "path_or_action": "POST /students"}],
            "db_entities": ["students"],
            "mappings": [
                {
                    "id": "m1",
                    "from_facet": "UI",
                    "from_id": "missing_op",
                    "to_facet": "API",
                    "to_id": "students.create",
                }
            ],
        }
    )

    async def _fake_chat(**_kwargs: object) -> object:
        return SimpleNamespace(output_text=bad_content)

    class _Router:
        def resolve(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(disposition="ALLOW", reason_codes=())

    class _Registry:
        def model_ref_for(self, _role: str) -> str:
            return "test-model"

    monkeypatch.setattr(hic, "chat_completion", _fake_chat)
    monkeypatch.setattr(mr, "load_model_registry", lambda: _Registry())
    monkeypatch.setattr(pr, "GovernedProviderRouter", _Router)

    result = await propose_and_seal_outcome_contract(
        objective_id="obj_x",
        objective=ERP_OBJECTIVE,
        definition_of_done=("Deliver ERP",),
        tenant_id="t",
        actor_id="a",
        authorization_header="Bearer test",
        use_llm=True,
        allow_heuristic_fallback=True,
    )
    assert result.ok is False
    assert result.source == "rejected"
    assert "without heuristic repair" in result.detail
    assert result.contract is None


@pytest.mark.asyncio
async def test_propose_and_seal_falls_back_to_heuristic_when_llm_off() -> None:
    proposal = await propose_and_seal_outcome_contract(
        objective_id="obj_erp",
        objective=ERP_OBJECTIVE,
        definition_of_done=("Deliver ERP",),
        use_llm=False,
    )
    assert proposal.ok is True
    assert proposal.source == "heuristic"
    assert proposal.contract is not None


def test_unbound_plan_not_accepted_when_contract_required() -> None:
    proposal = propose_outcome_contract_heuristic(
        objective_id="obj_erp",
        objective=ERP_OBJECTIVE,
    )
    assert proposal.contract is not None
    plan = PlanRevisionV1(
        objective_id="obj_erp",
        cycle_id="cyc_1",
        revision=1,
        created_from_evidence="ev1",
        steps=(
            _step(
                step_index=0,
                purpose="docs only",
                scope=("README.md",),
            ),
        ),
        status="DRAFT",
    )
    quality = evaluate_plan_quality(
        plan=plan,
        objective=ERP_OBJECTIVE,
        outcome_contract=proposal.contract,
        require_outcome_contract=True,
    )
    assert quality.ok is False
    assert quality.code in {
        "CONTRACT_OP_UNBOUND",
        "CONTRACT_FACET_MISSING",
        "CONTRACT_ENTITY_UNBOUND",
        "CONTRACT_MAPPING_UNBOUND",
    }


def test_bound_multifacet_plan_passes_coverage() -> None:
    proposal = propose_outcome_contract_heuristic(
        objective_id="obj_erp",
        objective=ERP_OBJECTIVE,
    )
    assert proposal.contract is not None
    contract = proposal.contract
    ui_ops = list(contract.facets.UI.required_operations)
    api_ops = [op.id for op in contract.facets.API.required_operations]
    entities = list(contract.facets.DB.entities)
    assert ui_ops and api_ops and entities

    built: list[StepContractV1] = []
    built.append(
        _step(
            step_index=0,
            purpose=f"IMPLEMENT {ui_ops[0]} with {api_ops[0]}",
            scope=(
                "proposed/obligation_seed/ui_flow.impl",
                "index.html",
                "api.php",
            ),
        ).model_copy(
            update={
                "acceptance_criteria": (f"UI {ui_ops[0]} and API {api_ops[0]} work",),
                "facet_refs": ("UI", "API"),
                "operation_refs": (ui_ops[0], api_ops[0]),
                "binding_role": "IMPLEMENT",
            }
        )
    )
    for op in ui_ops[1:]:
        built.append(
            _step(
                step_index=len(built),
                purpose=f"IMPLEMENT {op}",
                scope=(f"proposed/obligation_seed/ui_{op}.impl", "index.html"),
            ).model_copy(
                update={
                    "acceptance_criteria": (f"UI operation {op} works",),
                    "facet_refs": ("UI",),
                    "operation_refs": (op,),
                    "binding_role": "IMPLEMENT",
                }
            )
        )
    for op in api_ops[1:]:
        built.append(
            _step(
                step_index=len(built),
                purpose=f"IMPLEMENT {op}",
                scope=(f"proposed/obligation_seed/api_{op}.impl", "api.php"),
            ).model_copy(
                update={
                    "acceptance_criteria": (f"API operation {op} works",),
                    "facet_refs": ("API",),
                    "operation_refs": (op,),
                    "binding_role": "IMPLEMENT",
                }
            )
        )
    built.append(
        _step(
            step_index=len(built),
            purpose="IMPLEMENT persistence",
            scope=("proposed/obligation_seed/db_persistence.impl", "database.sql"),
        ).model_copy(
            update={
                "acceptance_criteria": tuple(f"DB entity {e} persisted" for e in entities),
                "facet_refs": ("DB",),
                "entity_refs": tuple(entities),
                "binding_role": "IMPLEMENT",
            }
        )
    )
    bound = bind_steps_to_contract(steps=tuple(built), contract=contract)
    assert all(s.operation_refs or s.entity_refs for s in bound)
    cov = evaluate_contract_coverage(
        outcome_contract=contract,
        steps=bound,
        require_outcome_contract=True,
    )
    assert cov.ok is True, cov.detail
    plan = PlanRevisionV1(
        objective_id="obj_erp",
        cycle_id="cyc_1",
        revision=1,
        created_from_evidence="ev1",
        steps=bound,
        status="DRAFT",
    )
    quality = evaluate_plan_quality(
        plan=plan,
        objective=ERP_OBJECTIVE,
        outcome_contract=proposal.contract,
        require_outcome_contract=True,
    )
    assert quality.ok is True, f"{quality.code}:{quality.detail}"


def test_file_suffix_bind_no_longer_stamps_all_ops() -> None:
    proposal = propose_outcome_contract_heuristic(
        objective_id="obj_erp_stamp",
        objective=ERP_OBJECTIVE,
    )
    assert proposal.contract is not None
    steps = (
        _step(
            step_index=0,
            purpose="build frontend UI",
            scope=("index.html", "styles.css", "app.js"),
        ),
        _step(
            step_index=1,
            purpose="build PHP API backend",
            scope=("api.php", "config.php"),
        ),
        _step(
            step_index=2,
            purpose="create database schema",
            scope=("database.sql",),
        ),
    )
    bound = bind_steps_to_contract(steps=steps, contract=proposal.contract)
    # Smoking-gun stamp removed: .html must not inherit every UI op
    ui_ops = set(proposal.contract.facets.UI.required_operations)
    stamped_all_ui = any(ui_ops and ui_ops.issubset(set(s.operation_refs)) for s in bound)
    assert stamped_all_ui is False
    cov = evaluate_contract_coverage(
        outcome_contract=proposal.contract,
        steps=bound,
        require_outcome_contract=True,
    )
    assert cov.ok is False
    assert cov.code in {
        "CONTRACT_OP_UNBOUND",
        "CONTRACT_FACET_MISSING",
        "CONTRACT_ENTITY_UNBOUND",
        "CONTRACT_MAPPING_UNBOUND",
        "CONTRACT_BINDING_UNSUBSTANTIATED",
    }


_EXPENSE_TRACKER = (
    "Build a complete Expense Tracker web app using FastAPI + Python backend, "
    "HTML/CSS/JavaScript frontend, and SQLite. Include registration/login, "
    "add/edit/delete expenses, categories, monthly budgets, dashboard totals, "
    "filters, validation, error handling, responsive UI, automated API tests, "
    "and README."
)


def test_heuristic_seals_expense_tracker_without_llm() -> None:
    assert is_product_class_objective(objective=_EXPENSE_TRACKER) is True
    assert infer_greenfield_product_slug(objective=_EXPENSE_TRACKER) == "expense-tracker"
    proposal = propose_outcome_contract_heuristic(
        objective_id="obj_expense",
        objective=_EXPENSE_TRACKER,
        definition_of_done=("Working Expense Tracker with UI, API, DB",),
    )
    assert proposal.ok is True, proposal.detail
    assert proposal.source == "heuristic"
    assert proposal.contract is not None
    entities = " ".join(proposal.contract.facets.DB.entities)
    assert "expense" in entities
    ops = " ".join(proposal.contract.facets.UI.required_operations)
    assert "login" in ops
    assert "register" in ops
