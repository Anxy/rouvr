"""AIX Feature 12 (Packet 12) — Unit tests for Operator Observability."""

import pytest
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.verification.objective_truth import derive_canonical_truth
from backend.aix_core.runtime.verification.objective_verification_contract import derive_ovc
from backend.aix_core.runtime.verification.gap import GapV1


@pytest.mark.asyncio
async def test_cycle_observability_fields_present():
    backend = DurableObjectiveCycleApiBackend()
    
    # Admit objective
    admit_res = await backend._admit(
        tenant_id="tenant-obs-1",
        actor_id="tester",
        command_id="cmd-admit-1",
        body={
            "objective": "Build FastAPI + SQLite Expense Tracker",
            "task_class": "execute",
            "definition_of_done": ["FastAPI", "SQLite", "Auth"],
            "workhub_task_id": "task-obs-1",
        },
    )
    assert admit_res["ok"]
    obj_id = admit_res["objective_id"]
    
    # Check truth & OVC sealed
    truth = backend._canonical_truths.get(obj_id)
    ovc = backend._objective_verification_contracts.get(obj_id)
    assert truth is not None
    assert ovc is not None

    # Start cycle
    start_res = await backend._start_cycle(
        tenant_id="tenant-obs-1",
        actor_id="tester",
        objective_id=obj_id,
        body={"candidate_sha": "abcdef12345"},
        command_id="cmd-start-1",
    )
    assert start_res["ok"]
    cyc_id = start_res["cycle_id"]

    cycle = backend._cycles[cyc_id]
    
    # Evaluate terminalization (will fail because no code yet)
    tdec = backend._evaluate_cycle_terminalization(cycle)
    
    # Check control payload contains observability fields
    control_payload = backend._control_payload_with_synced_phase(cycle, start_res)
    assert "canonical_truth_hash" in control_payload
    assert control_payload["canonical_truth_hash"] == truth.hash
    assert "ovc_hash" in control_payload
    assert control_payload["ovc_hash"] == ovc.hash
    assert "open_gaps_count" in control_payload
    assert "terminalization_decision" in control_payload
    assert control_payload["terminalization_decision"] == tdec.decision
