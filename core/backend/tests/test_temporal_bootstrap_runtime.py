from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from temporalio.workflow import _Definition

from backend.legacy.aix_orchestrator_v2 import temporal_workflows
from backend.legacy.aix_orchestrator_v2.temporal_workflows import _activity_timeout, _blocked_execution_payload
from backend.services.aix_enveloped_run_service import RunResult
from backend.workflows.temporal.activities import AixTemporalActivities
from backend.infrastructure.temporal.queue_strategy import build_queue_binding, select_task_queue
from backend.infrastructure.temporal.runtime import TemporalRuntimeManager, TemporalSdk
from backend.workflows.temporal.workflows import (
    ALLOWED_WORKFLOW_TRANSITIONS,
    TemporalWorkflowStateMachine,
    TemporalWorkflowTransitionError,
    WorkflowTransitionEvent,
    temporal_registry_linked_states,
)
from hyeve_core.task_lifecycle_registry import REVIEW_PENDING_TASK_STATUSES, TERMINAL_TASK_STATUS


@dataclass
class _Settings:
    TEMPORAL_ENABLED: bool = False
    TEMPORAL_AUTO_START_WORKER: bool = False
    TEMPORAL_ADDRESS: str = "127.0.0.1:7233"
    TEMPORAL_NAMESPACE: str = "hyeve-aix"
    TEMPORAL_TASK_QUEUE_PLAN: str = "aix.plan.current"
    TEMPORAL_TASK_QUEUE_EXECUTION: str = "aix.execution.current"
    TEMPORAL_TASK_QUEUE_VERIFICATION: str = "aix.verification.current"
    TEMPORAL_WORKER_IDENTITY: str = "hyeve-aix-worker"


def test_temporal_runtime_imports_without_test_shim() -> None:
    service_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", "import backend.infrastructure.temporal.runtime"],
        cwd=str(service_root),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_aix_v2_workflow_module_does_not_import_service_stack_at_module_load() -> None:
    service_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys; "
                "import backend.legacy.aix_orchestrator_v2.temporal_workflows; "
                "print(json.dumps({"
                "'service_loaded': 'backend.aix_orchestrator_v2.service' in sys.modules, "
                "'clients_loaded': 'backend.clients' in sys.modules"
                "}))"
            ),
        ],
        cwd=str(service_root),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '{"service_loaded": false, "clients_loaded": false}'


@pytest.mark.asyncio
async def test_aix_v2_workflow_passes_temporal_sandbox_validation() -> None:
    runner = SandboxedWorkflowRunner()
    definition = _Definition.must_from_class(temporal_workflows.AixOrchestratorV2Workflow)
    runner.prepare_workflow(definition)


def test_select_task_queue_by_kind_and_priority() -> None:
    separate = _Settings()
    separate.TEMPORAL_SEPARATE_QUEUES_ENABLED = True
    binding = build_queue_binding(separate)

    assert select_task_queue(workflow_kind="plan", binding=binding, priority="P2") == "aix.plan.current"
    assert (
        select_task_queue(workflow_kind="execution", binding=binding, priority="P1") == "aix.execution.current.priority"
    )
    assert (
        select_task_queue(workflow_kind="verification", binding=binding, priority="P0")
        == "aix.verification.current.priority"
    )


def test_queues_collapsed_onto_execution_by_default() -> None:
    # Wave 2: one live queue. Plan/verification stay parked unless separate
    # queues are explicitly enabled (with a worker per queue).
    binding = build_queue_binding(_Settings())

    assert binding.plan_queue == "aix.execution.current"
    assert binding.execution_queue == "aix.execution.current"
    assert binding.verification_queue == "aix.execution.current"


def test_aix_v2_workflow_activity_timeout_uses_timedelta() -> None:
    assert _activity_timeout(300) == timedelta(seconds=300)


def test_aix_v2_blocked_execution_payload_matches_run_result_schema() -> None:
    state = temporal_workflows.AixOrchestratorV2RunState(
        task_id="task-1",
        run_id="aixv2-test",
        packet={"task_ref": {"project_id": "bh-migrated-7-aix", "task_number": 2627}},
        plan=temporal_workflows.PlannerResult(status="blocked", blocked_reason="planner blocked"),
    )

    payload = _blocked_execution_payload(state).model_dump(mode="python")

    result = RunResult.model_validate(payload["run_result"])
    assert result.error is not None
    assert result.error.code == "PLANNER_BLOCKED"
    assert result.error.details == {"classification": "POLICY_FAIL", "semantic_state": "needs_shaping"}


def test_temporal_runtime_metadata_disabled() -> None:
    manager = TemporalRuntimeManager(settings=_Settings(), sdk_loader=lambda: None)
    metadata = manager.bootstrap_metadata()
    assert metadata["enabled"] is False
    assert metadata["temporal_sdk_available"] is False
    assert metadata["namespace"] == "hyeve-aix"


@pytest.mark.asyncio
async def test_temporal_runtime_startup_degraded_when_sdk_missing() -> None:
    settings = _Settings(TEMPORAL_ENABLED=True, TEMPORAL_AUTO_START_WORKER=False)
    manager = TemporalRuntimeManager(settings=settings, sdk_loader=lambda: None)
    status = await manager.startup()
    assert status["mode"] == "degraded"
    assert status["reason"] == "TEMPORAL_SDK_NOT_INSTALLED"


def _canonical_raw() -> dict[str, object]:
    return {
        "contract_version": "aix.objective-cycle.temporal-contract.v1",
        "workflow_version": 1,
        "tenant_id": "tenant-test",
        "objective_id": "objective-1",
        "cycle_id": "cycle-1",
        "candidate_sha": "abcdef1",
        "plan_sha256": "0" * 64,
        "start_request_id": "req-1",
        "correlation_id": "corr-1",
    }


