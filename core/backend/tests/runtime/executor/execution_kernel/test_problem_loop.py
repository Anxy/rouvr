"""EX2-S009: Problem-loop orchestration tests."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.aix_core.runtime.executor.execution_kernel_adapter.adapters import AdapterOutcome
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.routers.execution_kernel_router import router as execution_kernel_router

REPO_STATE = {
    "repository_id": "aix",
    "branch": "main",
    "base_commit_sha": "abc1234",
    "current_commit_sha": "abc1234",
    "working_tree_diff_reference": "",
    "untracked_artifact_references": [],
    "environment_manifest_reference": "",
}


async def _seed_two_items(svc: KernelService) -> None:
    for body in (
        ("project", "proj_1", 1, "PROJECT_CREATED", "c1", {"status": "ACTIVE", "vision_summary": "V", "requirements_summary": "R"}),
        ("capability", "cap_1", 1, "CAPABILITY_CREATED", "c2", {"status": "ACTIVE", "name": "C", "parent_id": "proj_1"}),
        ("problem", "pr_1", 1, "PROBLEM_CREATED", "c3", {"status": "EXECUTING", "parent_id": "cap_1", "charter_summary": "Ship"}),
        ("work_item", "wi_a", 1, "WI_CREATED", "c4", {"status": "PENDING", "parent_id": "pr_1", "title": "Schema"}),
        ("work_item", "wi_b", 1, "WI_CREATED", "c5", {"status": "PENDING", "parent_id": "pr_1", "title": "API"}),
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
    await svc.add_dependency(
        tenant_id="t1", work_item_id="wi_b", depends_on_work_item_id="wi_a", command_id="dep1"
    )


@pytest.mark.asyncio
async def test_tick_dry_verifies_then_unlocks_dependent() -> None:
    svc = KernelService()
    await _seed_two_items(svc)

    first = await svc.run_problem_step(
        tenant_id="t1",
        actor_id="actor-1",
        problem_id="pr_1",
        mode="tick_dry",
        repository_state=REPO_STATE,
        idempotency_key="step-1",
        lesson_summary="Schema before API",
        promote_pattern=False,
    )
    assert first.work_item_id == "wi_a"
    assert first.status == "VERIFIED"
    assert first.checkpoint is not None
    assert first.capsule_content_sha256
    assert first.distilled
    assert first.next_actionable == ["wi_b"]

    second = await svc.run_problem_step(
        tenant_id="t1",
        actor_id="actor-1",
        problem_id="pr_1",
        mode="tick_dry",
        repository_state=REPO_STATE,
        idempotency_key="step-2",
    )
    assert second.work_item_id == "wi_b"
    assert second.status == "PROBLEM_IN_REVIEW"
    assert second.problem_ready_for_review is True
    assert second.problem_status == "REVIEW"
    assert any(t["status"] == "REVIEW" for t in second.problem_transitions)
    assert second.next_actionable == []

    accepted = await svc.run_problem_step(
        tenant_id="t1",
        actor_id="actor-1",
        problem_id="pr_1",
        mode="accept_problem",
        repository_state=REPO_STATE,
        idempotency_key="step-accept",
    )
    assert accepted.status == "PROBLEM_VERIFIED"
    assert accepted.problem_status == "VERIFIED"
    problem = await svc.get_projection(tenant_id="t1", aggregate_type="problem", aggregate_id="pr_1")
    assert problem is not None
    assert problem["status"] == "VERIFIED"


@pytest.mark.asyncio
async def test_tick_live_then_complete() -> None:
    svc = KernelService()
    await _seed_two_items(svc)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess")

    live = await svc.run_problem_step(
        tenant_id="t1",
        actor_id="actor-1",
        problem_id="pr_1",
        mode="tick_live",
        repository_state=REPO_STATE,
        idempotency_key="live-1",
        cycle_backend=backend,
        workhub_task_id="wh-loop-1",
        repository_id="aix",
        candidate_sha="abc1234",
        repository={"repository_id": "aix", "current_commit_sha": "abc1234"},
    )
    assert live.status == "AWAITING_APPROVAL"
    assert live.live is not None
    cycle_id = live.live["handle"]["cycle_id"]

    done = await svc.run_problem_step(
        tenant_id="t1",
        actor_id="actor-1",
        problem_id="pr_1",
        mode="complete",
        repository_state=REPO_STATE,
        idempotency_key="complete-1",
        work_item_id="wi_a",
        outcome=AdapterOutcome(kind="SUCCEEDED", evidence_refs=("ev1",), cycle_id=cycle_id),
        decision="ACCEPT",
        lesson_summary="Approved after cycle",
    )
    assert done.verification is not None
    assert done.verification["work_item_status"] == "VERIFIED"
    assert done.next_actionable == ["wi_b"]


@pytest.mark.asyncio
async def test_problem_step_api_tick_dry() -> None:
    app = FastAPI()
    app.state.execution_kernel_service = KernelService()
    app.state.execution_kernel_require_gateway = False
    app.include_router(execution_kernel_router)
    svc = app.state.execution_kernel_service
    await _seed_two_items(svc)

    transport = ASGITransport(app=app)
    headers = {"X-Tenant-ID": "t1", "Idempotency-Key": "api-step-1"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/aix/execution-kernel/problems/pr_1/step",
            headers=headers,
            json={
                "mode": "tick_dry",
                "repository_state": REPO_STATE,
                "lesson_summary": "From API",
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()["step"]
        assert body["work_item_id"] == "wi_a"
        assert body["status"] == "VERIFIED"
        assert body["checkpoint"]["status"] == "CAPTURED"
