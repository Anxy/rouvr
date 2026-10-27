"""Create-empty-directory must mkdir + .gitkeep, not edit unrelated existing files."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.aix_core.runtime.executor.real_execute import (
    RealExecuteResult,
    _run_create_empty_directory_execute,
    looks_like_create_empty_directory_objective,
    resolve_empty_directory_marker_path,
    _run_action_request_execute,
)
from backend.aix_core.runtime.objective.contracts import ActionRequestV1, BudgetV1, StepContractV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.objective.task_family import classify_task_family


def test_task_family_create_for_empty_directory() -> None:
    result = classify_task_family(
        "Create an empty directory named my-web-app at my-web-app"
    )
    assert result.task_family == "CREATE"


def test_marker_prefers_gitkeep_allowed_file() -> None:
    assert (
        resolve_empty_directory_marker_path(
            objective="Create an empty directory",
            allowed_files=("my-web-app/.gitkeep",),
        )
        == "my-web-app/.gitkeep"
    )


def test_vague_directory_not_file_without_name_is_still_create_intent() -> None:
    assert looks_like_create_empty_directory_objective(
        "just any path and any name it is a directory not file create named my-web-app"
    )


@pytest.mark.asyncio
async def test_create_empty_directory_writes_gitkeep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "FileInput.tsx").write_text("export const FileInput = () => null;\n", encoding="utf-8")

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.real_execute.resolve_repository_root",
        lambda repository_id, settings=None: repo,
    )

    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = await _run_create_empty_directory_execute(
        objective="Create an empty directory named my-web-app at my-web-app. Track it with my-web-app/.gitkeep.",
        repository_id="aix-frontend",
        settings=None,
        workspace=workspace,
        workhub_loaded=False,
        artifacts={},
        apply_edits=True,
        allowed_files=("my-web-app/.gitkeep",),
    )
    assert result.ok is True
    marker = repo / "my-web-app" / ".gitkeep"
    assert marker.is_file()
    assert (repo / "src" / "FileInput.tsx").read_text(encoding="utf-8").startswith("export const FileInput")
    assert any(c["path"] == "my-web-app/.gitkeep" for c in result.changed_source_files)


@pytest.mark.asyncio
async def test_run_action_request_execute_mkdirs_via_gitkeep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.real_execute.resolve_repository_root",
        lambda repository_id, settings=None: repo,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.foundation.capability_executor.resolve_repository_root",
        lambda repository_id, settings=None: repo,
    )

    workspace = tmp_path / "ws"
    workspace.mkdir()
    contract = StepContractV1(
        cycle_id="cyc-1",
        tenant_id="tenant-1",
        step_index=0,
        purpose="Create empty directory marker my-web-app/.gitkeep",
        target_scope=("my-web-app/.gitkeep",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("my-web-app/.gitkeep exists",),
        validation_commands=("test -f my-web-app/.gitkeep",),
        assigned_role="executor",
        step_budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1),
        actions=(
            ActionRequestV1(
                capability="filesystem",
                operation="mkdir",
                arguments={"path": "my-web-app/.gitkeep", "marker": True},
            ),
        ),
        status="PENDING",
    )

    result = await _run_action_request_execute(
        step_contract=contract,
        repository_id="aix-frontend",
        settings=None,
        workspace=workspace,
        workhub_loaded=False,
        artifacts={},
        apply_edits=True,
        allowed_files=("my-web-app/.gitkeep",),
    )
    assert result.ok is True
    assert (repo / "my-web-app" / ".gitkeep").is_file()
    assert any(c["path"] == "my-web-app/.gitkeep" for c in result.changed_source_files)


@pytest.mark.asyncio
async def test_bound_create_directory_does_not_call_llm_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _planner_must_not_run(**kwargs: object) -> object:
        raise AssertionError("LLM planner must not run for bound create-directory")

    executed: dict[str, object] = {}

    async def _fake_execute(**kwargs: object) -> RealExecuteResult:
        executed["allowed_files"] = kwargs.get("allowed_files")
        executed["objective"] = kwargs.get("objective")
        return RealExecuteResult(
            ok=True,
            code="REAL_EXECUTE_OK",
            workspace="ws",
            evidence_refs=[],
            workhub_loaded=False,
            artifacts={},
            execution_status="COMPLETED",
            verification_status="PASSED",
            objective_status="COMPLETED",
            real_operations=1,
            target_files_modified=1,
            changed_source_files=[{"path": "aix-new-folder/.gitkeep", "additions": 0, "deletions": 0}],
        )

    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract",
        _planner_must_not_run,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        _fake_execute,
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        create_workhub_tasks=False,
    )
    objective = (
        "Create an empty directory named aix-new-folder at aix-new-folder. "
        "Track it with aix-new-folder/.gitkeep. Do not modify any existing files."
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "task-dir",
            "objective": objective,
            "task_class": "single_file_helper",
            "definition_of_done": (objective,),
            "repository_id": "aix-frontend",
            "allowed_files": ("aix-new-folder/.gitkeep",),
            "project_id": "proj-1",
        },
        idempotency_key="admit-dir",
        expected_version=None,
    )
    assert admit["ok"] is True, admit
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-dir",
        expected_version=None,
    )
    assert started["ok"] is True, started
    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-dir",
        expected_version=started.get("version"),
    )
    assert approved.get("error", {}).get("code") != "NEEDS_MORE_EVIDENCE", approved
    assert approved.get("terminal_reason") != "NEEDS_MORE_EVIDENCE", approved
    oid = admit["objective_id"]
    contract = backend._step_contracts.get(oid)
    assert contract is not None
    assert contract.target_scope == ("aix-new-folder/.gitkeep",)
    assert contract.actions
    assert contract.actions[0].capability == "filesystem"
    assert contract.actions[0].operation == "mkdir"
    assert contract.actions[0].arguments == {"path": "aix-new-folder/.gitkeep", "marker": True}
    plan = backend._active_plan_revision(oid)
    assert plan is not None
    assert str(plan.status) == "ACCEPTED"
    assert executed.get("allowed_files") == ("aix-new-folder/.gitkeep",) or executed.get("objective")