@pytest.mark.asyncio
async def test_temporal_runtime_canonical_start() -> None:
    started: dict[str, object] = {}

    class _Handle:
        run_id = "run-123"

    class _Client:
        @classmethod
        async def connect(cls, address: str, namespace: str):
            started["address"] = address
            started["namespace"] = namespace
            return cls()

        async def start_workflow(
            self, workflow_name, payload, *, id, task_queue,
            id_reuse_policy, id_conflict_policy,
        ):
            started["workflow_name"] = workflow_name
            started["payload"] = payload
            started["id"] = id
            started["task_queue"] = task_queue
            started["id_reuse_policy"] = id_reuse_policy
            started["id_conflict_policy"] = id_conflict_policy
            return _Handle()

    class _Worker:
        def __init__(self, *_args, **_kwargs):
            pass

    sdk = TemporalSdk(client_cls=_Client, worker_cls=_Worker)
    settings = _Settings(TEMPORAL_ENABLED=True, TEMPORAL_AUTO_START_WORKER=False)
    manager = TemporalRuntimeManager(settings=settings, sdk_loader=lambda: sdk)

    result = await manager.canonical_start(raw=_canonical_raw())

    assert result["workflow"] == "AixObjectiveCycleWorkflow"
    assert started["workflow_name"] == "AixObjectiveCycleWorkflow"
    assert started["id_reuse_policy"] is WorkflowIDReusePolicy.REJECT_DUPLICATE
    assert started["id_conflict_policy"] is WorkflowIDConflictPolicy.FAIL
    assert started["task_queue"] == "aix.execution.current"
    assert result["task_queue"] == "aix.execution.current"
    assert result["namespace"] == "hyeve-aix"
    assert str(result["workflow_id"]).startswith("aix-cycle-tenant-test-cycle-1")


@pytest.mark.asyncio
async def test_temporal_runtime_canonical_start_rejects_invalid_payload() -> None:
    sdk = TemporalSdk(client_cls=object, worker_cls=object)
    settings = _Settings(TEMPORAL_ENABLED=True, TEMPORAL_AUTO_START_WORKER=False)
    manager = TemporalRuntimeManager(settings=settings, sdk_loader=lambda: sdk)

    with pytest.raises(Exception):
        await manager.canonical_start(raw={"tenant_id": "tenant-test"})

    assert not hasattr(manager, "start_v2_workflow")


@pytest.mark.asyncio
async def test_temporal_runtime_emits_worker_and_workflow_events() -> None:
    captured_logs: list[tuple[str, dict[str, object]]] = []

    class _Handle:
        run_id = "run-123"

    class _Client:
        @classmethod
        async def connect(cls, address: str, namespace: str):
            return cls()

        async def start_workflow(
            self, workflow_name, payload, *, id, task_queue,
            id_reuse_policy, id_conflict_policy,
        ):
            assert id_reuse_policy is WorkflowIDReusePolicy.REJECT_DUPLICATE
            assert id_conflict_policy is WorkflowIDConflictPolicy.FAIL
            return _Handle()

    class _Worker:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self) -> None:
            return None

    class _Logger:
        def info(self, event: str, **kwargs) -> None:
            captured_logs.append((event, kwargs))

        def warning(self, event: str, **kwargs) -> None:
            captured_logs.append((event, kwargs))

        def error(self, event: str, **kwargs) -> None:
            captured_logs.append((event, kwargs))

    sdk = TemporalSdk(client_cls=_Client, worker_cls=_Worker)
    settings = _Settings(TEMPORAL_ENABLED=True, TEMPORAL_AUTO_START_WORKER=False)
    manager = TemporalRuntimeManager(settings=settings, sdk_loader=lambda: sdk)
    manager._logger = _Logger()

    await manager.startup()
    await manager.canonical_start(raw=_canonical_raw())

    assert any(event == "temporal.worker_bootstrap_start" for event, _ in captured_logs)
    assert any(
        event == "temporal.worker_queue_binding" and payload["execution_queue"] == "aix.execution.current"
        for event, payload in captured_logs
    )
    assert any(event == "temporal.workflow_start_requested" for event, _ in captured_logs)
    assert any(event == "temporal.workflow_started" for event, _ in captured_logs)


@pytest.mark.asyncio
async def test_temporal_runtime_worker_registers_only_objective_cycle_workflow() -> None:
    captured: dict[str, object] = {}
    ready = asyncio.Event()

    class _Client:
        @classmethod
        async def connect(cls, address: str, namespace: str):
            captured["address"] = address
            captured["namespace"] = namespace
            return cls()

    class _Worker:
        def __init__(self, *_args, **kwargs):
            captured["task_queue"] = kwargs["task_queue"]
            captured["workflows"] = kwargs["workflows"]
            captured["activities"] = kwargs["activities"]
            captured["identity"] = kwargs["identity"]

        async def run(self) -> None:
            captured["ran"] = True
            ready.set()
            await asyncio.sleep(3600)

    sdk = TemporalSdk(client_cls=_Client, worker_cls=_Worker)
    settings = _Settings(TEMPORAL_ENABLED=True, TEMPORAL_AUTO_START_WORKER=True)
    manager = TemporalRuntimeManager(settings=settings, sdk_loader=lambda: sdk)

    task = asyncio.create_task(manager.run_worker_forever())
    await asyncio.wait_for(ready.wait(), timeout=5)

    workflows = cast(list[type[Any]], captured["workflows"])
    activities = cast(list[Any], captured["activities"])
    assert captured["task_queue"] == "aix.execution.current"
    assert captured["identity"] == "hyeve-aix-worker"
    assert captured["ran"] is True
    assert [workflow.__name__ for workflow in workflows] == [
        "AixObjectiveCycleWorkflow",
    ]
    assert len(activities) == 7
    assert all(hasattr(activity, "__temporal_activity_definition") for activity in activities)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_temporal_runtime_worker_retries_after_connect_failure() -> None:
    attempts = {"count": 0}
    ready = asyncio.Event()
    captured_logs: list[tuple[str, dict[str, object]]] = []

    class _Client:
        @classmethod
        async def connect(cls, address: str, namespace: str):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("temporal not ready")
            return cls()

    class _Worker:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self) -> None:
            ready.set()
            await asyncio.sleep(3600)

    class _Logger:
        def info(self, event: str, **kwargs) -> None:
            captured_logs.append((event, kwargs))

        def warning(self, event: str, **kwargs) -> None:
            captured_logs.append((event, kwargs))

        def error(self, event: str, **kwargs) -> None:
            captured_logs.append((event, kwargs))

    sdk = TemporalSdk(client_cls=_Client, worker_cls=_Worker)
    settings = _Settings(TEMPORAL_ENABLED=True, TEMPORAL_AUTO_START_WORKER=True)
    manager = TemporalRuntimeManager(settings=settings, sdk_loader=lambda: sdk)
    manager._logger = _Logger()

    task = asyncio.create_task(manager.run_worker_forever())
    await asyncio.wait_for(ready.wait(), timeout=5)

    status = manager.runtime_status()
    assert attempts["count"] >= 2
    assert status["worker"]["state"] == "ready"
    assert status["worker"]["attempt"] >= 2
    assert status["worker"]["last_error"] is None
    ready_at = datetime.fromisoformat(str(status["worker"]["last_ready_at"]))
    assert ready_at.year >= datetime.now(UTC).year
    assert any(
        event == "temporal.worker_error" and payload["reason_code"] == "RuntimeError"
        for event, payload in captured_logs
    )
    assert any(
        event == "temporal.worker_workflow_registration"
        and payload["workflows"] == [
            "AixObjectiveCycleWorkflow",
        ]
        for event, payload in captured_logs
    )
    assert any(
        event == "temporal.worker_activity_registration" and len(cast(list[str], payload["activities"])) == 7
        for event, payload in captured_logs
    )

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_temporal_runtime_status_exposes_failed_worker_task() -> None:
    settings = _Settings(TEMPORAL_ENABLED=True, TEMPORAL_AUTO_START_WORKER=True)
    manager = TemporalRuntimeManager(settings=settings, sdk_loader=lambda: None)
    captured_logs: list[tuple[str, dict[str, object]]] = []

    class _Logger:
        def info(self, event: str, **kwargs) -> None:
            captured_logs.append((event, kwargs))

        def warning(self, event: str, **kwargs) -> None:
            captured_logs.append((event, kwargs))

        def error(self, event: str, **kwargs) -> None:
            captured_logs.append((event, kwargs))

    manager._logger = _Logger()

    async def _boom() -> None:
        raise RuntimeError("worker crashed")

    task = asyncio.create_task(_boom())
    await asyncio.sleep(0)
    manager._worker_task = task
    manager._handle_worker_task_completion(task)

    status = manager.runtime_status()
    assert status["worker"]["state"] == "failed"
    assert status["worker"]["task_state"] == "done"
    assert status["worker"]["task_exception_type"] == "RuntimeError"
    assert status["worker"]["task_exception"] == "worker crashed"
    assert any(
        event == "temporal.worker_failed" and payload["reason_code"] == "RuntimeError"
        for event, payload in captured_logs
    )
    assert any(
        event == "temporal.worker_task_failed" and payload["reason_code"] == "RuntimeError"
        for event, payload in captured_logs
    )


