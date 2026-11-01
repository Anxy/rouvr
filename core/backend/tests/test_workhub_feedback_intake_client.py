"""Retired-contract tests: every method returns the RETIRED tuple, no HTTP."""
from __future__ import annotations

import pytest

from backend.clients.workhub_feedback_intake_client import (
    RETIRED_ERROR,
    WorkhubFeedbackIntakeClient,
)


def _client(token="token"):
    return WorkhubFeedbackIntakeClient(
        base_url="http://workhub.local", timeout_seconds=5, service_token=token)


@pytest.mark.asyncio
async def test_create_feedback_intake_task_success() -> None:
    assert await _client().create_feedback_intake_task(
        tenant_id="default", project_id="proj-1", payload={}) == (None, RETIRED_ERROR)


@pytest.mark.asyncio
async def test_create_feedback_intake_task_requires_token() -> None:
    assert await _client(token=None).create_feedback_intake_task(
        tenant_id="default", project_id="proj-1", payload={}) == (None, RETIRED_ERROR)
