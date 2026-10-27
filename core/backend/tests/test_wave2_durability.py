"""Wave 2 durability honesty: Option B heartbeat, timeout->FAILED, PG fail-closed.

Accept gates:
- dispatch/verify activities carry no heartbeat timeout (slow models must not
  die on a 30s heartbeat); expiry raises and the workflow records FAILED.
- A raising activity (timeout incl. dead worker) terminalizes the engine to
  FAILED with correlation id — never a stranded RUNNING_STEP.
- DATABASE_URL set + PG unreachable => raise (fail closed), unless
  AIX_OFFLINE=1, which is the only JSON/inprocess path.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from backend.workflows.temporal.objective_cycle_contracts import (
    OBJECTIVE_CYCLE_ACTIVITY_CONTRACTS,
    ObjectiveCycleWorkflowInput,
)
from backend.workflows.temporal.objective_cycle_workflow import (
    AixObjectiveCycleWorkflow,
    AixObjectiveCycleWorkflowEngine,
)

HASH = "a" * 64


def _workflow_input() -> ObjectiveCycleWorkflowInput:
    return ObjectiveCycleWorkflowInput(
        workflow_version=1,
        tenant_id="tenant-1",
        objective_id="objective-1",
        cycle_id="cycle-1",
        candidate_sha="b" * 40,
        plan_sha256=HASH,
        start_request_id="request-1",
        correlation_id="correlation-1",
    )


def _running_engine() -> AixObjectiveCycleWorkflowEngine:
    engine = AixObjectiveCycleWorkflowEngine(
        _workflow_input(), clock=lambda: datetime.now(UTC)
    )
    engine.state.phase = "RUNNING_STEP"
    return engine


def test_backend_shelling_activities_have_no_heartbeat_timeout() -> None:
    by_name = {c.name: c for c in OBJECTIVE_CYCLE_ACTIVITY_CONTRACTS}
    assert by_name["dispatch_step"].heartbeat_timeout_seconds is None
    assert by_name["verify_step"].heartbeat_timeout_seconds is None
    assert by_name["dispatch_step"].start_to_close_seconds == 1800
    assert by_name["verify_step"].start_to_close_seconds == 900


@pytest.mark.asyncio
async def test_raising_activity_terminalizes_engine_failed(monkeypatch) -> None:
    wf = AixObjectiveCycleWorkflow()
    wf._engine = _running_engine()

    class ActivityError(Exception):
        pass

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise ActivityError("StartToClose timeout after 1800s")

    import temporalio.workflow

    monkeypatch.setattr(temporalio.workflow, "execute_activity", _boom)
    await wf._execute_step_activity("dispatch_step", step_id="s1", attempt_id="a1")

    engine = wf._engine
    assert engine is not None
    assert engine.state.phase == "FAILED"
    last = engine.state.history[-1]
    assert "DISPATCH_STEP_ACTIVITY_ERROR" in str(last)
    assert "correlation_id:correlation-1" in str(engine.state.history)


@pytest.mark.asyncio
async def test_cancelled_activity_records_cancelled_and_reraises(monkeypatch) -> None:
    wf = AixObjectiveCycleWorkflow()
    wf._engine = _running_engine()

    async def _cancelled(*args: Any, **kwargs: Any) -> Any:
        raise asyncio.CancelledError("server cancelled")

    import temporalio.workflow

    monkeypatch.setattr(temporalio.workflow, "execute_activity", _cancelled)
    with pytest.raises(asyncio.CancelledError):
        await wf._execute_step_activity("dispatch_step", step_id="s1", attempt_id="a1")

    engine = wf._engine
    assert engine is not None
    assert engine.state.phase == "CANCELLED"


def _attach_settings(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "OBJECTIVE_CYCLE_ALLOW_INPROCESS_LEDGER": False,
        "OBJECTIVE_CYCLE_PREFER_POSTGRES": True,
        "OBJECTIVE_CYCLE_REAL_EXECUTE": False,
        "OBJECTIVE_CYCLE_REQUIRE_WORKHUB_LOAD": False,
        "OBJECTIVE_CYCLE_CREATE_WORKHUB_TASK": False,
        "OBJECTIVE_CYCLE_MARK_WORKHUB_DONE": False,
        "DATABASE_URL": "postgresql://127.0.0.1:1/unreachable",
        "AIX_OFFLINE": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_fail_closed_when_pg_unreachable(monkeypatch) -> None:
    from backend.aix_core.runtime.objective import runtime_attach
    from backend.aix_core.runtime.objective.repository import ObjectiveCycleRepositoryError

    monkeypatch.setattr(runtime_attach, "resolve_test_database_url", lambda: None)
    monkeypatch.setattr(
        runtime_attach, "ObjectiveCycleRepository", lambda *a, **k: object()
    )

    import asyncpg

    async def _no_pool(*args: Any, **kwargs: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(asyncpg, "create_pool", _no_pool)

    with pytest.raises(ObjectiveCycleRepositoryError, match="refusing silent file fallback"):
        await runtime_attach.build_objective_cycle_api_backend(_attach_settings())


@pytest.mark.asyncio
async def test_offline_flag_is_the_only_file_fallback(monkeypatch) -> None:
    from backend.aix_core.runtime.objective import runtime_attach

    monkeypatch.setattr(runtime_attach, "resolve_test_database_url", lambda: None)
    monkeypatch.setattr(
        runtime_attach, "ObjectiveCycleRepository", lambda *a, **k: object()
    )

    import asyncpg

    async def _no_pool(*args: Any, **kwargs: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(asyncpg, "create_pool", _no_pool)

    backend = await runtime_attach.build_objective_cycle_api_backend(
        _attach_settings(AIX_OFFLINE=True, OBJECTIVE_CYCLE_ALLOW_INPROCESS_LEDGER=True)
    )
    assert backend is not None
    assert backend.mode == "inprocess"
