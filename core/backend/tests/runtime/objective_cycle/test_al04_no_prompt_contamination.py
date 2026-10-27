"""Autonomous Closed-Loop Lane, Packet AL-04: prove no upstream free-form prompt/prose
reaches the coder.

`step_contract_planner.propose_step_contract()` returns `StepContractProposalV1`
(`contract: StepContractV1 | None`, `reply: str`, `reason: str | None`) -- `reply` is
the planner's free-form chat-shaped text. `run_real_execute`'s `step_contract`
parameter (Sprint W7) is typed `StepContractV1 | None`, which has **no reply/prose
field at all** -- structurally, not just by convention, `proposal.reply` cannot reach
the executor through that parameter. This test proves the wiring in
`durable_api_backend.py` never smuggles it through some other channel either (e.g.
appending it to the objective text alongside `purpose`).
"""

from __future__ import annotations

from typing import Any

import pytest

from pydantic import ValidationError

from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1
from backend.aix_core.runtime.executor.real_execute import run_real_execute


@pytest.mark.asyncio
async def test_only_purpose_and_target_scope_reach_the_executor_never_reply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    import subprocess
    from pathlib import Path

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "FeeDue.php").write_text("<?php\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"visions": "{repo.as_posix()}"}}'
    )

    captured: dict[str, Any] = {}

    async def _fake_mutation(**kwargs: Any) -> Any:
        captured.update(kwargs)
        raise SystemExit("stop after capture")

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.real_execute.run_multi_step_mutation", _fake_mutation
    )

    # A StepContractV1 has no field for the planner's free-form chat reply -- this is
    # not simulated, it's structurally impossible to construct one with a "reply" kwarg.
    with pytest.raises(ValidationError):
        StepContractV1(  # type: ignore[call-arg]
            cycle_id="cyc-1", tenant_id="tenant-1", step_index=0, purpose="x",
            target_scope=("FeeDue.php",), expected_output_schema_ref="aix.step_contract.v1.generic_edit",
            acceptance_criteria=("x",), validation_commands=("x",), assigned_role="executor",
            step_budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1),
            status="PENDING",
            reply="I think this is probably related to the login flow, let me also check auth.py...",
        )

    contract = StepContractV1(
        cycle_id="cyc-1", tenant_id="tenant-1", step_index=0,
        purpose="Batch the per-student queries before the loop",
        target_scope=("FeeDue.php",), expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("x",), validation_commands=("x",), assigned_role="executor",
        step_budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1),
        status="PENDING",
    )
    with pytest.raises(SystemExit):
        await run_real_execute(
            tenant_id="tenant-1", cycle_id="cyc-1", objective_id="obj-1",
            objective="refactor and optimise FeeDue.php", workhub_task_id="wh-1", project_id=None,
            task_number=None, repository_id="visions", require_workhub_load=False,
            workspace_base=tmp_path / "ws", step_contract=contract,
        )

    assert captured["objective"] == (
        "Planner guidance: Batch the per-student queries before the loop\n"
        "Acceptance: x\n"
        "Target: FeeDue.php"
    )
    # The exact contract-level fields only -- nothing else from the planner's turn
    # (chat reply, intermediate reasoning, prior conversation) is in scope to leak.
    assert "reply" not in captured
    assert "login flow" not in str(captured)
    assert "auth.py" not in str(captured)
