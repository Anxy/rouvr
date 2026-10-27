"""Tests for F8-P2 WorkHub Read and Write Adapters."""

from __future__ import annotations

import pytest

from backend.aix_core.framework.integration.workhub.adapter import (
    TaskCloseoutWriter,
    TaskContextReader,
    TaskEvidenceWriter,
    WorkHubTaskContext,
)


class _MockWorkHubClient:
    async def get_task_by_number(self, *, project_id: str, task_number: int, tenant_id: str | None = None):
        return {
            "id": f"task-{task_number}",
            "task_number": task_number,
            "title": f"Feature #{task_number}",
            "description": "Task description",
            "status": "OPEN",
        }

    async def get_task_ai_context_markdown(self, *, project_id: str, task_number: int, tenant_id: str | None = None):
        return "# Briefing\n\nConventions and symbols."

    async def attach_evidence(self, *, project_id: str, task_number: int, evidence: dict, correlation_id: str, tenant_id: str | None = None):
        return {"status": "ATTACHED", "task_number": task_number, "correlation_id": correlation_id}

    async def mark_task_done(self, *, project_id: str, task_number: int, resolution_notes: str, correlation_id: str, tenant_id: str | None = None):
        return {"status": "DONE", "task_number": task_number, "correlation_id": correlation_id}


@pytest.mark.asyncio
async def test_task_context_reader():
    reader = TaskContextReader(client=_MockWorkHubClient())
    ctx = await reader.load_task_context(
        project_id="proj-1",
        task_number=42,
        correlation_id="cid-100",
    )
    assert ctx is not None
    assert ctx.task_number == 42
    assert ctx.title == "Feature #42"
    assert ctx.correlation_id == "cid-100"
    assert ctx.ai_context_markdown is not None


@pytest.mark.asyncio
async def test_task_evidence_writer():
    writer = TaskEvidenceWriter(client=_MockWorkHubClient())
    res = await writer.attach_evidence(
        project_id="proj-1",
        task_number=42,
        evidence_bundle={"diff": "--- a\n+++ b", "verdict": "PASSED"},
        correlation_id="cid-200",
    )
    assert res["status"] == "ATTACHED"
    assert res["correlation_id"] == "cid-200"


@pytest.mark.asyncio
async def test_task_closeout_writer_governance():
    writer = TaskCloseoutWriter(client=_MockWorkHubClient())

    # Denied by default (mark_done_flag=False)
    with pytest.raises(PermissionError, match="lifecycle state transition denied"):
        await writer.mark_task_done(
            project_id="proj-1",
            task_number=42,
            resolution_notes="Completed successfully",
            mark_done_flag=False,
        )

    # Allowed when mark_done_flag=True
    res = await writer.mark_task_done(
        project_id="proj-1",
        task_number=42,
        resolution_notes="Completed successfully",
        mark_done_flag=True,
        correlation_id="cid-300",
    )
    assert res["status"] == "DONE"
    assert res["correlation_id"] == "cid-300"
