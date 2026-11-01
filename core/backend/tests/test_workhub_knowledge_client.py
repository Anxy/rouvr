"""Retired-contract tests: every method returns the RETIRED tuple, no HTTP."""
from __future__ import annotations

import pytest

from backend.clients.workhub_knowledge_client import (
    RETIRED_ERROR,
    WorkhubKnowledgeClient,
)


def _client(token="token"):
    return WorkhubKnowledgeClient(
        base_url="http://workhub.local", timeout_seconds=5, service_token=token)


@pytest.mark.asyncio
async def test_update_knowledge_success() -> None:
    assert await _client().update_knowledge(
        knowledge_id="k1", payload={}) == (False, RETIRED_ERROR)


@pytest.mark.asyncio
async def test_update_knowledge_requires_id() -> None:
    assert await _client().update_knowledge(
        knowledge_id="", payload={}) == (False, RETIRED_ERROR)


@pytest.mark.asyncio
async def test_update_knowledge_failure_returns_false() -> None:
    assert await _client(token=None).update_knowledge(
        knowledge_id="k1", payload={}) == (False, RETIRED_ERROR)
