"""Tests for F9-P4 OpsBus Correlation Capability Adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest

from backend.aix_core.framework.integration.hyeve.adapters.opsbus import (
    OpsBusCapabilityAdapter,
)


@pytest.mark.asyncio
async def test_opsbus_adapter_log_event_with_correlation():
    mock_client = MagicMock()
    mock_client._post_run_log = AsyncMock(return_value=(True, None))

    adapter = OpsBusCapabilityAdapter(client=mock_client)
    ok, err = await adapter.log_event(
        tenant_id="tenant-1",
        project_id="proj-1",
        run_id="run-100",
        correlation_id="cid-555",
        event_name="execution.completed",
        payload={"verdict": "SUCCESS"},
    )
    assert ok is True
    assert err is None
    mock_client._post_run_log.assert_called_once()
    call_kwargs = mock_client._post_run_log.call_args.kwargs
    assert call_kwargs["correlation_id"] == "cid-555"
    assert call_kwargs["payload"]["event_name"] == "execution.completed"
    assert call_kwargs["payload"]["correlation_id"] == "cid-555"


@pytest.mark.asyncio
async def test_opsbus_adapter_auto_generates_correlation():
    mock_client = MagicMock()
    mock_client._post_run_log = AsyncMock(return_value=(True, None))

    adapter = OpsBusCapabilityAdapter(client=mock_client)
    ok, err = await adapter.log_event(
        tenant_id="tenant-1",
        project_id="proj-1",
        run_id="run-100",
        correlation_id=None,
        event_name="execution.started",
        payload={"step": 1},
    )
    assert ok is True
    assert err is None
    call_kwargs = mock_client._post_run_log.call_args.kwargs
    assert call_kwargs["correlation_id"].startswith("corr-")
