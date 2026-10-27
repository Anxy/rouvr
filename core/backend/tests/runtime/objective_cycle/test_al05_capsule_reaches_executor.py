"""Autonomous Closed-Loop Lane, Packet AL-05: the context capsule's invariants and
previous-failure reach the executor's objective text -- additive, and absent by
default (no capsule -> zero behavior change, same pattern as every other opt-in
piece in this program)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import StepContextCapsuleV1
from backend.aix_core.runtime.executor.real_execute import run_real_execute


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture
def small_repo(tmp_path: Path) -> Path:
    (tmp_path / "FeeDue.php").write_text("<?php\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_invariants_and_previous_failure_reach_the_objective_text(
    small_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"visions": "{small_repo.as_posix()}"}}'
    )
    captured: dict[str, Any] = {}

    async def _fake_mutation(**kwargs: Any) -> Any:
        captured.update(kwargs)
        raise SystemExit("stop after capture")

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.real_execute.run_multi_step_mutation", _fake_mutation
    )
    capsule = StepContextCapsuleV1(
        step_id="step-1",
        invariants=("do not change the return shape", "do not touch CAJP conveyance"),
        relevant_previous_failure="installmentCountByFsmId declared but never used to change behavior",
    )
    with pytest.raises(SystemExit):
        await run_real_execute(
            tenant_id="tenant-1", cycle_id="cyc-1", objective_id="obj-1",
            objective="refactor and optimise FeeDue.php", workhub_task_id="wh-1", project_id=None,
            task_number=None, repository_id="visions", require_workhub_load=False,
            workspace_base=tmp_path / "ws", context_capsule=capsule,
        )
    assert "Invariants (must not violate): do not change the return shape; do not touch CAJP conveyance" in captured["objective"]
    assert "Previous attempt failed verification: installmentCountByFsmId declared but never used" in captured["objective"]


@pytest.mark.asyncio
async def test_no_capsule_is_zero_behavior_change(
    small_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"visions": "{small_repo.as_posix()}"}}'
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
            workspace_base=tmp_path / "ws",
        )
    assert captured["objective"] == "refactor and optimise FeeDue.php"
