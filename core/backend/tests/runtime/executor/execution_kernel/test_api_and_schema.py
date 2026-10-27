"""EX2-S005: schema constants + kernel API surface tests."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.infrastructure.postgres.execution_kernel.schema import (
    CHECKPOINT_TABLE,
    DEPENDENCY_EVENTS_TABLE,
    EXECUTION_EVENTS_TABLE,
    KNOWLEDGE_TABLE,
    PROJECTION_TABLE,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService
from backend.routers.execution_kernel_router import router as execution_kernel_router


def test_schema_table_names() -> None:
    assert EXECUTION_EVENTS_TABLE == "aix_execution_events"
    assert PROJECTION_TABLE == "aix_execution_projection"
    assert DEPENDENCY_EVENTS_TABLE == "aix_execution_dependency_events"
    assert CHECKPOINT_TABLE == "aix_execution_checkpoints"
    assert KNOWLEDGE_TABLE == "aix_execution_knowledge"


@pytest.mark.asyncio
async def test_kernel_api_append_projection_resume_capsule() -> None:
    app = FastAPI()
    app.state.execution_kernel_service = KernelService()
    app.state.execution_kernel_require_gateway = False
    app.include_router(execution_kernel_router)
    transport = ASGITransport(app=app)
    headers = {"X-Tenant-ID": "t1"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/aix/execution-kernel/health")
        assert health.status_code == 200
        assert health.json()["mode"] == "inprocess"

        missing = await client.post(
            "/aix/execution-kernel/events",
            json={
                "aggregate_type": "project",
                "aggregate_id": "p1",
                "expected_sequence_number": 1,
                "event_type": "PROJECT_CREATED",
                "command_id": "cmd1",
                "payload": {"status": "ACTIVE", "vision_summary": "V", "requirements_summary": "R"},
            },
        )
        assert missing.status_code == 400

        for body in (
            {
                "aggregate_type": "project",
                "aggregate_id": "proj_1",
                "expected_sequence_number": 1,
                "event_type": "PROJECT_CREATED",
                "command_id": "cmd_p",
                "payload": {"status": "ACTIVE", "vision_summary": "Ship", "requirements_summary": "Inv"},
            },
            {
                "aggregate_type": "capability",
                "aggregate_id": "cap_1",
                "expected_sequence_number": 1,
                "event_type": "CAPABILITY_CREATED",
                "command_id": "cmd_c",
                "payload": {"status": "ACTIVE", "name": "Inv", "parent_id": "proj_1"},
            },
            {
                "aggregate_type": "problem",
                "aggregate_id": "pr_1",
                "expected_sequence_number": 1,
                "event_type": "PROBLEM_CREATED",
                "command_id": "cmd_pr",
                "payload": {"status": "EXECUTING", "parent_id": "cap_1", "charter_summary": "Reg"},
            },
            {
                "aggregate_type": "work_item",
                "aggregate_id": "wi_1",
                "expected_sequence_number": 1,
                "event_type": "WI_CREATED",
                "command_id": "cmd_w",
                "payload": {"status": "PENDING", "parent_id": "pr_1", "title": "Build"},
            },
        ):
            res = await client.post("/aix/execution-kernel/events", headers=headers, json=body)
            assert res.status_code == 200, res.text
            assert res.json()["ok"] is True

        proj = await client.get("/aix/execution-kernel/projection/work_item/wi_1", headers=headers)
        assert proj.status_code == 200
        assert proj.json()["item"]["status"] == "PENDING"

        nxt = await client.get("/aix/execution-kernel/problems/pr_1/next-actionable", headers=headers)
        assert nxt.status_code == 200
        assert nxt.json()["work_item_ids"] == ["wi_1"]

        cp = await client.post(
            "/aix/execution-kernel/checkpoints",
            headers=headers,
            json={
                "problem_id": "pr_1",
                "repository_state": {
                    "repository_id": "repo",
                    "branch": "main",
                    "base_commit_sha": "abc1234",
                    "current_commit_sha": "abc1234",
                    "working_tree_diff_reference": "",
                    "untracked_artifact_references": [],
                    "environment_manifest_reference": "",
                },
            },
        )
        assert cp.status_code == 200
        assert cp.json()["checkpoint"]["status"] == "CAPTURED"

        resume = await client.post("/aix/execution-kernel/problems/pr_1/resume", headers=headers)
        assert resume.status_code == 200
        assert resume.json()["resume"]["source"] == "checkpoint"

        capsule = await client.post(
            "/aix/execution-kernel/capsules",
            headers=headers,
            json={"work_item_id": "wi_1", "max_tokens": 2000},
        )
        assert capsule.status_code == 200
        body = capsule.json()["capsule"]
        assert body["schema_version"] == "aix.execution-context-capsule.v1"
        assert len(body["capsule_content_sha256"]) == 64


@pytest.mark.asyncio
async def test_ex3_s002_proposal_inbox_endpoint() -> None:
    """EX3-S002: the GET /problems/{id}/proposals inbox endpoint returns
    open proposals and reflects resolution; also confirms the EX3-S001
    escalation fields (requests_new_capability/charter_change) are
    reachable through the actual API, not just the service layer."""
    app = FastAPI()
    app.state.execution_kernel_service = KernelService()
    app.state.execution_kernel_require_gateway = False
    app.include_router(execution_kernel_router)
    transport = ASGITransport(app=app)
    headers = {"X-Tenant-ID": "t1"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        boot = await client.post(
            "/aix/execution-kernel/bootstrap",
            headers=headers,
            json={
                "vision_summary": "V",
                "requirements_summary": "R",
                "capability_name": "Inventory",
                "charter_summary": "X",
                "work_items": [{"title": "Only"}],
                "problem_id": "pr_inbox",
                "idempotency_key": "boot-inbox",
            },
        )
        assert boot.status_code == 200, boot.text

        empty = await client.get("/aix/execution-kernel/problems/pr_inbox/proposals", headers=headers)
        assert empty.status_code == 200
        assert empty.json()["proposals"] == []

        raised = await client.post(
            "/aix/execution-kernel/problems/pr_inbox/proposals",
            headers=headers,
            json={
                "reason": "Inventory needs Finance",
                "level": 2,
                "affected_capabilities": ["cap_finance_other"],
                "command_id": "prop-esc",
            },
        )
        assert raised.status_code == 200, raised.text
        assert raised.json()["proposal"]["level"] == 3  # escalated by EX3-S001's guard, via the API

        raised4 = await client.post(
            "/aix/execution-kernel/problems/pr_inbox/proposals",
            headers=headers,
            json={
                "reason": "Need a new Device Registry capability",
                "level": 2,
                "requests_new_capability": True,
                "command_id": "prop-esc4",
            },
        )
        assert raised4.status_code == 200, raised4.text
        assert raised4.json()["proposal"]["level"] == 4

        inbox = await client.get("/aix/execution-kernel/problems/pr_inbox/proposals", headers=headers)
        assert inbox.status_code == 200
        proposal_ids = {p["proposal_id"] for p in inbox.json()["proposals"]}
        assert proposal_ids == {
            raised.json()["proposal"]["proposal_id"],
            raised4.json()["proposal"]["proposal_id"],
        }

        resolved = await client.post(
            f"/aix/execution-kernel/proposals/{raised.json()['proposal']['proposal_id']}/resolve",
            headers=headers,
            json={"decision": "RESOLVED", "command_id": "resolve-esc"},
        )
        assert resolved.status_code == 200, resolved.text

        after = await client.get("/aix/execution-kernel/problems/pr_inbox/proposals", headers=headers)
        remaining_ids = {p["proposal_id"] for p in after.json()["proposals"]}
        assert remaining_ids == {raised4.json()["proposal"]["proposal_id"]}