@pytest.mark.asyncio
async def test_temporal_runtime_reports_worker_stop_reason_on_cancellation() -> None:
    settings = _Settings(TEMPORAL_ENABLED=True, TEMPORAL_AUTO_START_WORKER=True)
    manager = TemporalRuntimeManager(settings=settings, sdk_loader=lambda: None)
    captured_logs: list[tuple[str, dict[str, object]]] = []

    class _Logger:
        def info(self, event: str, **kwargs) -> None:
            captured_logs.append((event, kwargs))

        def warning(self, event: str, **kwargs) -> None:
            captured_logs.append((event, kwargs))

        def error(self, event: str, **kwargs) -> None:
            captured_logs.append((event, kwargs))

    manager._logger = _Logger()

    async def _wait_forever() -> None:
        await asyncio.sleep(3600)

    task = asyncio.create_task(_wait_forever())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    manager._worker_task = task
    manager._handle_worker_task_completion(task)

    assert any(
        event == "temporal.worker_stopped" and payload["reason_code"] == "CANCELLED" for event, payload in captured_logs
    )
    assert any(
        event == "temporal.worker_task_stopped" and payload["reason_code"] == "CANCELLED"
        for event, payload in captured_logs
    )


@pytest.mark.asyncio
async def test_temporal_activities_are_idempotent_by_key() -> None:
    call_count = {"dispatch": 0}

    async def _dispatch(payload: dict) -> dict:
        call_count["dispatch"] += 1
        return {"ok": True, "status": "completed", "echo": payload}

    activities = AixTemporalActivities(dispatch_plan_fn=_dispatch)
    payload = {"idempotency_key": "idem-1", "task_number": 1139}
    first = await activities.dispatch_plan(payload)
    second = await activities.dispatch_plan(payload)

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["idempotent_replay"] is True
    assert call_count["dispatch"] == 1


@pytest.mark.asyncio
async def test_aix_v2_planner_activity_persists_blocked_state_for_invalid_recon(monkeypatch) -> None:
    persisted: list[dict[str, object]] = []

    class _Service:
        def _persist(self, state) -> None:
            persisted.append(state.model_dump(mode="python"))

    monkeypatch.setattr(temporal_workflows, "_service", lambda: _Service())
    activity_runner = temporal_workflows.AixOrchestratorV2Activities()

    result = await activity_runner.planner_activity(
        {
            "run_id": "aixv2-test",
            "task_id": "task-1",
            "task_payload": {"task_number": 2627, "title": "Sprint 2 temporal lane"},
            "packet": {
                "task_ref": {"project_id": "bh-migrated-7-aix", "task_number": 2627},
                "workspace_ref": {"workspace_root": "/workspace/hyeve"},
            },
            "recon": {
                "target_file": None,
                "target_symbol": None,
                "validation_target": "pytest tests/test_example.py",
                "execution_contract": {},
                "dependency_context": {},
                "related_tests": [],
                "entity_query": "task",
                "codeintel": {},
                "anchor": {},
                "symbol_ranges": [],
                "anchor_ranges": [],
                "target_files": [],
                "slim_packet_inputs": {},
            },
        }
    )

    assert result["status"] == "blocked"
    assert result["current_phase"] == "planner"
    assert result["failure"]["classification"] == "POLICY_FAIL"
    assert result["failure"]["code"] == "RECON_TARGET_FILE_REQUIRED"
    persisted_failure = cast(dict[str, object], persisted[-1]["failure"])
    assert persisted_failure["code"] == "RECON_TARGET_FILE_REQUIRED"


