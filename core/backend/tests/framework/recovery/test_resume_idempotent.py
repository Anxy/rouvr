"""Tests for F6-P3 ResumeProtocol Idempotency & Deduplication."""

from __future__ import annotations

import pytest

from backend.aix_core.framework.recovery.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointEventV1,
    ObjectiveCycleCheckpointStore,
)
from backend.aix_core.framework.recovery.resume import (
    ResumeDisposition,
    ResumeProtocol,
    ResumeRequest,
)


def _build_checkpoint(chk_id: str, seq: int) -> CheckpointEventV1:
    recorded_at = "2026-08-29T12:00:00Z"
    workflow = {"phase": "POST_ATTEMPT_COMMIT", "engine_version": seq}
    dispatcher = {"state": "POST_ATTEMPT_COMMIT", "step_id": "step-1"}
    plan = {"plan_id": "plan-1", "revision": 1}
    budgets = {"retries_remaining": 2, "replans_remaining": 1}
    evidence_refs = ("sha256:ref1",)
    decision_state = {"cursor": "step-1"}

    state_hash = CheckpointEventV1.compute_state_hash(
        schema_version=CHECKPOINT_SCHEMA_VERSION,
        checkpoint_id=chk_id,
        tenant_id="tenant-1",
        objective_id="obj-1",
        cycle_id="cycle-1",
        event_sequence=seq,
        command_id=f"cmd-{seq}:checkpoint",
        reason="POST_ATTEMPT_COMMIT",
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
        checkpoint_id=chk_id,
        tenant_id="tenant-1",
        objective_id="obj-1",
        cycle_id="cycle-1",
        event_sequence=seq,
        command_id=f"cmd-{seq}:checkpoint",
        reason="POST_ATTEMPT_COMMIT",
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
async def test_resume_idempotent_repeated_calls():
    store = ObjectiveCycleCheckpointStore()
    chk = _build_checkpoint("chk_10", 10)
    await store.append(chk)

    protocol = ResumeProtocol(checkpoint_store=store)

    req = ResumeRequest(
        tenant_id="tenant-1",
        cycle_id="cycle-1",
        checkpoint_id="chk_10",
        command_id="cmd-resume-1",
    )

    # First call: APPLIED
    res1 = await protocol.resume(req)
    assert res1.disposition == ResumeDisposition.APPLIED
    assert res1.checkpoint_id == "chk_10"
    assert res1.projection["event_sequence"] == 10

    # Second call (same key): ALREADY_APPLIED with identical projection
    res2 = await protocol.resume(req)
    assert res2.disposition == ResumeDisposition.ALREADY_APPLIED
    assert res2.checkpoint_id == "chk_10"
    assert res2.projection == res1.projection
    assert res2.resume_key == res1.resume_key


def test_resume_request_idempotency_key_stability():
    req1 = ResumeRequest(tenant_id="t1", cycle_id="c1", checkpoint_id="chk1", command_id="cmd1")
    req2 = ResumeRequest(tenant_id="t1", cycle_id="c1", checkpoint_id="chk1", command_id="cmd1")
    req3 = ResumeRequest(tenant_id="t1", cycle_id="c1", checkpoint_id="chk1", command_id="cmd2")

    assert req1.idempotency_key() == req2.idempotency_key()
    assert req1.idempotency_key() != req3.idempotency_key()
