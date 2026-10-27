"""AIX-REAL-EXECUTION-003/004: explore-before-plan gate + per-objective execution
context, driven through the production admission and cycle path
(`DurableObjectiveCycleApiBackend.command`), not through internal helpers directly."""

from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from pathlib import Path

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.planner import exploration_planner as exploration_planner_module


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture
def auth_repo(tmp_path: Path) -> Path:
    (tmp_path / "backend" / "auth").mkdir(parents=True)
    (tmp_path / "backend" / "auth" / "login.py").write_text(
        "def login(username, password):\n    return authenticate(username, password)\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "backend" / "auth" / "middleware.py").write_text(
        "def auth_middleware(request):\n    return request\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "README.md").write_text("auth service\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    return tmp_path


async def _admit_start_approve(
    backend: DurableObjectiveCycleApiBackend,
    *,
    objective: str,
    repository_id: str,
    key: str,
) -> dict:
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": objective,
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": repository_id,
            "allowed_files": (),
        },
        idempotency_key=f"admit-{key}",
        expected_version=None,
    )
    assert admit["ok"] is True, admit
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key=f"start-{key}",
        expected_version=None,
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
    return approved


@pytest.mark.asyncio
async def test_review_family_explores_target_repository_before_planning(
    auth_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"auth-service": "{auth_repo.as_posix()}"}}'
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False
    )
    approved = await _admit_start_approve(
        backend, objective="Review this service", repository_id="auth-service", key="review1"
    )
    assert approved["ok"] is True
    objective_id = list(backend._objectives.keys())[0]  # noqa: SLF001
    record = backend._objectives[objective_id]  # noqa: SLF001
    assert record.task_family == "REVIEW"
    context = record.execution_context
    assert context is not None
    assert context["directories_listed"], "REVIEW must list the target repository for real"
    assert context["files_read"], "REVIEW must read at least one real file for real"


@pytest.mark.asyncio
async def test_debug_family_explores_before_planning(auth_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"auth-service": "{auth_repo.as_posix()}"}}'
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False
    )
    approved = await _admit_start_approve(
        backend, objective="Why is login failing?", repository_id="auth-service", key="debug1"
    )
    assert approved["ok"] is True, approved
    objective_id = list(backend._objectives.keys())[0]  # noqa: SLF001
    record = backend._objectives[objective_id]  # noqa: SLF001
    assert record.task_family == "DEBUG"
    context = record.execution_context
    assert context is not None
    # A real search for auth/login-related terms and a real read must have happened
    # before any execution/mutation plan was attempted.
    assert context["searches_executed"] or context["files_read"]


@pytest.mark.asyncio
async def test_greeting_then_real_request_binds_and_executes_for_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("SMTP_HOST=smtp.example.com\n", encoding="utf-8", newline="\n")
    (tmp_path / "README.md").write_text("placeholder\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"hyeve-integration-service": "{tmp_path.as_posix()}"}}'
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False
    )
    target = tmp_path / ".env"
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": "placeholder, overridden by messages[] binding",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "hyeve-integration-service",
            "allowed_files": (),
            "conversation_id": "conv_greet",
            "messages": [
                {"message_id": "m1", "role": "user", "content": "Hey"},
                {
                    "message_id": "m2",
                    "role": "user",
                    "content": f"{target} read this file",
                    "parent_message_id": "m1",
                },
            ],
        },
        idempotency_key="admit-greet1",
        expected_version=None,
    )
    assert admit["ok"] is True
    assert admit["objective"] == f"{target} read this file"
    assert admit["objective_source_message_id"] == "m2"

    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-greet1",
        expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/greet1"},
        idempotency_key="approve-greet1",
        expected_version=started["version"],
    )
    assert approved["ok"] is True
    real = approved["real_execute"]
    assert real is not None
    assert real["objective_status"] == "COMPLETED"
    assert real["real_operations"] >= 1
    assert real["read_file"] is not None


