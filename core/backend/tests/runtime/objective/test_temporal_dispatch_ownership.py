"""PLAT-D2: single dispatch ownership — Temporal owns dispatch progression.

When Temporal mode is on, backend background self-drive parks at STEP_READY
instead of dispatching inline; the workflow's dispatch_step activity is the
only executor. Temporal-off behavior (inline self-drive, sync tests) is
unchanged. No contract-engine dependency; runs in the default suite.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.aix_core.runtime.objective.durable_aggregate.replay import InMemoryProjectionStore
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


async def _admit_start(backend: DurableObjectiveCycleApiBackend, *, suffix: str) -> str:
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-plat",
        actor_id="actor-1",
        resource_id=None,
        body={
            "workhub_task_id": f"wh-plat-{suffix}",
            "objective": "PLAT-D2 ownership proof",
            "task_class": "bounded_repair",
            "definition_of_done": ("ownership",),
            "repository_id": "repo-plat",
            "allowed_files": (),
        },
        idempotency_key=f"admit-{suffix}",
        expected_version=None,
    )
    assert admit.get("ok") is True, admit
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-plat",
        actor_id="actor-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "platsha1"},
        idempotency_key=f"start-{suffix}",
        expected_version=None,
    )
    assert started.get("ok") is True, started
    return started["cycle_id"]


def _backend(*, temporal: bool) -> DurableObjectiveCycleApiBackend:
    return DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=InMemoryProjectionStore(),
        settings=SimpleNamespace(TEMPORAL_ENABLED=temporal),
    )


def test_temporal_flag_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("HYEVE_AIX_SERVICE_TEMPORAL_ENABLED", raising=False)
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        projection_store=InMemoryProjectionStore(),
    )
    assert backend._temporal_owns_dispatch is False


@pytest.mark.asyncio
async def test_temporal_owned_parks_instead_of_scheduling() -> None:
    backend = _backend(temporal=True)
    assert backend._temporal_owns_dispatch is True
    cycle_id = await _admit_start(backend, suffix="park")
    cycle = backend._cycles[cycle_id]

    async def _forbidden_dispatch(**kwargs) -> None:  # pragma: no cover
        raise AssertionError("inline dispatch must not run under Temporal ownership")

    backend.dispatch_ready_step = _forbidden_dispatch  # type: ignore[method-assign]
    ready = {"ok": True, "operation": "ready", "dispatch_required": True}
    out = await backend._dispatch_after_ready(
        cycle=cycle,
        tenant_id="tenant-plat",
        cycle_id=cycle_id,
        command_id="cmd-park",
        caller_auth=None,
        actor_id="actor-1",
        ready=ready,
    )
    assert out is not None
    assert out.get("note") == "dispatch_parked_temporal_owned"
    assert out.get("async_execution") is False
    assert cycle_id not in backend._execute_tasks


@pytest.mark.asyncio
async def test_temporal_off_still_self_drives() -> None:
    backend = _backend(temporal=False)
    assert backend._temporal_owns_dispatch is False
    cycle_id = await _admit_start(backend, suffix="drive")
    cycle = backend._cycles[cycle_id]

    called: list[dict] = []

    async def _fake_dispatch(**kwargs):
        called.append(kwargs)
        return {"ok": True, "operation": "dispatch_ready_step"}

    backend.dispatch_ready_step = _fake_dispatch  # type: ignore[method-assign]
    ready = {"ok": True, "operation": "ready", "dispatch_required": True}
    out = await backend._dispatch_after_ready(
        cycle=cycle,
        tenant_id="tenant-plat",
        cycle_id=cycle_id,
        command_id="cmd-drive",
        caller_auth=None,
        actor_id="actor-1",
        ready=ready,
    )
    assert out is not None
    assert out.get("note") == "dispatch_scheduled_after_step_ready"
    assert cycle_id in backend._execute_tasks
    await backend._execute_tasks[cycle_id]
    assert called and called[0].get("cycle_id") == cycle_id
