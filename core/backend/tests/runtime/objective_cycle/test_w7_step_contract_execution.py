"""AIX Closed-Loop Execution Plan, Sprint W7: StepContractV1-aware run_real_execute.

Purely additive parameter -- `step_contract` defaults to `None`. What's under test:
(1) when given, its target_scope becomes the allowed_files default only when the
caller didn't already narrow one; (2) an explicit allowed_files always wins, never
silently widened/replaced; (3) purpose gets appended as planner guidance to the
objective text the mutation runner actually receives; (4) omitting step_contract
(every pre-existing caller) is provably unaffected.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1
from backend.aix_core.runtime.executor.real_execute import run_real_execute


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture
def fee_repo(tmp_path: Path) -> Path:
    (tmp_path / "FeeDue.php").write_text("<?php\nfunction getDueFeeStudent() {}\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    return tmp_path


def _contract(*, target_scope: tuple[str, ...], purpose: str) -> StepContractV1:
    return StepContractV1(
        cycle_id="cyc-1", tenant_id="tenant-1", step_index=0, purpose=purpose,
        target_scope=target_scope, expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("x",), validation_commands=("php -l FeeDue.php",),
        assigned_role="executor",
        step_budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1),
        status="PENDING",
    )


@pytest.mark.asyncio
async def test_step_contract_target_scope_becomes_allowed_files_when_none_given(
    fee_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"visions": "{fee_repo.as_posix()}"}}'
    )
    captured: dict[str, Any] = {}

    async def _fake_mutation(**kwargs: Any) -> Any:
        captured.update(kwargs)
        raise SystemExit("stop after capture")  # short-circuit, we only need the call args

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.real_execute.run_multi_step_mutation", _fake_mutation
    )
    contract = _contract(target_scope=("FeeDue.php",), purpose="Batch the per-student queries")
    with pytest.raises(SystemExit):
        await run_real_execute(
            tenant_id="tenant-1", cycle_id="cyc-1", objective_id="obj-1",
            objective="refactor and optimise FeeDue.php", workhub_task_id="wh-1", project_id=None,
            task_number=None, repository_id="visions", require_workhub_load=False,
            workspace_base=tmp_path / "ws", step_contract=contract,
        )
    assert captured["allowed_files"] == ("FeeDue.php",)
    assert "Planner guidance: Batch the per-student queries" in captured["objective"]


@pytest.mark.asyncio
async def test_explicit_allowed_files_wins_over_step_contract(
    fee_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"visions": "{fee_repo.as_posix()}"}}'
    )
    captured: dict[str, Any] = {}

    async def _fake_mutation(**kwargs: Any) -> Any:
        captured.update(kwargs)
        raise SystemExit("stop after capture")

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.real_execute.run_multi_step_mutation", _fake_mutation
    )
    contract = _contract(target_scope=("SomeOtherFile.php",), purpose="x")
    with pytest.raises(SystemExit):
        await run_real_execute(
            tenant_id="tenant-1", cycle_id="cyc-1", objective_id="obj-1",
            objective="refactor and optimise FeeDue.php", workhub_task_id="wh-1", project_id=None,
            task_number=None, repository_id="visions", require_workhub_load=False,
            workspace_base=tmp_path / "ws", allowed_files=("FeeDue.php",), step_contract=contract,
        )
    # The human-set allowed_files must not be silently widened/replaced by the proposal.
    assert captured["allowed_files"] == ("FeeDue.php",)


@pytest.mark.asyncio
async def test_omitting_step_contract_is_unaffected(
    fee_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"visions": "{fee_repo.as_posix()}"}}'
    )
    captured: dict[str, Any] = {}

    async def _fake_mutation(**kwargs: Any) -> Any:
        captured.update(kwargs)
        raise SystemExit("stop after capture")

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.real_execute.run_multi_step_mutation", _fake_mutation
    )
    with pytest.raises(SystemExit):
        await run_real_execute(
            tenant_id="tenant-1", cycle_id="cyc-1", objective_id="obj-1",
            objective="refactor and optimise FeeDue.php", workhub_task_id="wh-1", project_id=None,
            task_number=None, repository_id="visions", require_workhub_load=False,
            workspace_base=tmp_path / "ws", allowed_files=("FeeDue.php",),
        )
    assert captured["objective"] == "refactor and optimise FeeDue.php"
    assert "Planner guidance" not in captured["objective"]
