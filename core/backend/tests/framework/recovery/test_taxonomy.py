"""Tests for F6-P1 Recovery Taxonomy & Action Records."""

from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from backend.aix_core.framework.recovery.taxonomy import (
    Checkpoint,
    Escalate,
    RecoveryMechanism,
    Repair,
    Replan,
    Resume,
    Retry,
)
from backend.aix_core.runtime.objective.contracts import RepairDirectiveV1


def test_recovery_mechanisms_enumeration():
    assert RecoveryMechanism.RETRY.value == "RETRY"
    assert RecoveryMechanism.REPAIR.value == "REPAIR"
    assert RecoveryMechanism.REPLAN.value == "REPLAN"
    assert RecoveryMechanism.CHECKPOINT.value == "CHECKPOINT"
    assert RecoveryMechanism.RESUME.value == "RESUME"
    assert RecoveryMechanism.ESCALATE.value == "ESCALATE"


def test_retry_action_record():
    retry = Retry(
        failure_ref="sha256:fail123",
        step_id="step-1",
        next_attempt=2,
        budget_after=1,
    )
    assert retry.failure_ref == "sha256:fail123"
    assert retry.step_id == "step-1"
    assert retry.next_attempt == 2
    assert retry.budget_after == 1

    d = retry.to_dict()
    assert d["mechanism"] == "RETRY"
    assert d["next_attempt"] == 2

    with pytest.raises(FrozenInstanceError):
        retry.next_attempt = 3  # type: ignore[misc]


def test_repair_action_record():
    retry = Retry(failure_ref="sha256:fail123", step_id="step-1", next_attempt=2, budget_after=1)
    directive = RepairDirectiveV1(
        step_id="step-1",
        required_correction="fix syntax error",
        failure_refs=("sha256:fail123",),
    )
    repair = Repair(failure_ref="sha256:fail123", directive=directive, retry=retry)

    assert repair.failure_ref == "sha256:fail123"
    assert repair.directive == directive
    assert repair.retry == retry

    d = repair.to_dict()
    assert d["mechanism"] == "REPAIR"
    assert d["retry"]["next_attempt"] == 2


def test_replan_checkpoint_resume_escalate_records():
    replan = Replan(decision={"plan_id": "p1"}, budget_after=2)
    assert replan.budget_after == 2
    assert replan.to_dict()["mechanism"] == "REPLAN"

    chk = Checkpoint(checkpoint_id="chk_1", event_sequence=10, state_hash="hash123")
    assert chk.checkpoint_id == "chk_1"
    assert chk.to_dict()["mechanism"] == "CHECKPOINT"

    res = Resume(resume_key="key_1", checkpoint_id="chk_1", disposition="APPLIED")
    assert res.resume_key == "key_1"
    assert res.to_dict()["mechanism"] == "RESUME"

    esc = Escalate(escalation_id="esc_1", reason_code="BUDGET_EXHAUSTED", evidence_refs=("ev1",))
    assert esc.reason_code == "BUDGET_EXHAUSTED"
    assert esc.to_dict()["mechanism"] == "ESCALATE"