@pytest.mark.asyncio
async def test_missing_capability_blocks_truthfully(
    auth_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan step naming an unregistered capability must block with
    `CAPABILITY_NOT_REGISTERED` -- never silently skipped, never executed on faith."""
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"auth-service": "{auth_repo.as_posix()}"}}'
    )

    from backend.aix_core.runtime.planner.exploration_planner import ExplorationPlan, ExplorationStep

    def _fake_plan(**_kwargs: object) -> ExplorationPlan:
        return ExplorationPlan(
            task_family="REVIEW",
            steps=(
                ExplorationStep(
                    step_id="explore-001",
                    name="Run an unregistered tool",
                    action="shell.execute_arbitrary",
                    capability="shell.execute_arbitrary",
                    arguments={},
                ),
            ),
            stop_condition="understanding_complete_or_clarification_required",
            understanding_criteria=(),
            max_steps=8,
        )

    monkeypatch.setattr(exploration_planner_module, "build_exploration_plan", _fake_plan)
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.build_exploration_plan", _fake_plan
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False
    )
    approved = await _admit_start_approve(
        backend, objective="Review this service", repository_id="auth-service", key="cap1"
    )
    assert approved["ok"] is False
    assert approved["error"]["code"] == "CAPABILITY_NOT_REGISTERED"
    snapshot = backend._projections.latest_snapshot(  # noqa: SLF001
        tenant_id="tenant-1",
        aggregate_type="run",
        aggregate_id=approved["cycle_id"],
    )
    assert snapshot is not None
    assert snapshot.snapshot["decision"]["phase"] == "BLOCKED"
    assert snapshot.snapshot["decision"]["terminal_reason"] == "CAPABILITY_NOT_REGISTERED"


@pytest.mark.asyncio
async def test_execution_context_survives_resume_and_clarification(
    auth_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second gate pass for the same objective (simulating resume/retry) must
    reuse the persisted context rather than starting from a blank slate."""
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"auth-service": "{auth_repo.as_posix()}"}}'
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": "Review this service",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "auth-service",
            "allowed_files": (),
        },
        idempotency_key="admit-resume1",
        expected_version=None,
    )
    record = backend._objectives[admit["objective_id"]]  # noqa: SLF001

    class _FakeCycle:
        engine = None
        tenant_id = "tenant-1"
        cycle_id = "cyc_fake"
        objective_id = admit["objective_id"]
        phase = "RUNNING_STEP"
        terminal_reason = None
        events: list = []  # type: ignore[assignment]
        trace_id = "trc_fake"
        event_sequence = 0

    cycle = _FakeCycle()
    first = await backend._run_exploration_gate(cycle=cycle, obj=record)  # noqa: SLF001
    first_files_read = len(record.execution_context["files_read"])
    assert first_files_read >= 1

    # Simulate resume: a second gate pass for the SAME objective must not re-issue an
    # identical read (fingerprint dedup) and must retain everything already recorded.
    second = await backend._run_exploration_gate(cycle=cycle, obj=record)  # noqa: SLF001
    assert len(record.execution_context["files_read"]) == first_files_read
    assert record.execution_context["understanding_complete"] is True
    assert first is None or second is None


@pytest.mark.asyncio
async def test_repository_resolution_does_not_block_the_api_event_loop(
    auth_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow recursive repository scan must run off the Uvicorn event loop.

    This is the regression behind the live symptom where port 8100 remained in
    LISTENING state but /health timed out during approve/resume exploration.
    """
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        f'{{"auth-service": "{auth_repo.as_posix()}"}}',
    )
    scan_started = threading.Event()

    def _slow_class_scan(*_args: object, **_kwargs: object) -> list[str]:
        scan_started.set()
        time.sleep(0.25)
        return []

    monkeypatch.setattr(
        "backend.aix_core.intelligence.repository.class_path_resolve.resolve_class_hints_in_repository",
        _slow_class_scan,
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-loop",
            "objective": "Review CronJobController",
            "task_class": "bounded_repair",
            "definition_of_done": ("reviewed",),
            "repository_id": "auth-service",
            "allowed_files": (),
        },
        idempotency_key="admit-loop-responsive",
        expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-loop-responsive",
        expected_version=None,
    )
    gate_task = asyncio.create_task(
        backend._run_exploration_gate(  # noqa: SLF001
            cycle=backend._cycles[started["cycle_id"]],  # noqa: SLF001
            obj=backend._objectives[admit["objective_id"]],  # noqa: SLF001
        )
    )
    assert await asyncio.to_thread(scan_started.wait, 1.0)
    tick_started = time.perf_counter()
    await asyncio.sleep(0.02)
    assert time.perf_counter() - tick_started < 0.10
    await gate_task
