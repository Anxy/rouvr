"""EX2-S007: knowledge store + adapter/verification/distill hooks."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter.adapters import (
    AdapterOutcome,
    distill_knowledge_proposals,
    map_adapter_outcome_without_verification,
    verify_adapter_outcome,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError


@pytest.mark.asyncio
async def test_adapter_never_auto_verifies() -> None:
    assert map_adapter_outcome_without_verification(AdapterOutcome(kind="SUCCEEDED")) == "AWAITING_VERIFICATION"
    assert map_adapter_outcome_without_verification(AdapterOutcome(kind="FAILED", reason_code="X")) == "FAILED"
    assert map_adapter_outcome_without_verification(AdapterOutcome(kind="BLOCKED")) == "BLOCKED"


def test_verification_requires_evidence_for_accept() -> None:
    outcome = AdapterOutcome(kind="SUCCEEDED", evidence_refs=())
    result = verify_adapter_outcome(outcome=outcome, decision="ACCEPT", required_evidence=True)
    assert result.work_item_status == "FAILED"
    assert result.reason_code == "EVIDENCE_REQUIRED"

    ok = verify_adapter_outcome(
        outcome=AdapterOutcome(kind="SUCCEEDED", evidence_refs=("ev1",)),
        decision="ACCEPT",
    )
    assert ok.work_item_status == "VERIFIED"


def test_waiver_requires_id() -> None:
    with pytest.raises(ExecutionKernelRepositoryError):
        verify_adapter_outcome(
            outcome=AdapterOutcome(kind="SUCCEEDED", evidence_refs=("ev1",)),
            decision="WAIVE",
        )


def test_ex3_s004_waiver_requires_full_structured_fields() -> None:
    """AIX-EXECUTION-ARCHITECTURE.md §11.4: a waiver needs a rationale and a
    named approver, not just an opaque id. Each required field's absence
    must fail closed on its own."""
    base = dict(
        outcome=AdapterOutcome(kind="SUCCEEDED", evidence_refs=("ev1",)),
        decision="WAIVE",
        waiver_id="w1",
    )
    with pytest.raises(ExecutionKernelRepositoryError, match="waiver_rationale"):
        verify_adapter_outcome(**base)  # rationale missing

    with pytest.raises(ExecutionKernelRepositoryError, match="waiver_approver_actor_id"):
        verify_adapter_outcome(**base, waiver_rationale="needed for release window")

    with pytest.raises(ExecutionKernelRepositoryError, match="waiver_approver_role"):
        verify_adapter_outcome(
            **base,
            waiver_rationale="needed for release window",
            waiver_approver_actor_id="human-1",
        )


def test_ex3_s004_waiver_forbids_executor_self_waiver() -> None:
    """§11.4: 'never Executor self-waiver' — the approver role is a closed
    enum of {planner, human}; anything else, including literally passing
    'executor', must fail closed rather than being silently accepted."""
    with pytest.raises(ExecutionKernelRepositoryError, match="never Executor self-waiver"):
        verify_adapter_outcome(
            outcome=AdapterOutcome(kind="SUCCEEDED", evidence_refs=("ev1",)),
            decision="WAIVE",
            waiver_id="w1",
            waiver_rationale="trying to self-approve",
            waiver_approver_actor_id="executor-bot-1",
            waiver_approver_role="executor",  # type: ignore[arg-type]
        )


def test_ex3_s004_waiver_succeeds_with_full_structured_fields() -> None:
    result = verify_adapter_outcome(
        outcome=AdapterOutcome(kind="SUCCEEDED", evidence_refs=()),
        decision="WAIVE",
        waiver_id="w1",
        waiver_rationale="Evidence pipeline was down; manually verified output",
        waiver_approver_actor_id="human-42",
        waiver_approver_role="human",
        waiver_expires_at="2026-09-01T00:00:00Z",
    )
    assert result.work_item_status == "VERIFIED"
    assert result.waiver_rationale == "Evidence pipeline was down; manually verified output"
    assert result.waiver_approver_actor_id == "human-42"
    assert result.waiver_approver_role == "human"
    assert result.waiver_expires_at == "2026-09-01T00:00:00Z"


def test_distiller_noop_on_reject_without_lesson() -> None:
    assert (
        distill_knowledge_proposals(
            tenant_id="t1",
            work_item_id="wi1",
            problem_id="pr1",
            capability_id="cap1",
            evidence_refs=[],
            decision="REJECT",
            work_item_status="FAILED",
        )
        == []
    )


@pytest.mark.asyncio
async def test_kernel_verify_and_distill_roundtrip() -> None:
    svc = KernelService()
    for body in (
        ("project", "proj_1", 1, "PROJECT_CREATED", "c1", {"status": "ACTIVE", "vision_summary": "V", "requirements_summary": "R"}),
        ("capability", "cap_1", 1, "CAPABILITY_CREATED", "c2", {"status": "ACTIVE", "name": "C", "parent_id": "proj_1"}),
        ("problem", "pr_1", 1, "PROBLEM_CREATED", "c3", {"status": "EXECUTING", "parent_id": "cap_1", "charter_summary": "X"}),
        ("work_item", "wi_1", 1, "WI_CREATED", "c4", {"status": "IN_PROGRESS", "parent_id": "pr_1", "title": "T"}),
    ):
        await svc.append_and_project(
            tenant_id="t1",
            aggregate_type=body[0],
            aggregate_id=body[1],
            expected_sequence_number=body[2],
            event_type=body[3],
            command_id=body[4],
            payload=body[5],
        )

    knowledge = await svc.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_seed",
        knowledge_kind="architecture",
        content="Keep tenant fail-closed",
        command_id="k1",
        capability_id="cap_1",
    )
    assert knowledge["revision"] == 1

    capsule, dispatch = await svc.prepare_dispatch(tenant_id="t1", work_item_id="wi_1", max_tokens=2000)
    assert dispatch.capsule_content_sha256 == capsule.capsule_content_sha256
    assert any(k["knowledge_entry_id"] == "k_seed" for k in capsule.knowledge)

    verified = await svc.apply_verification(
        tenant_id="t1",
        work_item_id="wi_1",
        outcome=AdapterOutcome(kind="SUCCEEDED", evidence_refs=("ev_html",), cycle_id="cyc_1"),
        decision="ACCEPT",
        expected_sequence_number=2,
        command_id="verify1",
    )
    assert verified["verification"]["work_item_status"] == "VERIFIED"

    entries = await svc.distill_learning(
        tenant_id="t1",
        work_item_id="wi_1",
        decision="ACCEPT",
        lesson_summary="Prefer content-hash capsule identity",
        promote_pattern=True,
        command_id_prefix="d1",
    )
    kinds = {e["knowledge_kind"] for e in entries}
    assert "lesson_learned" in kinds
    assert "pattern" in kinds

    capsule2 = await svc.build_capsule(tenant_id="t1", work_item_id="wi_1", max_tokens=4000)
    assert capsule2.capsule_content_sha256 != capsule.capsule_content_sha256
