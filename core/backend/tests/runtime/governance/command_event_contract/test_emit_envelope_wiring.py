"""C006-T04: event emit path attaches command/event envelope metadata."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.command_event_contract import COMMAND_EVENT_CONTRACT_VERSION
from backend.aix_core.runtime.objective.durable_api_backend import (
    DurableObjectiveCycleApiBackend,
    _CycleRecord,
)


@pytest.mark.asyncio
async def test_emit_event_attaches_command_event_envelope() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", repository=None)
    cycle = _CycleRecord(
        cycle_id="cyc_env_1",
        objective_id="obj_env_1",
        tenant_id="tenant-1",
        candidate_sha="sha",
        budgets={},
    )
    backend._emit_event(  # noqa: SLF001 - intentional unit probe of emit path
        cycle=cycle,
        event_type="cycle.started",
        payload_refs={"actor_id": "human-1", "command_id": "cmd_start_1"},
    )
    assert len(cycle.events) == 1
    event = cycle.events[0]
    assert event["contract_version"] == COMMAND_EVENT_CONTRACT_VERSION
    assert event["envelope"]["contract_version"] == COMMAND_EVENT_CONTRACT_VERSION
    assert event["envelope"]["event_type"] == "cycle.started"
    assert event["envelope"]["tenant_id"] == "tenant-1"
    assert event["envelope"]["run_id"] == "cyc_env_1"
    assert event["envelope"]["sequence_number"] == 1