@pytest.mark.asyncio
async def test_aix_v2_recon_activity_persists_recon_artifact_and_blocks_when_insufficient(monkeypatch) -> None:
    persisted: list[dict[str, object]] = []

    class _WorkHub:
        async def list_task_execution_contracts(self, *, project_id: str, task_number: int):
            return [{}]

    class _Service:
        def __init__(self) -> None:
            self._workhub = _WorkHub()

        def _persist(self, state) -> None:
            persisted.append(state.model_dump(mode="python"))

    async def _fake_build_recon(**_kwargs):
        return {
            "status": "insufficient",
            "reason": "target_file_missing",
            "entity_query": "task",
            "execution_contract": {},
            "context_error": None,
            "codeintel": {},
            "target_file": None,
            "target_symbol": None,
            "target_files": [],
            "anchor": {},
            "symbol_ranges": [],
            "anchor_ranges": [],
            "validation_target": "pytest tests/test_example.py",
            "related_tests": [],
            "dependency_context": {},
            "slim_packet_inputs": {"task_number": 2627},
        }

    monkeypatch.setattr(temporal_workflows, "_service", lambda: _Service())
    monkeypatch.setattr("backend.legacy.aix_orchestrator_v2.recon_service.build_recon", _fake_build_recon)
    activity_runner = temporal_workflows.AixOrchestratorV2Activities()

    result = await activity_runner.recon_activity(
        {
            "run_id": "aixv2-test",
            "task_id": "task-1",
            "task_payload": {"task_number": 2627, "title": "Sprint 2 temporal lane"},
            "packet": {
                "task_ref": {"project_id": "bh-migrated-7-aix", "task_number": 2627},
                "workspace_ref": {"workspace_root": "/workspace/hyeve"},
            },
        }
    )

    assert result["status"] == "blocked"
    assert result["failure"]["classification"] == "POLICY_FAIL"
    assert result["timestamps"]["recon_at"] is not None
    assert any(item["name"] == "recon" for item in cast(list[dict[str, object]], result["artifacts"]))


@pytest.mark.asyncio
async def test_aix_v2_load_task_activity_preserves_bootstrap_run_id(monkeypatch) -> None:
    persisted: list[dict[str, object]] = []

    class _Service:
        async def load_task(
            self,
            *,
            project_id: str,
            task_number: int,
            workspace_root: str,
            run_id: str | None = None,
            tenant_id: str | None = None,
        ):
            from backend.aix_orchestrator_v2.models import AixOrchestratorV2RunState

            state = AixOrchestratorV2RunState(run_id=run_id or "generated-run-id")
            state.task_id = "task-1"
            state.task_payload = {"id": "task-1", "task_number": task_number, "project_id": project_id}
            state.packet = {
                "task_ref": {"project_id": project_id, "task_id": "task-1", "task_number": task_number},
                "workspace_ref": {"workspace_root": workspace_root},
                "control": {"run_id": state.run_id},
            }
            state.status = "loaded"
            state.current_phase = "load_task"
            return state

        def _persist(self, state) -> None:
            persisted.append(state.model_dump(mode="python"))

    monkeypatch.setattr(temporal_workflows, "_service", lambda: _Service())
    activity_runner = temporal_workflows.AixOrchestratorV2Activities()

    result = await activity_runner.load_task_activity(
        {
            "project_id": "bh-migrated-7-aix",
            "task_number": 2627,
            "workspace_root": "/workspace/hyeve",
            "run_id": "aixv2-bootstrap-run",
        }
    )

    assert result["run_id"] == "aixv2-bootstrap-run"
    assert result["packet"]["control"]["run_id"] == "aixv2-bootstrap-run"
    assert persisted[-1]["run_id"] == "aixv2-bootstrap-run"


@pytest.mark.asyncio
async def test_aix_v2_writeback_activity_marks_successful_temporal_run_complete(monkeypatch) -> None:
    persisted: list[dict[str, object]] = []

    class _Service:
        def __init__(self) -> None:
            self._workhub = object()

        def _persist(self, state) -> None:
            persisted.append(state.model_dump(mode="python"))

    async def _fake_writeback_v2_run(**_kwargs):
        return {
            "writeback": {"status": "ok"},
            "verification_evaluation": {"decision": "pass"},
            "verification_error": None,
            "closeout_operation": {"status": "completed"},
            "closeout_error": None,
            "transition_ok": True,
            "transition_error": None,
            "evidence_ok": True,
            "evidence_error": None,
        }

    monkeypatch.setattr(temporal_workflows, "_service", lambda: _Service())
    monkeypatch.setattr("backend.legacy.aix_orchestrator_v2.writeback_service.writeback_v2_run", _fake_writeback_v2_run)
    activity_runner = temporal_workflows.AixOrchestratorV2Activities()

    result = await activity_runner.writeback_activity(
        {
            "run_id": "aixv2-test",
            "task_id": "task-1",
            "task_payload": {"task_number": 2672, "title": "happy proof"},
            "packet": {
                "task_ref": {"project_id": "bh-migrated-7-aix", "task_number": 2672},
                "runtime": {"tenant_id": "default", "actor_id": "tester"},
            },
            "status": "success",
            "execution": {
                "status": "completed",
                "run_result": {"status": "completed"},
                "changed_files": ["backend/services/example.py"],
                "commands": [{"command_text": "pytest tests/test_example.py -q"}],
                "tests": {"detected": True},
                "diff_refs": ["local://diff"],
                "failure": None,
                "trace": {},
                "envelope": {},
            },
            "verification": {
                "classification": "success",
                "summary": "declared validation completed successfully",
                "checks": [{"name": "declared_validation", "passed": True}],
                "reasons": [],
                "verifier_decision": {"decision": "verified"},
            },
        }
    )

    assert result["status"] == "writeback_complete"
    assert result["timestamps"]["writeback_at"] is not None
    assert result["timestamps"]["completed_at"] is not None
    assert any(item["name"] == "writeback" for item in cast(list[dict[str, object]], result["artifacts"]))
    assert persisted[-1]["status"] == "writeback_complete"


