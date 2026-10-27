"""AIX Closed-Loop Execution Plan, Sprint W11: acceptance-matrix reconciliation.

Audited the three existing suites against the plan's Phase 12 proposed scenario list
(simple edit / large-file edit / multi-file feature / bug fix / refactor / new
component / test-only / documentation / investigation-required / deliberately-failing /
wrong-plan) rather than building a fourth suite from scratch:

- `test_general_execution_acceptance.py` (aix-execution-005's "Acceptance matrix A-H"):
  covers simple edit (A), large-file multi-section edit, refactor (rename symbol across
  files, E), multi-file create+modify, and -- notably -- "deliberately-failing"
  (`test_bounded_repair_round_after_failing_tests`: AIX must repair itself after a
  first attempt fails tests).
- `test_cursor_class_acceptance_matrix.py` + `test_exploration_gate_integration.py`:
  cover investigation-required (search/read before planning; "why is login failing").
- This session's own Sprint W9/W10 tests
  (`test_semantic_layer_catches_stub_pattern_and_requests_repair`,
  `test_replan_verdict_produces_a_decision_and_returns_to_waiting_approval`): the
  "wrong-plan task" scenario the plan names explicitly did not exist in any suite
  before this session -- W10's test is now that scenario's real coverage.

Two genuine, confirmed gaps remained after that audit (grepped for
`docs_only_change`/`phpdoc`/`test_generation`-shaped acceptance coverage across the
whole `objective_cycle` test tree -- none found): **documentation** (a docs-only,
executable-code-untouched objective) and **test-only** (add regression coverage, no
other change). Both filled here, driven through the real admission path like every
other test this session has added -- not a new, separate mini-suite; same fixtures,
same style as `test_exploration_gate_integration.py`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1, UnderstandingEvidenceV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture
def small_repo(tmp_path: Path) -> Path:
    (tmp_path / "helper.php").write_text(
        "<?php\nfunction addTwo($a, $b) { return $a + $b; }\n", encoding="utf-8", newline="\n",
    )
    (tmp_path / "HelperTest.php").write_text(
        "<?php\n// existing tests\n", encoding="utf-8", newline="\n",
    )
    _init_git_repo(tmp_path)
    return tmp_path


async def _run_real_execute_ok(**kwargs: Any) -> RealExecuteResult:
    return RealExecuteResult(
        ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=True,
        artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
        objective_status="COMPLETED", real_operations=1, target_files_modified=1,
        completion_gates={"gates": {"diff_exists": True}, "verification_verdict": "PASSED"},
    )


async def _admit_start_approve(
    backend: DurableObjectiveCycleApiBackend, *, objective: str, task_class: str, allowed_files: tuple[str, ...],
) -> dict[str, Any]:
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={
            "workhub_task_id": "task-101", "objective": objective, "task_class": task_class,
            "definition_of_done": ("done",), "repository_id": "small-repo",
            "allowed_files": allowed_files, "project_id": "proj-1",
        },
        idempotency_key="admit-1", expected_version=None,
    )
    assert admit["ok"] is True, admit
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}}, idempotency_key="start-1",
        expected_version=None,
    )
    backend._understanding_evidence[admit["objective_id"]] = UnderstandingEvidenceV1(  # noqa: SLF001
        objective_id=admit["objective_id"], cycle_id=started["cycle_id"],
        files_read=allowed_files or ("helper.php",),
        observed_problem="test evidence", understanding_complete=True,
    )
    backend._step_contracts[admit["objective_id"]] = StepContractV1(  # noqa: SLF001
        cycle_id=started["cycle_id"], tenant_id="tenant-1", step_index=0,
        purpose="seeded execute for envelope scenario",
        target_scope=allowed_files or ("helper.php",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("seeded acceptance is independently verifiable",),
        validation_commands=("echo ok",),
        assigned_role="executor",
        step_budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1),
        obligation_refs=tuple(o.id for o in backend._obligations.get(admit["objective_id"], ())),  # noqa: SLF001
        status="PENDING",
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-1", expected_version=started["version"],
    )
    return {"admit": admit, "approved": approved}


@pytest.mark.asyncio
async def test_documentation_scenario_admits_with_a_real_envelope(
    small_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan's Phase 12 'Documentation' scenario: 'Update API documentation for the new
    endpoint' / FeeDue A4's restraint case. Confirms the pipeline handles
    `docs_only_change` end to end -- envelope construction succeeds directly (it's
    already a supported task_class, no Sprint W1 mapping fallback needed) and the
    cycle completes."""
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"small-repo": "{small_repo.as_posix()}"}}'
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    result = await _admit_start_approve(
        backend, objective="Add PHPDoc to addTwo. Do not change executable code.",
        task_class="docs_only_change", allowed_files=("helper.php",),
    )
    assert result["admit"]["envelope_constructed"] is True
    assert result["admit"]["envelope_construction_reason"] is None
    assert result["approved"]["ok"] is True, result["approved"]


@pytest.mark.asyncio
async def test_test_only_scenario_admits_with_a_real_envelope(
    small_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan's Phase 12 'Test-only task' scenario: 'Add regression coverage for this
    bug', no other change. Confirms `test_generation` (also already a supported
    task_class) admits and completes end to end."""
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"small-repo": "{small_repo.as_posix()}"}}'
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute_ok
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    result = await _admit_start_approve(
        backend, objective="Add a regression test proving addTwo handles negative numbers.",
        task_class="test_generation", allowed_files=("HelperTest.php",),
    )
    assert result["admit"]["envelope_constructed"] is True
    assert result["approved"]["ok"] is True, result["approved"]
