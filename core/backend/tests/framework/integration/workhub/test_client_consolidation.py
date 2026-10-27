"""Tests for F8-P3 WorkHub Client Consolidation."""

from __future__ import annotations

import pytest

from backend.aix_core.framework.integration.workhub.client import (
    WorkHubTaskClient,
    workhub_task_client,
)
from backend.aix_orchestrator_v2.workhub_client import (
    WorkHubTaskClient as ShimWorkHubTaskClient,
)


def test_workhub_task_client_identity_and_shim_equivalence():
    assert WorkHubTaskClient is ShimWorkHubTaskClient
    client = WorkHubTaskClient(base_url="http://localhost:8000", service_token="jwt.token")
    assert client.base_url == "http://localhost:8000"
    assert client.service_token == "jwt.token"


def test_workhub_task_client_headers():
    client = WorkHubTaskClient(base_url="http://localhost:8000", service_token="token123")
    headers = client._headers(
        project_id="proj-1",
        tenant_id="tenant-1",
        correlation_id="cid-123",
        idempotency_key="idemp-456",
    )
    assert headers["X-Project-ID"] == "proj-1"
    assert headers["X-Correlation-ID"] == "cid-123"
    assert headers["Idempotency-Key"] == "idemp-456"