@pytest.mark.asyncio
async def test_aix_v2_execution_activity_tolerates_null_failure_on_success(monkeypatch) -> None:
    persisted: list[dict[str, object]] = []
    logged: list[tuple[str, dict[str, object]]] = []

    class _Service:
        def _persist(self, state) -> None:
            persisted.append(state.model_dump(mode="python"))

    class _Logger:
        def info(self, event: str, **kwargs) -> None:
            logged.append((event, kwargs))

    def _fake_execute_plan(**_kwargs):
        return {
            "status": "completed",
            "run_result": {"status": "completed"},
            "changed_files": ["docs/ops/WH_BRIDGE_GUARD_ALLOWLIST.txt"],
            "commands": [{"command_text": "python -c pass", "returncode": 0, "ok": True}],
            "tests": {},
            "diff_refs": ["local://diff"],
            "failure": None,
            "trace": {},
            "envelope": {},
        }

    monkeypatch.setattr(temporal_workflows, "_service", lambda: _Service())
    monkeypatch.setattr(temporal_workflows, "_activity_logger", lambda: _Logger())
    monkeypatch.setattr("backend.legacy.aix_orchestrator_v2.execution_service.execute_plan", _fake_execute_plan)
    activity_runner = temporal_workflows.AixOrchestratorV2Activities()

    result = await activity_runner.execution_activity(
        {
            "run_id": "aixv2-test",
            "task_id": "task-1",
            "task_payload": {"task_number": 2727, "title": "happy path"},
            "packet": {
                "task_ref": {"project_id": "bh-migrated-7-aix", "task_number": 2727},
                "workspace_ref": {"workspace_root": "/workspace/hyeve"},
                "runtime": {"repo_key": "hyeve", "explicit_profile_id": "RP-HYEVE-PY-FASTAPI-CURRENT"},
            },
            "recon": {
                "status": "ready",
                "entity_query": "docs/ops/WH_BRIDGE_GUARD_ALLOWLIST.txt",
                "target_file": "docs/ops/WH_BRIDGE_GUARD_ALLOWLIST.txt",
                "validation_target": "python -c pass",
            },
            "plan": {"status": "planned", "steps": [{"tool": "cmd.run"}]},
        }
    )

    assert result["status"] == "executed"
    assert result["execution"]["failure"] is None
    assert persisted[-1]["status"] == "executed"
    end_events = [kwargs for event, kwargs in logged if event == "activity_end"]
    assert end_events
    assert end_events[-1]["classification"] == "success"
    assert end_events[-1]["reason_code"] == ""


@pytest.mark.asyncio
async def test_aix_v2_execution_activity_emits_terminal_failure_event(monkeypatch) -> None:
    logged: list[tuple[str, dict[str, object]]] = []

    class _Service:
        def _persist(self, state) -> None:
            return None

    class _Logger:
        def info(self, event: str, **kwargs) -> None:
            logged.append((event, kwargs))

    def _fake_execute_plan(**_kwargs):
        return {
            "status": "failed",
            "run_result": {"status": "failed"},
            "changed_files": [],
            "commands": [{"command_text": "python -c boom", "returncode": 1, "ok": False}],
            "tests": {},
            "diff_refs": [],
            "failure": {"code": "EXECUTOR_TERMINAL_STEP_FAILED", "message": "tool step failed: cmd.run"},
            "trace": {},
            "envelope": {},
        }

    monkeypatch.setattr(temporal_workflows, "_service", lambda: _Service())
    monkeypatch.setattr(temporal_workflows, "_activity_logger", lambda: _Logger())
    monkeypatch.setattr("backend.legacy.aix_orchestrator_v2.execution_service.execute_plan", _fake_execute_plan)
    activity_runner = temporal_workflows.AixOrchestratorV2Activities()

    await activity_runner.execution_activity(
        {
            "run_id": "aixv2-test",
            "task_id": "task-1",
            "task_payload": {"task_number": 2728, "title": "non-happy path"},
            "packet": {
                "task_ref": {"project_id": "bh-migrated-7-aix", "task_number": 2728},
                "workspace_ref": {"workspace_root": "/workspace/hyeve"},
                "runtime": {"repo_key": "hyeve", "explicit_profile_id": "RP-HYEVE-PY-FASTAPI-CURRENT"},
            },
            "recon": {
                "status": "ready",
                "entity_query": "backend/example.py",
                "target_file": "backend/example.py",
                "validation_target": "python -c boom",
            },
            "plan": {"status": "planned", "steps": [{"tool": "cmd.run"}]},
        }
    )

    terminal_events = [kwargs for event, kwargs in logged if event == "terminal_failure"]
    assert terminal_events
    assert terminal_events[-1]["phase"] == "execution"
    assert terminal_events[-1]["reason_code"] == "EXECUTOR_TERMINAL_STEP_FAILED"


@pytest.mark.asyncio
async def test_aix_v2_execution_activity_contract_drift_emits_terminal_failure_event(monkeypatch) -> None:
    persisted: list[dict[str, object]] = []
    logged: list[tuple[str, dict[str, object]]] = []

    class _Service:
        def _persist(self, state) -> None:
            persisted.append(state.model_dump(mode="python"))

    class _Logger:
        def info(self, event: str, **kwargs) -> None:
            logged.append((event, kwargs))

    def _fake_execute_plan(**_kwargs):
        return {
            "status": "failed",
            "run_result": {},
            "changed_files": [],
            "commands": [],
            "tests": {},
            "diff_refs": [],
            "failure": {
                "code": "SLIM_PACKET_STATE_DRIFT",
                "message": "execution contract drift detected before runtime dispatch",
                "retryable": False,
                "details": {"expected": {"task_number": 2753}, "actual": {"task_number": 9999}},
            },
            "trace": {"contract_consistency": {"reason_code": "SLIM_PACKET_STATE_DRIFT"}},
            "envelope": {},
        }

    monkeypatch.setattr(temporal_workflows, "_service", lambda: _Service())
    monkeypatch.setattr(temporal_workflows, "_activity_logger", lambda: _Logger())
    monkeypatch.setattr("backend.legacy.aix_orchestrator_v2.execution_service.execute_plan", _fake_execute_plan)
    activity_runner = temporal_workflows.AixOrchestratorV2Activities()

    result = await activity_runner.execution_activity(
        {
            "run_id": "aixv2-contract-drift",
            "task_id": "task-1",
            "task_payload": {"task_number": 2753, "title": "contract drift"},
            "packet": {
                "task_ref": {"project_id": "bh-migrated-7-aix", "task_number": 2753},
                "workspace_ref": {"workspace_root": "/workspace/hyeve"},
                "slim_packet": {"task_number": 9999},
            },
            "recon": {
                "status": "ready",
                "entity_query": "backend/services/example.py",
                "execution_contract": {},
                "context_error": None,
                "codeintel": {},
                "target_file": "backend/services/example.py",
                "target_symbol": "example.symbol",
                "target_files": ["backend/services/example.py"],
                "anchor": {},
                "symbol_ranges": [],
                "anchor_ranges": [],
                "validation_target": "pytest tests/test_example.py -q",
                "related_tests": [],
                "dependency_context": {},
                "slim_packet_inputs": {"task_number": 2753},
            },
            "plan": {"status": "planned", "steps": [{"tool": "cmd.run"}]},
        }
    )

    assert result["execution"]["failure"]["code"] == "SLIM_PACKET_STATE_DRIFT"
    assert any(
        event == "terminal_failure" and payload.get("reason_code") == "SLIM_PACKET_STATE_DRIFT"
        for event, payload in logged
    )
    persisted_execution = cast(dict[str, Any], persisted[-1]["execution"])
    assert cast(dict[str, Any], persisted_execution["failure"])["code"] == "SLIM_PACKET_STATE_DRIFT"


