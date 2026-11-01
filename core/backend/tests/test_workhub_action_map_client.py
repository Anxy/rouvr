"""Retired-contract tests: every method returns the RETIRED tuple, no HTTP."""
from __future__ import annotations

import pytest

from backend.clients.workhub_action_map_client import (
    RETIRED_ERROR,
    WorkhubActionMapClient,
)


def _client(token="token"):
    return WorkhubActionMapClient(
        base_url="http://workhub.local", timeout_seconds=5, service_token=token)


@pytest.mark.asyncio
async def test_get_active_action_map_returns_highest_version() -> None:
    assert await _client().get_active_action_map(
        tenant_id="default", project_id="proj-1") == (None, RETIRED_ERROR)


@pytest.mark.asyncio
async def test_get_active_action_map_version_not_found() -> None:
    assert await _client().get_active_action_map(
        tenant_id="default", project_id="proj-1", version="v9") == (None, RETIRED_ERROR)


@pytest.mark.asyncio
async def test_create_proposed_action_map_success() -> None:
    assert await _client().create_proposed_action_map(
        tenant_id="default", project_id="proj-1", payload={}) == (None, RETIRED_ERROR)


@pytest.mark.asyncio
async def test_create_proposed_action_map_requires_token() -> None:
    assert await _client(token=None).create_proposed_action_map(
        tenant_id="default", project_id="proj-1", payload={}) == (None, RETIRED_ERROR)


@pytest.mark.asyncio
async def test_worker_query_cache_hit_and_trace() -> None:
    assert await _client().get_action_map_for_worker(
        tenant_id="default", project_id="proj-1") == (
        None, RETIRED_ERROR, {"cache_hit": False})


@pytest.mark.asyncio
async def test_worker_query_pinned_version_uses_explicit_fetch() -> None:
    assert await _client().get_action_map_for_worker(
        tenant_id="default", project_id="proj-1", version="v2") == (
        None, RETIRED_ERROR, {"cache_hit": False})


@pytest.mark.asyncio
async def test_worker_query_tenant_safe_cache_keys() -> None:
    assert await _client().get_action_map_for_worker(
        tenant_id="tenant-b", project_id="proj-1") == (
        None, RETIRED_ERROR, {"cache_hit": False})
    assert _client().invalidate_cache() is None
