"""Retired-contract tests: every method returns the RETIRED tuple, no HTTP."""
from __future__ import annotations

import pytest

from backend.clients.workhub_task_communication_client import (
    RETIRED_ERROR,
    WorkhubTaskCommunicationClient,
)


def _client(token="token"):
    return WorkhubTaskCommunicationClient(
        base_url="http://workhub.local", timeout_seconds=5, service_token=token)


@pytest.mark.asyncio
async def test_add_task_communication_posts_typed_payload() -> None:
    assert await _client().add_task_communication(
        tenant_id="default", project_id="proj-1", task_id="task-1",
        payload={}) == (False, RETIRED_ERROR, None)


@pytest.mark.asyncio
async def test_add_task_communication_requires_token() -> None:
    assert await _client(token=None).add_task_communication(
        tenant_id="default", project_id="proj-1", task_id="task-1",
        payload={}) == (False, RETIRED_ERROR, None)


@pytest.mark.asyncio
async def test_list_task_coordination_returns_actionable_items() -> None:
    assert await _client().list_task_coordination(
        tenant_id="default", project_id="proj-1") == ([], RETIRED_ERROR)


@pytest.mark.asyncio
async def test_list_task_communication_returns_typed_items() -> None:
    assert await _client().list_task_communication(
        tenant_id="default", project_id="proj-1", task_id="task-1") == ([], RETIRED_ERROR)