@pytest.mark.asyncio
async def test_aix_v2_execution_activity_persists_remediation_control_on_failure(monkeypatch) -> None:
    persisted: list[dict[str, object]] = []

    class _Service:
        def _persist(self, state) -> None:
            persisted.append(state.model_dump(mode="python"))

        def _apply_remediation_control(self, state) -> None:
            state.add_artifact(
                name="remediation_control",
                payload={
                    "effective_action": "attach_existing",
                    "effective_reason_code": "EQUIVALENT_OPEN_BLOCKER_EXISTS",
                },
            )

    def _fake_execute_plan(**_kwargs):
        return {
            "status": "failed",
            "run_result": {},
            "changed_files": [],
            "commands": [],
            "tests": {},
            "diff_refs": [],
            "failure": {
                "code": "HOST_CHECKOUT_DIRTY_FOR_EXECUTION",
                "message": "workspace checkout leaked changes",
            },
            "trace": {},
            "envelope": {},
        }

    monkeypatch.setattr(temporal_workflows, "_service", lambda: _Service())
    monkeypatch.setattr("backend.legacy.aix_orchestrator_v2.execution_service.execute_plan", _fake_execute_plan)
    activity_runner = temporal_workflows.AixOrchestratorV2Activities()

    await activity_runner.execution_activity(
        {
            "run_id": "aixv2-remediation",
            "task_id": "task-1",
            "task_payload": {"task_number": 2754, "title": "remediation activation"},
            "packet": {
                "task_ref": {"project_id": "bh-migrated-7-aix", "task_number": 2754},
                "workspace_ref": {"workspace_root": "/workspace/hyeve"},
                "runtime": {"repo_key": "hyeve", "explicit_profile_id": "RP-HYEVE-PY-FASTAPI-CURRENT"},
            },
            "recon": {
                "status": "ready",
                "entity_query": "backend/example.py",
                "target_file": "backend/example.py",
                "validation_target": "python -c boom",
            },
            "plan": {"status": "planned", "steps": [{"tool": "cmd.run"}]},
            "validator_ledger": {"last_validated_sha": {}, "events": []},
        }
    )

    artifacts = cast(list[dict[str, Any]], persisted[-1]["artifacts"])
    artifact_names = [str(item.get("name") or "") for item in artifacts]
    assert "remediation_control" in artifact_names


@pytest.mark.asyncio
async def test_aix_v2_execution_activity_prefers_async_remediation_control(monkeypatch) -> None:
    persisted: list[dict[str, object]] = []

    class _Service:
        def _persist(self, state) -> None:
            persisted.append(state.model_dump(mode="python"))

        async def _apply_remediation_control_async(self, state) -> None:
            state.add_artifact(
                name="remediation_control",
                payload={
                    "effective_action": "attach_existing",
                    "effective_reason_code": "EQUIVALENT_OPEN_BLOCKER_EXISTS",
                    "path": "async",
                },
            )

        def _apply_remediation_control(self, state) -> None:
            raise AssertionError("sync remediation path should not run when async helper exists")

    def _fake_execute_plan(**_kwargs):
        return {
            "status": "failed",
            "run_result": {},
            "changed_files": [],
            "commands": [],
            "tests": {},
            "diff_refs": [],
            "failure": {
                "code": "HOST_CHECKOUT_DIRTY_FOR_EXECUTION",
                "message": "workspace checkout leaked changes",
            },
            "trace": {},
            "envelope": {},
        }

    monkeypatch.setattr(temporal_workflows, "_service", lambda: _Service())
    monkeypatch.setattr("backend.legacy.aix_orchestrator_v2.execution_service.execute_plan", _fake_execute_plan)
    activity_runner = temporal_workflows.AixOrchestratorV2Activities()

    await activity_runner.execution_activity(
        {
            "run_id": "aixv2-remediation-async",
            "task_id": "task-1",
            "task_payload": {"task_number": 2755, "title": "remediation async"},
            "packet": {
                "task_ref": {"project_id": "bh-migrated-7-aix", "task_number": 2755},
                "workspace_ref": {"workspace_root": "/workspace/hyeve"},
                "runtime": {"repo_key": "hyeve", "explicit_profile_id": "RP-HYEVE-PY-FASTAPI-CURRENT"},
            },
            "recon": {
                "status": "ready",
                "entity_query": "backend/example.py",
                "target_file": "backend/example.py",
                "validation_target": "python -c boom",
            },
            "plan": {"status": "planned", "steps": [{"tool": "cmd.run"}]},
            "validator_ledger": {"last_validated_sha": {}, "events": []},
        }
    )

    artifacts = cast(list[dict[str, Any]], persisted[-1]["artifacts"])
    remediation_artifacts = [item for item in artifacts if item.get("name") == "remediation_control"]
    assert remediation_artifacts
    assert cast(dict[str, Any], remediation_artifacts[-1]["payload"])["path"] == "async"


@pytest.mark.asyncio
async def test_aix_v2_verifier_activity_emits_validation_result(monkeypatch) -> None:
    logged: list[tuple[str, dict[str, object]]] = []

    class _Service:
        def _persist(self, state) -> None:
            return None

    class _Logger:
        def info(self, event: str, **kwargs) -> None:
            logged.append((event, kwargs))

    monkeypatch.setattr(temporal_workflows, "_service", lambda: _Service())
    monkeypatch.setattr(temporal_workflows, "_activity_logger", lambda: _Logger())
    activity_runner = temporal_workflows.AixOrchestratorV2Activities()

    await activity_runner.verifier_activity(
        {
            "run_id": "aixv2-test",
            "task_id": "task-1",
            "plan": {
                "status": "planned",
                "declared_validation": "python -m py_compile backend/example.py",
                "planner_packet": {"acceptance_criteria": ["validation passes"]},
            },
            "execution": {
                "status": "completed",
                "run_result": {"status": "completed"},
                "changed_files": ["backend/example.py"],
                "diff_refs": ["local://diff"],
                "commands": [
                    {
                        "ok": True,
                        "command_text": "python -m py_compile backend/example.py",
                        "returncode": 0,
                        "exit_code": 0,
                        "parsed_result": "passed",
                        "classification_mapping": {
                            "classification": "success",
                            "reason_code": "VALIDATION_PASSED",
                        },
                    }
                ],
            },
        }
    )

    validation_events = [kwargs for event, kwargs in logged if event == "validation_result"]
    assert validation_events
    assert validation_events[-1]["classification"] == "success"
    assert validation_events[-1]["reason_code"] == "VALIDATION_PASSED"


