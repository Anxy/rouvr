"""P6: remaining mixed-state consumers cannot authorize execution."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from backend.aix_core.intelligence.context.step_context_capsule import build_step_context_capsule
from backend.aix_core.runtime.executor.executor_package import build_executor_package
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    ObjectiveObligationV1,
    StepContractV1,
    UnderstandingEvidenceV1,
)
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend

_BACKEND = Path(__file__).resolve().parents[4] / "aix_core" / "runtime" / "objective" / "durable_api_backend.py"
_REAL_EXECUTE = Path(__file__).resolve().parents[4] / "aix_core" / "runtime" / "executor" / "real_execute.py"
_VALIDATORS = (
    Path(__file__).resolve().parents[4] / "aix_core" / "runtime" / "objective" / "provenance" / "validators.py"
)


def _function_source(name: str, path: Path = _BACKEND) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"    (?:async )?def {name}\(.*?(?=\n    (?:async )?def |\nclass |\Z)", text, re.S)
    assert match is not None, f"function {name} not found in {path.name}"
    return match.group(0)


def test_exploration_gate_cannot_bind_from_allowed_files() -> None:
    gate = _function_source("_run_exploration_gate")
    assert "allowed_bound" not in gate
    assert "for p in (obj.allowed_files" not in gate
    assert "obj.allowed_files or ()" not in gate
    assert "is_demo_compatible_page_objective" not in gate


def test_repair_cannot_write_allowed_files() -> None:
    execute = _function_source("_execute_claimed_step")
    assert "obj.allowed_files = merged" not in execute
    assert "cycle.repair_scope_expanded" not in execute
    backend = _BACKEND.read_text(encoding="utf-8")
    assert "obj.allowed_files = merged" not in backend


def test_executor_does_not_invent_scope_from_objective_detectors() -> None:
    text = _REAL_EXECUTE.read_text(encoding="utf-8")
    assert "src/lib/service-endpoints.ts" not in text
    assert "CycleSummaryStrip.tsx" not in text
    assert "url_extras" not in text
    assert "objective_requires_ui_surface" not in text


def test_facet_obligation_coverage_is_not_execute_authority() -> None:
    execute = _function_source("_execute_claimed_step")
    assert "self._provenance.latest_plan(obj.objective_id) is None" in execute
    assert "validate_obligation_coverage" in execute


def test_legacy_plan_quality_cannot_accept_when_intent_exists() -> None:
    execute = _function_source("_execute_claimed_step")
    assert "LEGACY_PLAN_FORBIDDEN" in execute
    assert "allowed_files=obj.allowed_files" not in execute


def test_seeds_cannot_recover_scope_from_allowed_files() -> None:
    mkdir = _function_source("_seed_create_directory_contract")
    rename = _function_source("_seed_rename_path_contract")
    summary = _function_source("_seed_verification_summary_contract")
    assert "allowed_files=()" in mkdir
    assert "allowed_files=()" in rename
    assert "obj.allowed_files" not in summary.split("P6:")[1] or "cannot choose" in summary
    assert "for raw in obj.allowed_files" not in summary
    assert "obligation_refs=()" in rename
    assert "obligation_refs=obligation_refs" in summary
    audit = _function_source("_seed_codebase_audit_contract")
    assert "obj.allowed_files" not in audit
    assert "codebase_audit_markdown_filename" in audit


def test_planner_excerpts_cannot_seed_gateway_files() -> None:
    excerpts = _function_source("_planner_file_excerpts")
    assert "service-endpoints.ts" not in excerpts
    assert "obj.allowed_files" not in excerpts


def test_plan_markdown_lane_is_not_empty_allowed_files() -> None:
    persist = _function_source("_persist_accepted_plan_observability")
    assert "not bool(obj.allowed_files)" not in persist
    assert "_demo_capability_from_accepted_plan" in persist
    assert "## Provenance" in persist
    assert "plan_targets" in persist


def test_execution_scope_never_returns_allowed_files() -> None:
    scope = _function_source("_execution_scope_paths")
    assert "return obj.allowed_files" not in scope
    assert "allowed_files=obj.allowed_files" not in scope


def test_open_implementation_still_has_no_relevance_or_necessity_validator() -> None:
    source = _VALIDATORS.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.name.casefold() for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert not any("relevance" in name or "necessity" in name for name in names)


def test_flag_still_absent() -> None:
    dab = _BACKEND.read_text(encoding="utf-8")
    assert "OBJECTIVE_PROVENANCE_ENABLED" not in dab
    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    assert not hasattr(backend, "OBJECTIVE_PROVENANCE_ENABLED")


def test_dead_allowed_files_helpers_are_deleted() -> None:
    text = _BACKEND.read_text(encoding="utf-8")
    assert "_collect_bound_target_understanding" not in text
    assert "def _seed_unbound_topic_reads" not in text


def test_live_planner_does_not_feed_excerpts_into_execute_authority() -> None:
    execute = _function_source("_execute_claimed_step")
    assert "_planner_file_excerpts(" not in execute
    assert "file_excerpts={}" in execute


def test_admit_does_not_mint_allowed_files_as_mutation_constraints() -> None:
    text = _BACKEND.read_text(encoding="utf-8")
    assert "allowed_files=(),  # P6: admission list is metadata, not mutation authority" in text


def test_capsule_live_call_is_intent_and_plan_targets() -> None:
    execute = _function_source("_execute_claimed_step")
    capsule = execute[execute.index("build_step_context_capsule") : execute.index("build_executor_package")]
    assert "intent_id=intent.id if intent is not None else None" in capsule
    assert "plan_target_paths=execution_scope" in capsule
    assert "observed_problem=" not in capsule


def test_executor_cannot_recover_empty_frozen_scope_from_step_contract() -> None:
    text = _REAL_EXECUTE.read_text(encoding="utf-8")
    assert "scope_frozen = allowed_files is not None" in text
    assert "if demo_capability and _step_requests_html_css_generation" in text
    assert "demo_capability\n        or looks_like_page_generation_objective" not in text
    assert "if not allowed_files and not scope_frozen" in text


def test_world_state_live_facts_are_cited_evidence_not_files_read_bag() -> None:
    text = _BACKEND.read_text(encoding="utf-8")
    block = text[
        text.index('if operation == "get_world_state"') : text.index(
            'if operation == "get_executor_packages"'
        )
    ]
    assert "provenance_known_facts=cited_locations if intent is not None else None" in block
    assert "tuple(understanding.files_read)" not in block


@pytest.mark.asyncio
async def test_empty_allowed_files_cannot_authorize_live_execute() -> None:
    """Finish line: the old mixed representation cannot dispatch work."""
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        settings=None,
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-p6",
            "objective": "Build a React registration form",
            "task_class": "bounded_repair",
            "definition_of_done": ("form validates",),
            "repository_id": "aix-frontend",
            "allowed_files": ("src/pages/Register.tsx",),
        },
        idempotency_key="admit-p6-auth",
        expected_version=None,
    )
    assert admit["ok"] is True
    obj = backend._objectives[admit["objective_id"]]
    assert obj.allowed_files == ("src/pages/Register.tsx",)
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-p6-auth",
        expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/p6"},
        idempotency_key="approve-p6-auth",
        expected_version=started["version"],
    )
    assert approved["ok"] is False
    assert (approved.get("error") or {}).get("code") == "NEED_EVIDENCE"
    assert approved.get("real_execute") is None
    assert backend._provenance.latest_plan(obj.objective_id) is None


def _leftover_contract(cycle_id: str, *paths: str) -> StepContractV1:
    return StepContractV1(
        cycle_id=cycle_id,
        tenant_id="tenant-1",
        step_index=0,
        purpose="leftover mixed-state contract",
        target_scope=paths,
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("leftover must not execute",),
        validation_commands=("true",),
        assigned_role="executor",
        step_budget=BudgetV1(
            time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=2, concurrency=1
        ),
        obligation_refs=(),
        status="PENDING",
    )


async def _live_mixed_cycle(
    *,
    allowed_files: tuple[str, ...],
    leftover_target_scope: tuple[str, ...] | None,
    key: str,
):
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        settings=None,
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": f"wh-{key}",
            "objective": "Build a React registration form",
            "task_class": "bounded_repair",
            "definition_of_done": ("form validates",),
            "repository_id": "aix-frontend",
            "allowed_files": allowed_files,
        },
        idempotency_key=f"admit-{key}",
        expected_version=None,
    )
    assert admit["ok"] is True
    obj = backend._objectives[admit["objective_id"]]
    if leftover_target_scope is not None:
        backend._step_contracts[obj.objective_id] = _leftover_contract(
            "pending-cycle", *leftover_target_scope
        )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=obj.objective_id,
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key=f"start-{key}",
        expected_version=None,
    )
    if leftover_target_scope is not None and obj.objective_id in backend._step_contracts:
        planted = backend._step_contracts[obj.objective_id]
        if planted.cycle_id == "pending-cycle":
            backend._step_contracts[obj.objective_id] = planted.model_copy(
                update={"cycle_id": started["cycle_id"]}
            )
    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": f"workhub://approvals/{key}"},
        idempotency_key=f"approve-{key}",
        expected_version=started["version"],
    )
    return backend, obj, approved


def _assert_mixed_state_cannot_execute(backend, obj, approved, *, forbidden_paths: tuple[str, ...]) -> None:
    assert approved["ok"] is False
    code = str((approved.get("error") or {}).get("code") or "")
    assert code in {"NEED_EVIDENCE", "LEGACY_PLAN_FORBIDDEN", "NEED_CLARIFICATION"}
    assert approved.get("real_execute") is None
    assert backend._executor_packages.get(obj.objective_id, []) == []
    assert backend._provenance.latest_plan(obj.objective_id) is None
    scope = backend._execution_scope_paths(obj, backend._step_contracts.get(obj.objective_id))
    for path in forbidden_paths:
        assert path not in scope
    for path in obj.allowed_files:
        assert path not in scope


@pytest.mark.asyncio
async def test_mixed_allowed_files_without_target_scope_cannot_execute() -> None:
    backend, obj, approved = await _live_mixed_cycle(
        allowed_files=("src/pages/Register.tsx",),
        leftover_target_scope=None,
        key="mix-1",
    )
    assert obj.allowed_files == ("src/pages/Register.tsx",)
    _assert_mixed_state_cannot_execute(
        backend, obj, approved, forbidden_paths=("src/pages/Register.tsx",)
    )


@pytest.mark.asyncio
async def test_mixed_empty_allowed_files_with_leftover_target_scope_cannot_execute() -> None:
    backend, obj, approved = await _live_mixed_cycle(
        allowed_files=(),
        leftover_target_scope=("src/pages/Register.tsx",),
        key="mix-2",
    )
    assert obj.allowed_files == ()
    _assert_mixed_state_cannot_execute(
        backend, obj, approved, forbidden_paths=("src/pages/Register.tsx",)
    )


@pytest.mark.asyncio
async def test_mixed_allowed_files_a_with_leftover_target_scope_b_cannot_execute() -> None:
    backend, obj, approved = await _live_mixed_cycle(
        allowed_files=("A",),
        leftover_target_scope=("B",),
        key="mix-3",
    )
    assert obj.allowed_files == ("A",)
    _assert_mixed_state_cannot_execute(backend, obj, approved, forbidden_paths=("A", "B"))


def test_execution_scope_helper_does_not_read_allowed_files_or_uncited_leftovers() -> None:
    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    from backend.aix_core.runtime.objective.durable_api_backend import _ObjectiveRecord

    obj = _ObjectiveRecord(
        objective_id="obj_mix_scope",
        tenant_id="t1",
        workhub_task_id="wh",
        objective="Build a React registration form",
        original_objective="Build a React registration form",
        task_class="bounded_repair",
        definition_of_done=("form validates",),
        repository_id="aix-frontend",
        allowed_files=("src/pages/Register.tsx", "A"),
    )
    leftover = _leftover_contract("cyc", "src/pages/Register.tsx", "B")
    assert backend._accepted_plan_target_paths(obj) is None
    assert backend._execution_scope_paths(obj, None) == ()
    scope = backend._execution_scope_paths(obj, leftover)
    assert scope == ()
    assert "src/pages/Register.tsx" not in scope
    assert "A" not in scope
    assert "B" not in scope


_TAINT_PATHS = (
    "src/pages/Register.tsx",
    "proposed/secret.py",
    "backend/auth/middleware.py",
)


def _taint_legacy_fields(backend: DurableObjectiveCycleApiBackend, obj, *, tainted: str, cycle_id: str) -> None:
    """Inject stale values into every leftover mixed-state field. Intent stays frozen."""
    obj.allowed_files = (tainted,)
    obj.original_objective = f"modify {tainted}"
    backend._obligations[obj.objective_id] = (
        ObjectiveObligationV1(
            objective_id=obj.objective_id,
            description=tainted,
            required=True,
            source="objective_text",
            acceptance_criteria=(tainted,),
        ),
    )
    backend._understanding_evidence[obj.objective_id] = UnderstandingEvidenceV1(
        objective_id=obj.objective_id,
        cycle_id=cycle_id,
        files_read=(tainted,),
        symbols_found=(tainted,),
        observed_problem=f"legacy dump names {tainted}",
        evidence_refs=(f"toolcall://{tainted}",),
        understanding_complete=True,
    )
    backend._step_contracts[obj.objective_id] = _leftover_contract(cycle_id, tainted)


def _authority_surfaces(backend: DurableObjectiveCycleApiBackend, obj) -> dict[str, object]:
    intent = backend._provenance.get_intent(obj.objective_id)
    prov_plan = backend._provenance.latest_plan(obj.objective_id)
    contract = backend._step_contracts.get(obj.objective_id)
    scope = backend._execution_scope_paths(obj, contract)
    capsule = build_step_context_capsule(
        step_id=contract.id if contract is not None else "implicit",
        understanding=backend._understanding_evidence.get(obj.objective_id),
        target_scope=scope,
        intent_id=intent.id if intent is not None else None,
        intent_text=intent.text if intent is not None else None,
        cited_claim_ids=(),
        cited_evidence_ids=tuple(prov_plan.evidence_ids) if prov_plan is not None else (),
        plan_target_paths=scope,
        plan_id=prov_plan.plan_id if prov_plan is not None else None,
    )
    package = build_executor_package(
        cycle_id="cyc-taint",
        objective_id=obj.objective_id,
        objective=backend._intent_text(obj),
        envelope=None,
        step_contract=contract,
        context_capsule=capsule,
        repair_context=None,
        plan_id=prov_plan.plan_id if prov_plan is not None else None,
        target_paths=scope,
    )
    return {
        "plan_targets": tuple(t.path for t in prov_plan.targets) if prov_plan is not None else (),
        "scope": scope,
        "capsule_files": capsule.relevant_files,
        "capsule_plan_targets": capsule.plan_target_paths,
        "package_paths": package.target_paths,
        "intent_text": intent.text if intent is not None else "",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("tainted", _TAINT_PATHS)
async def test_tainted_legacy_fields_cannot_acquire_authority(
    tainted: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    async def _forbidden_execute(**kwargs: object) -> object:
        captured.update(kwargs)
        raise AssertionError(f"real_execute must not run for tainted leftover {tainted}")

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        _forbidden_execute,
    )
    slug = tainted.replace("/", "_").replace("\\", "_")
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        settings=None,
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": f"wh-taint-{slug}",
            "objective": "Fix login",
            "task_class": "bounded_repair",
            "definition_of_done": ("login works",),
            "repository_id": "aix-frontend",
            "allowed_files": (),
        },
        idempotency_key=f"admit-taint-{slug}",
        expected_version=None,
    )
    assert admit["ok"] is True
    obj = backend._objectives[admit["objective_id"]]
    intent = backend._provenance.get_intent(obj.objective_id)
    assert intent is not None
    assert intent.text == "Fix login"
    _taint_legacy_fields(backend, obj, tainted=tainted, cycle_id="pre-start")
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=obj.objective_id,
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key=f"start-taint-{slug}",
        expected_version=None,
    )
    _taint_legacy_fields(backend, obj, tainted=tainted, cycle_id=started["cycle_id"])
    assert obj.allowed_files == (tainted,)
    assert obj.original_objective == f"modify {tainted}"
    assert backend._provenance.get_intent(obj.objective_id).text == "Fix login"
    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": f"workhub://approvals/taint-{slug}"},
        idempotency_key=f"approve-taint-{slug}",
        expected_version=started["version"],
    )
    assert approved["ok"] is False
    assert (approved.get("error") or {}).get("code") in {
        "NEED_EVIDENCE",
        "LEGACY_PLAN_FORBIDDEN",
        "NEED_CLARIFICATION",
        "INTENT_UNCLEAR",
        "NO_ACCEPTED_PLAN",
        "PLANNER_DISABLED",
    }
    assert approved.get("real_execute") is None
    assert captured == {}
    assert backend._executor_packages.get(obj.objective_id, []) == []
    assert backend._provenance.latest_plan(obj.objective_id) is None
    legacy_plan = backend._active_plan_revision(obj.objective_id)
    if legacy_plan is not None:
        for step in legacy_plan.steps:
            assert tainted not in step.target_scope
    surfaces = _authority_surfaces(backend, obj)
    assert surfaces["intent_text"] == "Fix login"
    for bag in (
        surfaces["plan_targets"],
        surfaces["scope"],
        surfaces["capsule_files"],
        surfaces["capsule_plan_targets"],
        surfaces["package_paths"],
    ):
        assert tainted not in bag
    for kwargs_value in captured.values():
        assert tainted not in str(kwargs_value)


