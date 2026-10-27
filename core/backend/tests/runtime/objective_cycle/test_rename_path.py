"""AIX-GAEF-001: bound filesystem rename dispatches through the governed capability
registry -> policy gateway -> executor (StepContractV1.actions), never through a
classify_intent phrase branch."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.aix_core.runtime.executor.filesystem_primitives import (
    looks_like_rename_path_objective,
    resolve_rename_paths,
)
from backend.aix_core.runtime.executor.real_execute import (
    RealExecuteResult,
    _run_action_request_execute,
    classify_intent,
)
from backend.aix_core.runtime.objective.contracts import ActionRequestV1, BudgetV1, StepContractV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


def test_classify_intent_no_longer_has_a_rename_phrase_branch() -> None:
    """AIX-GAEF-001 rule 4: classify_intent must not gain new phrase branches for
    capabilities the foundation package already handles via ActionRequestV1."""
    objective = "Rename banner.png to test.png"
    assert classify_intent(objective, target_path=None, symbol_name=None) != "RENAME_PATH"


def test_rename_paths_resolved_from_objective_and_allowed_files() -> None:
    assert looks_like_rename_path_objective("Rename banner.png to test.png")
    assert resolve_rename_paths(
        objective="Rename assets/banner.png to test.png",
        allowed_files=("assets/banner.png",),
    ) == ("assets/banner.png", "assets/test.png")


def test_bare_word_symbol_rename_is_not_a_path_rename() -> None:
    """Live WIDEN-w3: 'rename berth_fee to dock_fee ... tests/test_ship.py'
    moved the test file to tests/dock_fee. Bare-word operands are symbols."""
    objective = (
        "In the cycle workspace, rename berth_fee to dock_fee in the existing "
        "file ship.py and update tests/test_ship.py to import and call dock_fee "
        "instead (same behavior, same default rate). "
        "Verify by running the project's pytest suite."
    )
    assert looks_like_rename_path_objective(objective) is False
    # The detector gates every rename caller; a False here means the symbol
    # rename flows to the EDIT mutation path with full verification, and the
    # resolver itself refuses bare-word pairs as defense in depth.
    assert (
        resolve_rename_paths(
            objective=objective,
            allowed_files=("ship.py", "tests/test_ship.py"),
        )
        is None
    )


def test_extensionless_file_rename_still_detected_when_explicit() -> None:
    assert looks_like_rename_path_objective("Rename the file README to CHANGELOG") is True
    assert looks_like_rename_path_objective("Rename src/old.py to src/new.py") is True
    assert looks_like_rename_path_objective("Rename count to total in analytics") is False


@pytest.mark.asyncio
async def test_run_action_request_execute_renames_a_real_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "banner.png").write_bytes(b"fake-png-bytes")

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
        purpose="Rename banner.png to test.png",
        target_scope=("banner.png", "test.png"),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("test.png exists", "banner.png no longer exists"),
        validation_commands=("test -f test.png",),
        assigned_role="executor",
        step_budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1),
        actions=(
            ActionRequestV1(
                capability="filesystem",
                operation="rename",
                arguments={"source": "banner.png", "destination": "test.png"},
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
        allowed_files=("banner.png", "test.png"),
    )
    assert isinstance(result, RealExecuteResult)
    assert result.ok is True
    assert result.objective_status == "COMPLETED"
    assert not (repo / "banner.png").exists()
    assert (repo / "test.png").is_file()
    assert (repo / "test.png").read_bytes() == b"fake-png-bytes"
    assert any(c["path"] == "test.png" for c in result.changed_source_files)
    assert result.diff_text is not None
    assert "+++ b/test.png" in result.diff_text
    assert "--- a/banner.png" in result.diff_text


@pytest.mark.asyncio
async def test_bound_rename_does_not_call_llm_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _planner_must_not_run(**kwargs: object) -> object:
        raise AssertionError("LLM planner must not run for bound rename")

    executed: dict[str, object] = {}

    async def _fake_execute(**kwargs: object) -> RealExecuteResult:
        executed["step_contract"] = kwargs.get("step_contract")
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
            changed_source_files=[{"path": "test.png", "additions": 0, "deletions": 0}],
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
    objective = "Rename banner.png to test.png"
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "task-rename",
            "objective": objective,
            "task_class": "single_file_helper",
            "definition_of_done": (objective,),
            "repository_id": "aix-frontend",
            "allowed_files": ("banner.png",),
            "project_id": "proj-1",
        },
        idempotency_key="admit-rename",
        expected_version=None,
    )
    assert admit["ok"] is True, admit
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-rename",
        expected_version=None,
    )
    assert started["ok"] is True, started
    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-rename",
        expected_version=started.get("version"),
    )
    assert approved.get("error", {}).get("code") != "NEEDS_MORE_EVIDENCE", approved
    assert approved.get("terminal_reason") != "NEEDS_MORE_EVIDENCE", approved
    oid = admit["objective_id"]
    contract = backend._step_contracts.get(oid)
    assert contract is not None
    assert contract.actions
    assert contract.actions[0].capability == "filesystem"
    assert contract.actions[0].operation == "rename"
    assert contract.actions[0].arguments == {"source": "banner.png", "destination": "test.png"}
    plan = backend._active_plan_revision(oid)
    assert plan is not None
    assert str(plan.status) == "ACCEPTED"
    assert executed.get("step_contract") is not None