@pytest.mark.asyncio
async def test_aix_v2_writeback_activity_marks_failed_temporal_run_when_writeback_fails(monkeypatch) -> None:
    persisted: list[dict[str, object]] = []

    class _Service:
        def __init__(self) -> None:
            self._workhub = object()

        def _persist(self, state) -> None:
            persisted.append(state.model_dump(mode="python"))

    async def _fake_writeback_v2_run(**_kwargs):
        return {
            "writeback": {"status": "error"},
            "verification_evaluation": None,
            "verification_error": None,
            "closeout_operation": None,
            "closeout_error": None,
            "transition_ok": False,
            "transition_error": "transition failed",
            "evidence_ok": True,
            "evidence_error": None,
        }

    monkeypatch.setattr(temporal_workflows, "_service", lambda: _Service())
    monkeypatch.setattr("backend.legacy.aix_orchestrator_v2.writeback_service.writeback_v2_run", _fake_writeback_v2_run)
    activity_runner = temporal_workflows.AixOrchestratorV2Activities()

    result = await activity_runner.writeback_activity(
        {
            "run_id": "aixv2-test",
            "task_id": "task-1",
            "task_payload": {"task_number": 2673, "title": "forced non-happy proof"},
            "packet": {
                "task_ref": {"project_id": "bh-migrated-7-aix", "task_number": 2673},
                "runtime": {"tenant_id": "default", "actor_id": "tester"},
            },
            "status": "blocked",
            "execution": {
                "status": "skipped",
                "run_result": {"status": "failed"},
                "changed_files": [],
                "commands": [],
                "tests": {},
                "diff_refs": [],
                "failure": None,
                "trace": {},
                "envelope": {},
            },
            "verification": {
                "classification": "blocked",
                "summary": "planner blocked",
                "checks": [{"name": "declared_validation", "passed": False}],
                "reasons": [{"message": "planner blocked"}],
                "verifier_decision": {"decision": "needs_fix"},
            },
        }
    )

    assert result["status"] == "failed"
    assert result["failure"]["classification"] == "CODE_FAIL"
    assert result["timestamps"]["writeback_at"] is not None
    persisted_failure = cast(dict[str, object], cast(dict[str, object], persisted[-1])["failure"])
    assert persisted_failure["code"] == "WRITEBACK_FAILED"


@pytest.mark.asyncio
async def test_aix_v2_writeback_activity_emits_writeback_result_and_terminal_failure(monkeypatch) -> None:
    logged: list[tuple[str, dict[str, object]]] = []

    class _Service:
        def __init__(self) -> None:
            self._workhub = object()

        def _persist(self, state) -> None:
            return None

    class _Logger:
        def info(self, event: str, **kwargs) -> None:
            logged.append((event, kwargs))

    async def _fake_writeback_v2_run(**_kwargs):
        return {
            "writeback": {"status": "error"},
            "verification_evaluation": None,
            "verification_error": None,
            "closeout_operation": None,
            "closeout_error": None,
            "transition_ok": False,
            "transition_error": "transition failed",
            "evidence_ok": True,
            "evidence_error": None,
        }

    monkeypatch.setattr(temporal_workflows, "_service", lambda: _Service())
    monkeypatch.setattr(temporal_workflows, "_activity_logger", lambda: _Logger())
    monkeypatch.setattr("backend.legacy.aix_orchestrator_v2.writeback_service.writeback_v2_run", _fake_writeback_v2_run)
    activity_runner = temporal_workflows.AixOrchestratorV2Activities()

    await activity_runner.writeback_activity(
        {
            "run_id": "aixv2-test",
            "task_id": "task-1",
            "task_payload": {"task_number": 2674, "title": "writeback events"},
            "packet": {
                "task_ref": {"project_id": "bh-migrated-7-aix", "task_number": 2674},
                "runtime": {"tenant_id": "default", "actor_id": "tester"},
            },
            "status": "blocked",
            "execution": {
                "status": "skipped",
                "run_result": {"status": "failed"},
                "changed_files": [],
                "commands": [],
                "tests": {},
                "diff_refs": [],
                "failure": None,
                "trace": {},
                "envelope": {},
            },
            "verification": {
                "classification": "blocked",
                "summary": "planner blocked",
                "checks": [{"name": "declared_validation", "passed": False}],
                "reasons": [{"message": "planner blocked"}],
                "verifier_decision": {"decision": "needs_fix"},
            },
        }
    )

    writeback_events = [kwargs for event, kwargs in logged if event == "writeback_result"]
    terminal_events = [kwargs for event, kwargs in logged if event == "terminal_failure"]
    assert writeback_events
    assert writeback_events[-1]["phase"] == "writeback"
    assert writeback_events[-1]["classification"] == "CODE_FAIL"
    assert terminal_events
    assert terminal_events[-1]["phase"] == "writeback"
    assert terminal_events[-1]["reason_code"] == "WRITEBACK_FAILED"


def test_workflow_state_machine_replay_after_restart() -> None:
    machine = TemporalWorkflowStateMachine()
    machine.transition(to_state="planning", reason_code="PLAN_STARTED")
    machine.transition(to_state="ready_for_review", reason_code="PLAN_READY")
    machine.transition(to_state="running", reason_code="APPROVED")
    machine.transition(to_state="verifying", reason_code="EXECUTION_COMPLETE")
    machine.transition(to_state="done", reason_code="VERIFIED")

    persisted_events = list(machine.events)
    restarted = TemporalWorkflowStateMachine()
    final_state = restarted.replay(persisted_events)

    assert final_state == "done"


