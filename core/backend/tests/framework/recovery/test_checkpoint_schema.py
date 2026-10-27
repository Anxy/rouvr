"""Tests for F6-P2 Checkpoint Schema, Hash Integrity & Serialization."""

from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from backend.aix_core.framework.recovery.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointEventV1,
)


def _build_sample_checkpoint(
    *,
    checkpoint_id: str = "chk_100",
    tenant_id: str = "tenant-1",
    objective_id: str = "obj-1",
    cycle_id: str = "cycle-1",
    event_sequence: int = 5,
    command_id: str = "cmd-1:checkpoint",
    reason: str = "POST_ATTEMPT_COMMIT",
) -> CheckpointEventV1:
    workflow = {"phase": "VERIFYING", "engine_version": 1}
    dispatcher = {"state": "VERIFYING", "step_id": "step-1", "claim_id": "claim-1"}
    plan = {"plan_id": "plan-1", "revision": 1}
    attempt = {"attempt_id": "att-1", "outcome": "SUCCEEDED"}
    budgets = {"retries_remaining": 2, "replans_remaining": 1}
    approval = None
    evidence_refs = ("sha256:diff123", "sha256:test456")
    decision_state = {"cursor": "step-1", "terminal": False}
    recorded_at = "2026-08-29T12:00:00Z"

    state_hash = CheckpointEventV1.compute_state_hash(
        schema_version=CHECKPOINT_SCHEMA_VERSION,
        checkpoint_id=checkpoint_id,
        tenant_id=tenant_id,
        objective_id=objective_id,
        cycle_id=cycle_id,
        event_sequence=event_sequence,
        command_id=command_id,
        reason=reason,
        recorded_at=recorded_at,
        workflow=workflow,
        dispatcher=dispatcher,
        plan=plan,
        attempt=attempt,
        budgets=budgets,
        approval=approval,
        evidence_refs=evidence_refs,
        decision_state=decision_state,
        projection_ref="snapshot://1",
        workhub_task_id="wh-task-1",
    )

    return CheckpointEventV1(
        checkpoint_id=checkpoint_id,
        tenant_id=tenant_id,
        objective_id=objective_id,
        cycle_id=cycle_id,
        event_sequence=event_sequence,
        command_id=command_id,
        reason=reason,  # type: ignore[arg-type]
        recorded_at=recorded_at,
        workflow=workflow,
        dispatcher=dispatcher,
        plan=plan,
        attempt=attempt,
        budgets=budgets,
        approval=approval,
        evidence_refs=evidence_refs,
        decision_state=decision_state,
        projection_ref="snapshot://1",
        workhub_task_id="wh-task-1",
        state_hash=state_hash,
    )


def test_checkpoint_event_integrity_validation():
    chk = _build_sample_checkpoint()
    assert chk.validate_integrity() is True
    assert len(chk.state_hash) == 64


def test_checkpoint_event_tampering_detected():
    chk = _build_sample_checkpoint()

    # Tampered event sequence
    tampered = CheckpointEventV1(
        checkpoint_id=chk.checkpoint_id,
        tenant_id=chk.tenant_id,
        objective_id=chk.objective_id,
        cycle_id=chk.cycle_id,
        event_sequence=999,  # tampered!
        command_id=chk.command_id,
        reason=chk.reason,
        recorded_at=chk.recorded_at,
        workflow=chk.workflow,
        dispatcher=chk.dispatcher,
        plan=chk.plan,
        attempt=chk.attempt,
        budgets=chk.budgets,
        approval=chk.approval,
        evidence_refs=chk.evidence_refs,
        decision_state=chk.decision_state,
        state_hash=chk.state_hash,
    )
    assert tampered.validate_integrity() is False


def test_checkpoint_serialization_round_trip():
    chk = _build_sample_checkpoint()
    data = chk.to_dict()
    assert data["checkpoint_id"] == "chk_100"
    assert data["schema_version"] == CHECKPOINT_SCHEMA_VERSION

    reconstructed = CheckpointEventV1.from_dict(data)
    assert reconstructed == chk
    assert reconstructed.validate_integrity() is True


def test_checkpoint_immutability():
    chk = _build_sample_checkpoint()
    with pytest.raises(FrozenInstanceError):
        chk.event_sequence = 10  # type: ignore[misc]
