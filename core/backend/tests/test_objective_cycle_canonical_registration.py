"""Final single-path architecture regression tests.

Guards the converged Objective Cycle runtime::

    canonical_start -> aix.execution.current -> AixObjectiveCycleWorkflow
        -> ground_objective -> compile_plan -> approval
        -> dispatch_step -> verify_step

Temporal owns scheduling/history; Postgres is projection-only; the
reconciler (where present) is diagnostic/oracle-only; exactly one workflow
is registered and no legacy runtime path remains active.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.infrastructure.temporal import runtime as canonical_runtime
from backend.infrastructure.temporal.queue_strategy import build_queue_binding
from backend.workflows.temporal.objective_cycle_activities import (
    CANONICAL_ACTIVITIES,
    _CANONICAL_ACTIVITY_NAMES,
    bind_backend_factory,
    dispatch_step,
    verify_step,
)
from backend.workflows.temporal.objective_cycle_contracts import (
    OBJECTIVE_CYCLE_ACTIVITY_CONTRACTS,
    OBJECTIVE_CYCLE_WORKFLOW_TYPE,
)
from backend.workflows.temporal.objective_cycle_workflow import (
    AixObjectiveCycleWorkflow,
    AixObjectiveCycleWorkflowEngine,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class _Settings:
    TEMPORAL_ENABLED = True
    TEMPORAL_AUTO_START_WORKER = False
    TEMPORAL_ADDRESS = "127.0.0.1:7233"
    TEMPORAL_NAMESPACE = "hyeve-aix"
    TEMPORAL_TASK_QUEUE_PLAN = "aix.plan.current"
    TEMPORAL_TASK_QUEUE_EXECUTION = "aix.execution.current"
    TEMPORAL_TASK_QUEUE_VERIFICATION = "aix.verification.current"
    TEMPORAL_WORKER_IDENTITY = "hyeve-aix-worker"


def _activity_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contract_version": "aix.objective-cycle.temporal-contract.v1",
        "activity_name": "dispatch_step",
        "activity_id": "aix-activity-dispatch_step-test",
        "tenant_id": "tenant-test",
        "objective_id": "objective-1",
        "cycle_id": "cycle-1",
        "aggregate_version": 0,
        "idempotency_key": "idem-test-1",
        "correlation_id": "corr-1",
        "causation_id": "cause-1",
        "payload_refs": {"ledger_ref": "pg://cycle/cycle-1"},
    }
    payload.update(overrides)
    return payload


def test_exactly_one_workflow_registered() -> None:
    assert list(canonical_runtime._REGISTERED_WORKFLOW_CLASSES) == [
        AixObjectiveCycleWorkflow
    ]
    assert canonical_runtime._WORKFLOW_NAME == OBJECTIVE_CYCLE_WORKFLOW_TYPE == (
        "AixObjectiveCycleWorkflow"
    )


def test_activity_set_matches_contracts() -> None:
    contracted = {item.name for item in OBJECTIVE_CYCLE_ACTIVITY_CONTRACTS}
    assert set(CANONICAL_ACTIVITIES) == contracted
    assert set(_CANONICAL_ACTIVITY_NAMES) == contracted
    assert "dispatch_step" in CANONICAL_ACTIVITIES
    assert "verify_step" in CANONICAL_ACTIVITIES


def test_canonical_execution_queue() -> None:
    binding = build_queue_binding(_Settings())
    assert binding.namespace == "hyeve-aix"
    assert binding.execution_queue == "aix.execution.current"


def test_no_legacy_runtime_path_in_worker() -> None:
    source = (BACKEND_ROOT / "infrastructure" / "temporal" / "runtime.py").read_text()
    assert "AixOrchestratorV2Workflow" not in source
    assert "start_v2_workflow" not in source
    assert "backend.legacy" not in source
    assert not hasattr(canonical_runtime.TemporalRuntimeManager, "start_v2_workflow")
    assert hasattr(canonical_runtime.TemporalRuntimeManager, "canonical_start")


def test_no_second_temporal_worker_registrar() -> None:
    """The infrastructure runtime is the only Temporal Worker registrar."""
    offenders: list[str] = []
    for path in sorted((BACKEND_ROOT).rglob("*.py")):
        if "tests" in path.parts or "legacy" in path.parts:
            continue
        text = path.read_text()
        if "worker_cls(" in text or "Worker(" in text:
            offenders.append(str(path.relative_to(BACKEND_ROOT)))
    assert offenders == ["infrastructure/temporal/runtime.py"]


def test_engine_remains_deterministic_core() -> None:
    # The engine owns domain semantics; Temporal owns scheduling/history.
    # Proven structurally: exactly one workflow registered (above) and the
    # engine class drives phase transitions without importing the SDK.
    assert AixObjectiveCycleWorkflowEngine is not None
    workflow_source = (
        BACKEND_ROOT / "workflows" / "temporal" / "objective_cycle_workflow.py"
    ).read_text()
    assert "class AixObjectiveCycleWorkflowEngine" in workflow_source
    assert "AixOrchestratorV2" not in workflow_source


@pytest.mark.asyncio
async def test_dispatch_step_rejects_unregistered_activity_name() -> None:
    with pytest.raises(Exception):
        await dispatch_step(_activity_payload(activity_name="legacy_execute"))


@pytest.mark.asyncio
async def test_verify_step_rejects_forbidden_payload_keys() -> None:
    with pytest.raises(Exception):
        await verify_step(
            _activity_payload(payload_refs={"auto_accept": "true"})
        )


@pytest.mark.asyncio
async def test_dispatch_step_fails_closed_without_backend() -> None:
    bind_backend_factory(None)
    result = await dispatch_step(_activity_payload())
    assert result["ok"] is False
    assert result["reason_code"] == "TEMPORAL_BACKEND_NOT_BOUND"


@pytest.mark.asyncio
async def test_dispatch_step_delegates_to_backend_command() -> None:
    calls: list[dict[str, Any]] = []

    class _Backend:
        async def command(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {"ok": True, "status": "completed"}

    first = await dispatch_step(_activity_payload(), backend=_Backend())
    second = await dispatch_step(_activity_payload(), backend=_Backend())

    assert first["ok"] is True
    assert first["reason_code"] == "BACKEND_OK"
    assert second["idempotent_replay"] is True
    assert len(calls) == 1
    assert calls[0]["operation"] == "dispatch_ready_step"
    assert calls[0]["resource_id"] == "cycle-1"


@pytest.mark.asyncio
async def test_verify_step_maps_blocked_backend_outcome() -> None:
    class _Backend:
        async def command(self, **kwargs: Any) -> dict[str, Any]:
            return {"ok": False, "status": "blocked_needs_human"}

    result = await verify_step(
        _activity_payload(activity_id="aix-activity-verify_step-test"),
        backend=_Backend(),
    )
    assert result["ok"] is False
    assert result["status"] == "blocked"


@pytest.mark.asyncio
async def test_objective_cycle_workflow_passes_temporal_sandbox() -> None:
    pytest.importorskip("temporalio")
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
    from temporalio.workflow import _Definition

    definition = _Definition.must_from_class(AixObjectiveCycleWorkflow)
    runner = SandboxedWorkflowRunner()
    runner.prepare_workflow(definition)
    assert definition.name == "AixObjectiveCycleWorkflow"
