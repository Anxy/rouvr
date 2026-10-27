"""Tests for F9-P3 CodeIntel Capability Adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest

from backend.aix_core.framework.integration.hyeve.adapters.codeintel import (
    CodeIntelCapabilityAdapter,
)


@pytest.mark.asyncio
async def test_codeintel_adapter_context_and_symbols():
    mock_client = MagicMock()
    mock_client.get_context = AsyncMock(
        return_value=(
            {
                "entities": [
                    {"name": "AuthService", "kind": "class", "file_path": "backend/auth.py"},
                    {"name": "validate_token", "kind": "function", "file_path": "backend/auth.py"},
                ],
                "relationships": [],
            },
            None,
        )
    )

    adapter = CodeIntelCapabilityAdapter(client=mock_client)
    ctx, err = await adapter.get_code_context(
        tenant_id="tenant-1",
        project_id="proj-1",
        entity_query="AuthService",
    )
    assert err is None
    assert len(ctx["entities"]) == 2

    symbols, error = await adapter.query_symbols(
        tenant_id="tenant-1",
        project_id="proj-1",
        symbol_query="AuthService",
    )
    assert error is None
    assert len(symbols) == 2
    assert symbols[0]["name"] == "AuthService"


@pytest.mark.asyncio
async def test_codeintel_adapter_error_handling():
    mock_client = MagicMock()
    mock_client.get_context = AsyncMock(return_value=({}, "CODEINTEL_PROJECT_ID_REQUIRED"))

    adapter = CodeIntelCapabilityAdapter(client=mock_client)
    symbols, error = await adapter.query_symbols(
        tenant_id="tenant-1",
        project_id="unknown",
        symbol_query="AuthService",
    )
    assert error == "CODEINTEL_PROJECT_ID_REQUIRED"
    assert symbols == []