def test_workflow_state_machine_rejects_invalid_replay() -> None:
    restarted = TemporalWorkflowStateMachine()
    events = [
        WorkflowTransitionEvent(sequence=1, from_state="queued", to_state="planning", reason_code="PLAN_STARTED"),
        WorkflowTransitionEvent(sequence=2, from_state="running", to_state="verifying", reason_code="BROKEN"),
    ]
    with pytest.raises(TemporalWorkflowTransitionError):
        restarted.replay(events)


def test_temporal_registry_linked_states_follow_lifecycle_registry() -> None:
    linked = set(temporal_registry_linked_states())

    assert "waiting_on_human" in linked
    assert TERMINAL_TASK_STATUS in linked
    assert REVIEW_PENDING_TASK_STATUSES.issubset(linked)


def test_temporal_review_pending_transitions_are_registry_derived() -> None:
    for state in REVIEW_PENDING_TASK_STATUSES:
        assert ALLOWED_WORKFLOW_TRANSITIONS[state] == {"running", TERMINAL_TASK_STATUS, "failed"}


def test_verifying_transitions_include_registry_review_pending_states() -> None:
    verifying_targets = ALLOWED_WORKFLOW_TRANSITIONS["verifying"]

    assert REVIEW_PENDING_TASK_STATUSES.issubset(verifying_targets)
    assert "waiting_on_human" in verifying_targets
    assert TERMINAL_TASK_STATUS in verifying_targets


@pytest.mark.asyncio
async def test_aix_v2_workflow_emits_missing_signal_when_verifier_starts_without_execution_state(
    monkeypatch,
) -> None:
    logged: list[tuple[str, dict[str, object]]] = []

    class _Logger:
        def info(self, event: str, **kwargs) -> None:
            logged.append((event, kwargs))

    class _WorkflowShim:
        logger = _Logger()

        @staticmethod
        async def execute_activity(fn, payload, start_to_close_timeout=None):  # noqa: ARG004
            name = getattr(fn, "__name__", "")
            if name == "load_task_activity":
                return payload
            if name == "recon_activity":
                updated = dict(payload)
                updated["recon"] = {"status": "ready"}
                return updated
            if name == "planner_activity":
                updated = dict(payload)
                updated["plan"] = {"status": "planned"}
                return updated
            if name == "verifier_activity":
                updated = dict(payload)
                updated["verification"] = {"classification": "failed", "reason_code": "MISSING_EXECUTION"}
                updated["failure"] = {"classification": "INFRA_FAIL", "code": "MISSING_EXECUTION"}
                return updated
            return payload

    monkeypatch.setattr(temporal_workflows, "workflow", _WorkflowShim)
    workflow_runner = temporal_workflows.AixOrchestratorV2Workflow()

    result = await workflow_runner.run(
        {
            "run_id": "aixv2-test",
            "task_id": "task-1",
            "task_number": 2750,
            "packet": {"runtime": {"writeback": False}},
        }
    )

    assert result["verification"]["reason_code"] == "MISSING_EXECUTION"
    terminal_events = [kwargs for event, kwargs in logged if event == "terminal_failure"]
    assert any(event["reason_code"] == "VERIFIER_CALLED_WITHOUT_EXECUTION_STATE" for event in terminal_events)


@pytest.mark.asyncio
async def test_aix_v2_workflow_emits_missing_signal_when_planner_skip_has_no_failure_reason(monkeypatch) -> None:
    logged: list[tuple[str, dict[str, object]]] = []

    class _Logger:
        def info(self, event: str, **kwargs) -> None:
            logged.append((event, kwargs))

    class _WorkflowShim:
        logger = _Logger()

        @staticmethod
        async def execute_activity(fn, payload, start_to_close_timeout=None):  # noqa: ARG004
            name = getattr(fn, "__name__", "")
            if name == "load_task_activity":
                return payload
            if name == "recon_activity":
                updated = dict(payload)
                updated["recon"] = {"status": "insufficient"}
                return updated
            if name == "verifier_activity":
                updated = dict(payload)
                updated["execution"] = {"status": "skipped"}
                updated["verification"] = {"classification": "blocked", "reason_code": "RECON_INSUFFICIENT"}
                return updated
            return payload

    monkeypatch.setattr(temporal_workflows, "workflow", _WorkflowShim)
    workflow_runner = temporal_workflows.AixOrchestratorV2Workflow()

    await workflow_runner.run(
        {
            "run_id": "aixv2-test",
            "task_id": "task-2",
            "task_number": 2750,
            "packet": {"runtime": {"writeback": False}},
        }
    )

    planner_skips = [kwargs for event, kwargs in logged if event == "planner_skipped"]
    terminal_events = [kwargs for event, kwargs in logged if event == "terminal_failure"]
    assert planner_skips
    assert planner_skips[-1]["reason_code"] == "RECON_INSUFFICIENT"
    assert any(event["reason_code"] == "PLANNER_SKIPPED_WITHOUT_REASON" for event in terminal_events)


@pytest.mark.asyncio
async def test_aix_v2_workflow_run_does_not_touch_workflow_logger(monkeypatch) -> None:
    class _WorkflowShim:
        @property
        def logger(self):  # pragma: no cover - defensive tripwire
            raise AssertionError("workflow.logger should not be accessed inside AixOrchestratorV2Workflow.run")

        @staticmethod
        async def execute_activity(fn, payload, start_to_close_timeout=None):  # noqa: ARG004
            name = getattr(fn, "__name__", "")
            if name == "load_task_activity":
                return payload
            if name == "recon_activity":
                updated = dict(payload)
                updated["recon"] = {"status": "ready"}
                return updated
            if name == "planner_activity":
                updated = dict(payload)
                updated["plan"] = {"status": "planned"}
                return updated
            if name == "execution_activity":
                updated = dict(payload)
                updated["execution"] = {"status": "completed"}
                return updated
            if name == "verifier_activity":
                updated = dict(payload)
                updated["verification"] = {"classification": "success", "reason_code": "VALIDATION_PASSED"}
                return updated
            return payload

    monkeypatch.setattr(temporal_workflows, "workflow", _WorkflowShim())
    workflow_runner = temporal_workflows.AixOrchestratorV2Workflow()

    result = await workflow_runner.run(
        {
            "run_id": "aixv2-no-logger",
            "task_id": "task-3",
            "task_number": 2751,
            "packet": {"runtime": {"writeback": False}},
        }
    )

    assert result["verification"]["classification"] == "success"
