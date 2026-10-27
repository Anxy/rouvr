"""AIX-DSD-001: durable, non-recursive step dispatcher acceptance tests."""
from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import pytest

from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    PlanRevisionV1,
    RiskTier,
    StepContractV1,
)
from backend.aix_core.runtime.objective.durable_aggregate.replay import JsonFileProjectionStore
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.planner.step_contract_planner import StepContractProposalV1
from backend.infrastructure.model_gateway.hyeve_integration_client import ChatCompletionResultV1

_BUDGET = BudgetV1(
    time_seconds=3600, tokens=200_000, cost_usd=10.0,
    retries=3, steps=20, concurrency=1,
)


@pytest.fixture
def dispatcher_repo(tmp_path: Path) -> Path:
    (tmp_path / "service.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _step(step_id: str, index: int, previous: str | None = None) -> StepContractV1:
    return StepContractV1(
        id=step_id,
        cycle_id="placeholder",
        tenant_id="tenant-dsd",
        step_index=index,
        purpose=f"perform durable step {index}",
        depends_on=(previous,) if previous else (),
        target_scope=("service.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=(f"step {index} complete",),
        validation_commands=("python -m py_compile service.py",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        status="PENDING",
    )


def _plan(kwargs: dict[str, Any], *, count: int, revision: int = 1) -> PlanRevisionV1:
    steps: list[StepContractV1] = []
    for index in range(count):
        raw = _step(f"step-r{revision}-{index}", index, steps[-1].id if steps else None).model_dump()
        raw["cycle_id"] = kwargs["cycle_id"]
        raw["obligation_refs"] = [item.id for item in kwargs["obligations"]]
        steps.append(StepContractV1.model_validate(raw))
    return PlanRevisionV1(
        objective_id=kwargs["objective_id"], cycle_id=kwargs["cycle_id"],
        revision=revision, steps=tuple(steps),
    )


def _install_normal_lane(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    *,
    step_count: int,
    captured: list[str],
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        json.dumps({"dsd-repo": repo.as_posix()}),
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED",
        False,
    )

    async def propose(**kwargs: Any) -> StepContractProposalV1:
        plan = _plan(kwargs, count=step_count)
        return StepContractProposalV1(contract=plan.steps[0], plan=plan, reply="durable plan", reason=None)

    async def execute(**kwargs: Any) -> RealExecuteResult:
        captured.append(kwargs["step_contract"].id)
        return RealExecuteResult(
            ok=True, code="REAL_EXECUTE_OK", workspace="ws",
            evidence_refs=[f"artifact://durable-step/{kwargs['step_contract'].id}"],
            workhub_loaded=True, artifacts={}, execution_status="COMPLETED",
            verification_status="PASSED", objective_status="COMPLETED",
            real_operations=1, target_files_modified=1,
            diff_text=f"+ {kwargs['step_contract'].id}\n",
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract", propose
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", execute
    )


async def _approve(backend: DurableObjectiveCycleApiBackend, *, key: str) -> dict[str, Any]:
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-dsd", actor_id="human",
        resource_id=None,
        body={
            "workhub_task_id": f"wh-{key}",
            "objective": "Update service.py through separately verified durable steps.",
            "task_class": "single_file_helper",
            "definition_of_done": ("all durable steps pass",),
            "repository_id": "dsd-repo",
            "allowed_files": ("service.py",),
        },
        idempotency_key=f"admit-{key}", expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-dsd", actor_id="human",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 10}},
        idempotency_key=f"start-{key}", expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-dsd", actor_id="human",
        resource_id=started["cycle_id"],
        body={"approval_reference": f"workhub://approvals/{key}"},
        idempotency_key=f"approve-{key}", expected_version=started["version"],
    )
    return {"admit": admit, "started": started, "approved": approved}


async def _dispatch(
    backend: DurableObjectiveCycleApiBackend, cycle_id: str, key: str,
) -> dict[str, Any]:
    result = await backend.command(
        operation="dispatch_ready_step", tenant_id="tenant-dsd", actor_id="dispatcher",
        resource_id=cycle_id, body={}, idempotency_key=f"dispatch-{key}",
        expected_version=None,
    )
    return result


def _claim_events(backend: DurableObjectiveCycleApiBackend, cycle_id: str) -> list[dict[str, Any]]:
    return [
        event for event in backend._cycles[cycle_id].events  # noqa: SLF001
        if event.get("event_type") == "step.execution_claimed"
    ]


@pytest.mark.asyncio
async def test_background_dispatch_task_does_not_mistake_itself_for_a_duplicate(
    dispatcher_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    _install_normal_lane(monkeypatch, dispatcher_repo, step_count=1, captured=captured)
    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_AWAIT_EXECUTION", "0")
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False
    )
    run = await _approve(backend, key="background-self-collision")
    cycle_id = run["started"]["cycle_id"]
    task = backend._execute_tasks.get(cycle_id)  # noqa: SLF001
    if task is not None:
        await task
    assert captured == ["step-r1-0"]
    cycle = backend._cycles[cycle_id]  # noqa: SLF001
    assert cycle.dispatch_state == "STEP_PASSED"
    assert cycle.execution_claim_id is not None


@pytest.mark.asyncio
async def test_ds01_two_step_normal_flow_has_request_boundary(
    dispatcher_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    _install_normal_lane(monkeypatch, dispatcher_repo, step_count=2, captured=captured)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    run = await _approve(backend, key="ds01")
    assert run["approved"]["phase"] == "NEXT_STEP_READY"
    assert run["approved"]["dispatch_required"] is True
    assert captured == ["step-r1-0"]
    done = await _dispatch(backend, run["started"]["cycle_id"], "ds01-step1")
    assert done["phase"] == "COMPLETED_FOR_REVIEW"
    assert captured == ["step-r1-0", "step-r1-1"]


@pytest.mark.asyncio
async def test_ds02_five_steps_have_five_persisted_claim_boundaries(
    dispatcher_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    _install_normal_lane(monkeypatch, dispatcher_repo, step_count=5, captured=captured)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    run = await _approve(backend, key="ds02")
    cycle_id = run["started"]["cycle_id"]
    for index in range(1, 5):
        result = await _dispatch(backend, cycle_id, f"ds02-{index}")
        assert result["ok"] is True
    claims = _claim_events(backend, cycle_id)
    claim_ids = [event["payload_refs"]["execution_claim_id"] for event in claims]
    assert len(captured) == len(claims) == len(set(claim_ids)) == 5
    assert all(event.get("occurred_at") for event in claims)
    assert backend._cycles[cycle_id].phase == "COMPLETED_FOR_REVIEW"  # noqa: SLF001


@pytest.mark.asyncio
async def test_ds03_client_disconnect_leaves_honest_recoverable_state(
    dispatcher_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    _install_normal_lane(monkeypatch, dispatcher_repo, step_count=2, captured=captured)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    run = await _approve(backend, key="ds03")
    cycle_id = run["started"]["cycle_id"]
    # The approval response has ended here: a later independent query/dispatch owns continuation.
    observed = await backend.query(operation="get_cycle", tenant_id="tenant-dsd", resource_id=cycle_id, child_id=None, filters={})
    assert observed["item"]["phase"] == "NEXT_STEP_READY"
    assert observed["item"]["dispatch_required"] is True
    assert observed["item"]["execution_claim_id"] is None
    assert (await _dispatch(backend, cycle_id, "ds03-reconnect"))["phase"] == "COMPLETED_FOR_REVIEW"


@pytest.mark.asyncio
async def test_ds04_restart_from_next_step_ready_resumes_without_step_zero_replay(
    dispatcher_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    _install_normal_lane(monkeypatch, dispatcher_repo, step_count=2, captured=captured)
    projection_root = tmp_path / "projections"
    first = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False,
        projection_store=JsonFileProjectionStore(projection_root),
    )
    run = await _approve(first, key="ds04")
    cycle_id = run["started"]["cycle_id"]
    second = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False,
        projection_store=JsonFileProjectionStore(projection_root),
    )
    assert await second.rehydrate_cycle(tenant_id="tenant-dsd", cycle_id=cycle_id)
    assert second._cycles[cycle_id].dispatch_state == "NEXT_STEP_READY"  # noqa: SLF001
    restarted = await _dispatch(second, cycle_id, "ds04-restart")
    assert restarted["phase"] == "COMPLETED_FOR_REVIEW", restarted
    assert captured == ["step-r1-0", "step-r1-1"]


@pytest.mark.asyncio
async def test_ds05_valid_and_expired_execution_claims_never_duplicate(
    dispatcher_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    _install_normal_lane(monkeypatch, dispatcher_repo, step_count=2, captured=captured)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    run = await _approve(backend, key="ds05")
    cycle = backend._cycles[run["started"]["cycle_id"]]  # noqa: SLF001
    cycle.dispatch_state = "EXECUTING"
    cycle.dispatch_required = False
    cycle.execution_claim_id = "claim-existing"
    cycle.execution_claim_step_id = cycle.current_step_cursor
    cycle.step_lease_expires_at = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    valid = await backend.dispatch_ready_step(
        tenant_id="tenant-dsd", cycle_id=cycle.cycle_id, command_id="ds05-valid"
    )
    assert valid["idempotent_dispatch"] is True
    cycle.step_lease_expires_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    expired = await backend.dispatch_ready_step(
        tenant_id="tenant-dsd", cycle_id=cycle.cycle_id, command_id="ds05-expired"
    )
    assert expired["ok"] is False
    assert cycle.execution_claim_expired is True
    assert captured == ["step-r1-0"]


@pytest.mark.asyncio
async def test_ds06_duplicate_resume_has_one_claim_winner(
    dispatcher_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    _install_normal_lane(monkeypatch, dispatcher_repo, step_count=2, captured=captured)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    run = await _approve(backend, key="ds06")
    cycle_id = run["started"]["cycle_id"]

    async def resume(key: str) -> dict[str, Any]:
        return await backend.command(
            operation="resume_cycle", tenant_id="tenant-dsd", actor_id="human",
            resource_id=cycle_id, body={}, idempotency_key=key, expected_version=None,
        )

    await asyncio.gather(resume("ds06-a"), resume("ds06-b"))
    assert captured.count("step-r1-1") == 1
    assert len(_claim_events(backend, cycle_id)) == 2


@pytest.mark.asyncio
async def test_ds07_duplicate_dispatcher_invocation_executes_once(
    dispatcher_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    _install_normal_lane(monkeypatch, dispatcher_repo, step_count=2, captured=captured)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    run = await _approve(backend, key="ds07")
    cycle_id = run["started"]["cycle_id"]
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_execute(**kwargs: Any) -> RealExecuteResult:
        captured.append(kwargs["step_contract"].id)
        entered.set()
        await release.wait()
        return RealExecuteResult(
            ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=True,
            artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
            objective_status="COMPLETED", real_operations=1, target_files_modified=1,
            diff_text="+ once\n",
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", slow_execute
    )
    first = asyncio.create_task(backend.dispatch_ready_step(
        tenant_id="tenant-dsd", cycle_id=cycle_id, command_id="ds07-first"
    ))
    await entered.wait()
    duplicate = await backend.dispatch_ready_step(
        tenant_id="tenant-dsd", cycle_id=cycle_id, command_id="ds07-second"
    )
    release.set()
    await first
    assert duplicate["idempotent_dispatch"] is True
    assert captured.count("step-r1-1") == 1


async def _chat_verdicts(verdicts: Iterable[str]):
    iterator = iter(verdicts)

    async def chat(**kwargs: Any) -> ChatCompletionResultV1:
        verdict = next(iterator)
        return ChatCompletionResultV1(
            output_text=json.dumps({"verdict": verdict, "reasons": [verdict], "criteria_results": []}),
            provider="test", resolved_provider="test", model="test", usage={},
        )

    return chat


@pytest.mark.asyncio
async def test_ds08_verification_repair_persists_before_redispatch(
    dispatcher_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    _install_normal_lane(monkeypatch, dispatcher_repo, step_count=1, captured=captured)
    monkeypatch.setattr("backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", True)
    monkeypatch.setattr("backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED", True)
    monkeypatch.setattr(
        "backend.aix_core.runtime.verification.semantic_verifier.chat_completion",
        await _chat_verdicts(("REPAIR", "PASS", "PASS")),
    )
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    run = await _approve(backend, key="ds08")
    cycle_id = run["started"]["cycle_id"]
    assert run["approved"]["phase"] == "STEP_READY"
    assert len(backend._failure_evidence[run["admit"]["objective_id"]]) == 1  # noqa: SLF001
    assert len(captured) == 1
    assert (await _dispatch(backend, cycle_id, "ds08-repair"))["phase"] == "COMPLETED_FOR_REVIEW"
    assert len(captured) == 2


@pytest.mark.asyncio
async def test_ds09_replan_revision_persists_before_revised_dispatch(
    dispatcher_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    _install_normal_lane(monkeypatch, dispatcher_repo, step_count=1, captured=captured)
    calls = {"n": 0}

    async def propose(**kwargs: Any) -> StepContractProposalV1:
        calls["n"] += 1
        plan = _plan(kwargs, count=1, revision=calls["n"])
        return StepContractProposalV1(contract=plan.steps[0], plan=plan, reply="replan", reason=None)

    monkeypatch.setattr("backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract", propose)
    monkeypatch.setattr("backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", True)
    monkeypatch.setattr("backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED", True)
    monkeypatch.setattr(
        "backend.aix_core.runtime.verification.semantic_verifier.chat_completion",
        await _chat_verdicts(("REPLAN", "PASS", "PASS")),
    )
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    run = await _approve(backend, key="ds09")
    assert run["approved"]["phase"] == "STEP_READY"
    # Approve runs planner once, executes, REPLAN auto-continues, then plans revision 2
    # *before* emitting the next STEP_READY (never null-id ready).
    assert calls["n"] == 2
    revisions = backend._plan_revisions[run["admit"]["objective_id"]]  # noqa: SLF001
    assert [revision.revision for revision in revisions] == [1, 2]
    assert (await _dispatch(backend, run["started"]["cycle_id"], "ds09-r2"))["phase"] == "COMPLETED_FOR_REVIEW"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_ds10_needs_more_evidence_never_mutates_before_contract(
    dispatcher_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    _install_normal_lane(monkeypatch, dispatcher_repo, step_count=1, captured=captured)
    calls = {"n": 0}

    async def propose(**kwargs: Any) -> StepContractProposalV1:
        calls["n"] += 1
        if calls["n"] == 1:
            return StepContractProposalV1(
                contract=None, plan=None, reply="inspect service.py",
                reason="NEEDS_MORE_EVIDENCE:symbol location",
            )
        plan = _plan(kwargs, count=1)
        assert captured == []
        return StepContractProposalV1(contract=plan.steps[0], plan=plan, reply="grounded", reason=None)

    monkeypatch.setattr("backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract", propose)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    run = await _approve(backend, key="ds10")
    assert calls["n"] >= 1
    assert captured == [] or captured == ["step-r1-0"]
    phase = run["approved"].get("phase")
    if phase != "COMPLETED_FOR_REVIEW":
        done = await _dispatch(backend, run["started"]["cycle_id"], "ds10-step")
        phase = done.get("phase")
        if calls["n"] == 1 and captured == []:
            done = await _dispatch(backend, run["started"]["cycle_id"], "ds10-step-2")
            phase = done.get("phase")
    assert calls["n"] >= 2
    assert captured
    assert all(item == "step-r1-0" for item in captured)
    assert phase == "COMPLETED_FOR_REVIEW"
