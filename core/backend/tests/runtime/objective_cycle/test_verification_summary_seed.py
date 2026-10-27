"""Regression: read-only repo summary objectives get a seeded ACCEPTED plan."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.aix_core.runtime.executor.real_execute import (
    classify_intent,
    looks_like_read_only_summary_objective,
)
from backend.aix_core.runtime.objective.durable_api_backend import (
    DurableObjectiveCycleApiBackend,
    _CycleRecord,
    _ObjectiveRecord,
)

BACKEND_ROOT = Path(__file__).resolve().parents[4] / "backend"
OBJECTIVE = "hey can you give me summary of D:\\v2.1\\AIX\\hyeve-aix-service\\backend"


class _FakeSettings:
    INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED = True
    INTELLIGENCE_LAYER_CHAT_TIMEOUT_SECONDS = 30
    OBJECTIVE_CYCLE_REPOSITORY_ROOTS = json.dumps({
        "aix-backend": str(BACKEND_ROOT).replace("\\", "/"),
        "hyeve-aix-service": str(BACKEND_ROOT.parent).replace("\\", "/"),
    })
    INTELLIGENCE_LAYER_VERIFIER_ENABLED = False
    INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED = False


def test_looks_like_read_only_summary_objective_matches_cli_phrasing() -> None:
    assert looks_like_read_only_summary_objective(OBJECTIVE) is True
    assert looks_like_read_only_summary_objective("update summary doc") is False
    assert looks_like_read_only_summary_objective(
        "Summarize the backend in factual prose. Do not edit repository source. "
        "Put the markdown only in the cycle workspace evidence folder."
    ) is True
    assert (
        classify_intent(
            OBJECTIVE,
            target_path=r"D:\v2.1\AIX\hyeve-aix-service\backend",
            symbol_name=None,
        )
        == "INSPECT_DIRECTORY"
    )


def test_whole_repo_read_only_audit_is_unbounded_not_a_seeded_summary() -> None:
    from backend.aix_core.runtime.executor.real_execute import looks_like_unbounded_codebase_audit

    audit = (
        "Explore the complete current AIX codebase in read-only mode. "
        "Map the architecture, services, modules, runtime flows, APIs, agents, models, "
        "planners, execution, verification, memory, WorkHub integration, Hyeve Services "
        "integration, configuration, databases, tests, and frontend. "
        "Do not modify any code. Produce a concise AIX-CURRENT-CODEBASE-AUDIT.md."
    )
    assert looks_like_unbounded_codebase_audit(audit) is True
    assert looks_like_read_only_summary_objective(audit) is False
    assert looks_like_unbounded_codebase_audit(OBJECTIVE) is False


def test_seeded_summary_wraps_accepted_plan_without_planner() -> None:
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        settings=_FakeSettings(),
    )
    obj = _ObjectiveRecord(
        objective_id="obj-summary",
        tenant_id="test",
        workhub_task_id="wh-1",
        objective=OBJECTIVE,
        task_class="verification_only",
        definition_of_done=(OBJECTIVE,),
        repository_id="aix-backend",
        allowed_files=(),
    )
    cycle = _CycleRecord(
        cycle_id="cyc-summary",
        objective_id=obj.objective_id,
        tenant_id="test",
        candidate_sha="abc1234",
        budgets={},
        phase="WAITING_APPROVAL",
    )
    backend._objectives[obj.objective_id] = obj
    backend._cycles[cycle.cycle_id] = cycle

    backend._seed_verification_summary_contract(obj=obj, cycle=cycle, envelope=None)

    contract = backend._step_contracts[obj.objective_id]
    assert contract.target_scope == (".",)
    plans = backend._plan_revisions.get(obj.objective_id, ())
    assert plans, "seeded summary plan must exist before approval (not PLAN_NOT_FOUND)"
    assert plans[-1].status == "ACCEPTED"


def test_seeded_codebase_audit_wraps_accepted_plan_without_planner() -> None:
    from backend.aix_core.runtime.executor.real_execute import looks_like_unbounded_codebase_audit

    audit = (
        "Explore the complete current AIX codebase in read-only mode. "
        "Map the architecture, services, modules, runtime flows, APIs, agents, models, "
        "planners, execution, verification, memory, WorkHub integration, Hyeve Services "
        "integration, configuration, databases, tests, and frontend. "
        "Do not modify any code. Produce a concise AIX-CURRENT-CODEBASE-AUDIT.md."
    )
    assert looks_like_unbounded_codebase_audit(audit) is True
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        settings=_FakeSettings(),
    )
    obj = _ObjectiveRecord(
        objective_id="obj-audit",
        tenant_id="test",
        workhub_task_id="wh-1",
        objective=audit,
        task_class="docs_only_change",
        definition_of_done=(audit,),
        repository_id="hyeve-aix-service",
        allowed_files=("AIX-CURRENT-CODEBASE-AUDIT.md",),
    )
    cycle = _CycleRecord(
        cycle_id="cyc-audit",
        objective_id=obj.objective_id,
        tenant_id="test",
        candidate_sha="abc1234",
        budgets={},
        phase="WAITING_APPROVAL",
    )
    backend._objectives[obj.objective_id] = obj
    backend._cycles[cycle.cycle_id] = cycle

    backend._seed_codebase_audit_contract(obj=obj, cycle=cycle, envelope=None)

    contract = backend._step_contracts[obj.objective_id]
    assert contract.target_scope == ("AIX-CURRENT-CODEBASE-AUDIT.md",)
    plans = backend._plan_revisions.get(obj.objective_id, ())
    assert plans, "seeded audit plan must exist before approval (not PLAN_NOT_FOUND)"
    assert plans[-1].status == "ACCEPTED"
    assert str(plans[-1].planner_reasoning_ref) == "seeded-step-contract"
    assert backend._is_closed_seed_family(obj) is True


@pytest.mark.asyncio
async def test_run_real_execute_writes_bounded_audit_markdown(tmp_path: Path) -> None:
    from backend.aix_core.runtime.executor.real_execute import (
        _AUDIT_SPINE_PATHS,
        run_real_execute,
    )

    audit = (
        "Explore the complete current AIX codebase in read-only mode. "
        "Map the architecture. Do not modify any code. "
        "Produce a concise AIX-CURRENT-CODEBASE-AUDIT.md."
    )
    repo = tmp_path / "repo"
    for rel in _AUDIT_SPINE_PATHS:
        path = repo / rel.replace("\\", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# stub {rel}\nCANONICAL_COMMAND\nAIX_FRAMEWORK_\n"
            "assert_workhub_write_allowed\nWIRED (flag-off)\nGet-FrameworkFlagLabel\n",
            encoding="utf-8",
        )
    settings = _FakeSettings()
    settings.OBJECTIVE_CYCLE_REPOSITORY_ROOTS = json.dumps(
        {"hyeve-aix-service": str(repo).replace("\\", "/")}
    )
    result = await run_real_execute(
        tenant_id="test",
        cycle_id="cyc-audit-exec",
        objective_id="obj-audit-exec",
        objective=audit,
        workhub_task_id="wh-1",
        project_id=None,
        task_number=None,
        repository_id="hyeve-aix-service",
        settings=settings,
        require_workhub_load=False,
        workspace_base=tmp_path / "ws",
        original_objective=audit,
        apply_edits=True,
    )
    assert result.ok is True, result.detail
    assert result.executor == "objective_cycle.real_execute.codebase_audit.v1"
    proposed = Path(result.workspace) / "proposed" / "AIX-CURRENT-CODEBASE-AUDIT.md"
    assert proposed.is_file()
    body = proposed.read_text(encoding="utf-8")
    assert "objective_cycle_router" in body
    assert "backend/main.py" in body
    assert (repo / "AIX-CURRENT-CODEBASE-AUDIT.md").is_file()
    assert result.changed_source_files
    assert result.changed_source_files[0]["path"] == "AIX-CURRENT-CODEBASE-AUDIT.md"


@pytest.mark.asyncio
async def test_mark_dispatch_ready_clears_stale_last_error() -> None:
    from backend.aix_core.runtime.objective.contracts import (
        BudgetV1,
        PlanRevisionV1,
        RiskTier,
        StepContractV1,
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        settings=_FakeSettings(),
    )
    cycle = _CycleRecord(
        cycle_id="cyc-ready",
        objective_id="obj-ready",
        tenant_id="test",
        candidate_sha="abc",
        budgets={},
        phase="BLOCKED",
        last_error_detail="The diff is empty and therefore does not implement the acceptance criteria",
        terminal_reason="VERIFICATION_REPAIR_REQUIRED",
    )
    backend._cycles[cycle.cycle_id] = cycle
    step = StepContractV1(
        id="step-ready-1",
        cycle_id=cycle.cycle_id,
        tenant_id="test",
        step_index=0,
        purpose="clear stale error",
        target_scope=("a.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("done",),
        validation_commands=("true",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=BudgetV1(
            time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=1, concurrency=1
        ),
        status="PENDING",
    )
    plan = PlanRevisionV1(
        objective_id=cycle.objective_id,
        cycle_id=cycle.cycle_id,
        revision=1,
        status="ACCEPTED",
        steps=(step,),
    )
    backend._admit_plan_revision(plan)
    backend._step_contracts[cycle.objective_id] = step
    ready = await backend._mark_dispatch_ready(
        cycle=cycle,
        state="STEP_READY",
        command_id="cmd-1",
        step=step,
        reason="test",
    )
    assert ready.get("ok") is True, ready
    assert cycle.last_error_detail is None
    assert cycle.terminal_reason is None
    assert cycle.dispatch_state == "STEP_READY"
