"""AIX gap-closure sprint 3, Priority 5: real cancellation.

Cancellation must actually stop an in-flight, deliberately long-running command --
not just flip a status flag while the subprocess keeps running to completion.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture
def slow_test_repo(tmp_path: Path) -> Path:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "config.py").write_text("X = 1\n", encoding="utf-8", newline="\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'svc'\n", encoding="utf-8", newline="\n")
    (tmp_path / "tests").mkdir()
    # A genuinely long-running command -- 20s real sleep inside a real pytest test.
    (tmp_path / "tests" / "test_slow.py").write_text(
        "import time\n\ndef test_slow():\n    time.sleep(20)\n    assert True\n",
        encoding="utf-8", newline="\n",
    )
    _init_git_repo(tmp_path)
    return tmp_path


def _seed_run_tests_contract(backend: DurableObjectiveCycleApiBackend, *, objective_id: str, cycle_id: str) -> None:
    """Force the cancellable RUN_TESTS lane (not mkdir / empty-diff mutation)."""
    backend._step_contracts[objective_id] = StepContractV1(  # noqa: SLF001
        cycle_id=cycle_id,
        tenant_id="tenant-1",
        step_index=0,
        purpose="validate: run the affected test suite",
        target_scope=("pyproject.toml",),
        expected_output_schema_ref="aix.step_contract.v1.proposal",
        acceptance_criteria=("validate the suite completed",),
        validation_commands=("true; use-repository-default-tests",),
        assigned_role="executor",
        step_budget=BudgetV1(
            time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1
        ),
        obligation_refs=(),
        status="PENDING",
    )
    obj = backend._objectives[objective_id]  # noqa: SLF001
    cycle = backend._cycles[cycle_id]  # noqa: SLF001
    backend._ensure_accepted_plan_for_seeded_contract(  # noqa: SLF001
        obj=obj, cycle=cycle, allow_test_bootstrap=True
    )


@pytest.mark.no_plan_bootstrap
@pytest.mark.asyncio
async def test_cancel_actually_terminates_a_long_running_command(
    slow_test_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        json.dumps({"svc": str(slow_test_repo).replace("\\", "/")}),
    )
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    admit = await backend.command(
        operation="admit_objective", tenant_id="t", actor_id="h", resource_id=None,
        body={
            "workhub_task_id": "wh-1", "task_class": "bounded_repair", "definition_of_done": ("done",),
            "repository_id": "svc", "allowed_files": (), "objective": "Run affected tests",
        },
        idempotency_key="admit-1", expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle", tenant_id="t", actor_id="h", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 5}},
        idempotency_key="start-1", expected_version=None,
    )
    _seed_run_tests_contract(
        backend,
        objective_id=admit["objective_id"],
        cycle_id=started["cycle_id"],
    )

    t0 = time.monotonic()

    async def do_approve() -> dict:
        return await backend.command(
            operation="validate_approval_reference", tenant_id="t", actor_id="h", resource_id=started["cycle_id"],
            body={"approval_reference": "workhub://approvals/1"},
            idempotency_key="approve-1", expected_version=started["version"],
        )

    approve_task = asyncio.create_task(do_approve())
    await asyncio.sleep(1.5)  # let the subprocess actually start
    cancel_result = await backend.command(
        operation="cancel_cycle", tenant_id="t", actor_id="h", resource_id=started["cycle_id"],
        body={"reason": "operator requested cancellation"},
        idempotency_key="cancel-1", expected_version=None,
    )
    assert cancel_result["ok"] is True

    approved = await approve_task
    elapsed = time.monotonic() - t0

    # The test sleeps 20s; a real kill returns well under that. A generous bound
    # (15s) avoids CI flakiness while still proving the process was actually killed,
    # not waited out.
    assert elapsed < 15, f"cancellation did not stop the 20s sleep (took {elapsed:.1f}s)"
    assert approved["ok"] is False
    assert approved["phase"] == "CANCELLED"
    assert approved["terminal_reason"] == "CANCELLED"

    # Never allow cancelled work to later become COMPLETED.
    retry_attempt = await backend.command(
        operation="retry_step", tenant_id="t", actor_id="h", resource_id=started["cycle_id"],
        body={"reason": "try again after cancel"},
        idempotency_key="retry-after-cancel", expected_version=None,
    )
    assert retry_attempt["phase"] == "CANCELLED"
