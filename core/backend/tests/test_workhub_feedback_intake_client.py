from __future__ import annotations

import pytest
import respx
from httpx import Response

from backend.clients.workhub_feedback_intake_client import WorkhubFeedbackIntakeClient


@pytest.mark.asyncio
@respx.mock
async def test_create_feedback_intake_task_success() -> None:
    client = WorkhubFeedbackIntakeClient(base_url="http://workhub.local", timeout_seconds=5, service_token="token")
    route = respx.post("http://workhub.local/api/v1/projects/proj-1/tasks/batch").mock(
        return_value=Response(
            200,
            json={
                "data": {
                    "results": [
                        {
                            "success": True,
                            "data": {"id": "task-1", "task_number": 900},
                        }
                    ]
                }
            },
        )
    )
    data, err = await client.create_feedback_intake_task(
        tenant_id="default",
        project_id="proj-1",
        correlation_id="corr-1",
        title="[Feedback] Search issue",
        description="search misses expected records",
        priority="high",
        metadata_json={"kind": "pilot_feedback_intake"},
        tags_json=["pilot-feedback"],
    )
    assert route.called
    assert err is None
    assert data is not None
    assert data["task_number"] == 900
    request = route.calls.last.request
    assert request.headers.get("X-Tenant-ID") == "default"
    assert request.headers.get("X-Project-ID") == "proj-1"
    assert request.headers.get("X-Correlation-ID") == "corr-1"


@pytest.mark.asyncio
async def test_create_feedback_intake_task_requires_token() -> None:
    client = WorkhubFeedbackIntakeClient(base_url="http://workhub.local", timeout_seconds=5, service_token=None)
    data, err = await client.create_feedback_intake_task(
        tenant_id="default",
        project_id="proj-1",
        correlation_id="corr-1",
        title="[Feedback] Search issue",
        description="search misses expected records",
        priority="high",
        metadata_json={"kind": "pilot_feedback_intake"},
        tags_json=["pilot-feedback"],
    )
    assert data is None
    assert err == "WORKHUB_API_TOKEN_MISSING"
