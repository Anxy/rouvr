"""Tests for F6-P2 Checkpoint Persistence & Recovery Restart."""

from __future__ import annotations

import pytest

from backend.aix_core.framework.recovery.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointEventV1,
    ObjectiveCycleCheckpointStore,
)


def _build_checkpoint(seq: int, reason: str) -> CheckpointEventV1:
    recorded_at = f"2026-08-29T12:00:0{seq}Z"
    workflow = {"phase": reason, "engine_version": seq}
    dispatcher = {"state": reason, "step_id": f"step-{seq}"}
    plan = {"plan_id": "plan-1", "revision": 1}
    budgets = {"retries_remaining": 2, "replans_remaining": 1}
    evidence_refs = (f"sha256:ref{seq}",)
    decision_state = {"cursor": f"step-{seq}"}

    state_hash = CheckpointEventV1.compute_state_hash(
        schema_version=CHECKPOINT_SCHEMA_VERSION,
        checkpoint_id=f"chk_{seq}",
        tenant_id="tenant-1",
        objective_id="obj-1",
        cycle_id="cycle-1",
        event_sequence=seq,
        command_id=f"cmd-{seq}:checkpoint",
        reason=reason,
        recorded_at=recorded_at,
        workflow=workflow,
        dispatcher=dispatcher,
        plan=plan,
        attempt=None,
        budgets=budgets,
        approval=None,
        evidence_refs=evidence_refs,
        decision_state=decision_state,
    )

    return CheckpointEventV1(
        checkpoint_id=f"chk_{seq}",
        tenant_id="tenant-1",
        objective_id="obj-1",
        cycle_id="cycle-1",
        event_sequence=seq,
        command_id=f"cmd-{seq}:checkpoint",
        reason=reason,  # type: ignore[arg-type]
        recorded_at=recorded_at,
        workflow=workflow,
        dispatcher=dispatcher,
        plan=plan,
        attempt=None,
        budgets=budgets,
        approval=None,
        evidence_refs=evidence_refs,
        decision_state=decision_state,
        state_hash=state_hash,
    )


@pytest.mark.asyncio
async def test_checkpoint_store_append_and_latest():
    store = ObjectiveCycleCheckpointStore()

    # Step 1: POST_ADMISSION
    chk1 = _build_checkpoint(1, "POST_ADMISSION")
    await store.append(chk1)

    latest = await store.latest("tenant-1", "cycle-1")
    assert latest is not None
    assert latest.checkpoint_id == "chk_1"
    assert latest.reason == "POST_ADMISSION"

    # Step 2: POST_ATTEMPT_COMMIT
    chk2 = _build_checkpoint(2, "POST_ATTEMPT_COMMIT")
    await store.append(chk2)

    latest2 = await store.latest("tenant-1", "cycle-1")
    assert latest2 is not None
    assert latest2.checkpoint_id == "chk_2"
    assert latest2.event_sequence == 2

    # Query by ID
    fetched1 = await store.by_id("tenant-1", "chk_1")
    assert fetched1 is not None
    assert fetched1.checkpoint_id == "chk_1"


@pytest.mark.asyncio
async def test_checkpoint_store_rejects_corrupted_hash():
    store = ObjectiveCycleCheckpointStore()
    corrupt_chk = CheckpointEventV1(
        checkpoint_id="chk_bad",
        tenant_id="tenant-1",
        objective_id="obj-1",
        cycle_id="cycle-1",
        event_sequence=1,
        command_id="cmd-1:checkpoint",
        reason="POST_ADMISSION",
        recorded_at="2026-08-29T12:00:00Z",
        workflow={},
        dispatcher={},
        plan={},
        attempt=None,
        budgets={},
        approval=None,
        evidence_refs=(),
        decision_state={},
        state_hash="invalid_hash_string",
    )

    with pytest.raises(ValueError, match="integrity validation failed"):
        await store.append(corrupt_chk)
