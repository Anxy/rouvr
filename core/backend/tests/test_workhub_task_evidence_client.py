"""Retired-contract tests: every method returns the RETIRED tuple, no HTTP."""
from __future__ import annotations

import pytest

from backend.clients.workhub_task_evidence_client import (
    RETIRED_ERROR,
    WorkhubTaskEvidenceClient,
)


def _client(token="token"):
    return WorkhubTaskEvidenceClient(
        base_url="http://workhub.local", timeout_seconds=5, service_token=token)


@pytest.mark.asyncio
async def test_add_model_usage_evidence_success() -> None:
    result = await _client().add_model_usage_evidence(
        tenant_id="default", project_id="proj-1", task_id="task-1",
        correlation_id="corr-1", actor_id="user-1", usage_block={"tokens": 123})
    assert result == (False, RETIRED_ERROR, None)


@pytest.mark.asyncio
async def test_add_model_usage_evidence_token_missing_returns_false() -> None:
    result = await _client(token=None).add_model_usage_evidence(
        tenant_id="default", project_id="proj-1", task_id="task-1",
        correlation_id="corr-1", actor_id="user-1", usage_block={"tokens": 123})
    assert result == (False, RETIRED_ERROR, None)


@pytest.mark.asyncio
async def test_add_model_usage_evidence_prefers_service_token_over_override_token() -> None:
    result = await _client().add_model_usage_evidence(
        tenant_id="default", project_id="proj-1", task_id="task-1",
        correlation_id="corr-1", actor_id="user-1", usage_block={"tokens": 1},
        override_token="override")
    assert result == (False, RETIRED_ERROR, None)


@pytest.mark.asyncio
async def test_add_model_usage_evidence_rejects_override_when_service_token_missing() -> None:
    result = await _client(token=None).add_model_usage_evidence(
        tenant_id="default", project_id="proj-1", task_id="task-1",
        correlation_id="corr-1", actor_id="user-1", usage_block={"tokens": 1},
        override_token="override")
    assert result == (False, RETIRED_ERROR, None)


@pytest.mark.asyncio
async def test_add_model_usage_evidence_failure_returns_false() -> None:
    result = await _client().add_model_usage_evidence(
        tenant_id="default", project_id="proj-1", task_id="task-1",
        correlation_id="corr-1", actor_id="user-1", usage_block={})
    assert result == (False, RETIRED_ERROR, None)


@pytest.mark.asyncio
async def test_add_model_routing_decision_evidence_success() -> None:
    result = await _client().add_model_routing_decision_evidence(
        tenant_id="default", project_id="proj-1", task_id="task-1",
        correlation_id="corr-1", actor_id="user-1", decision_block={})
    assert result == (False, RETIRED_ERROR, None)


@pytest.mark.asyncio
async def test_add_redaction_report_evidence_success() -> None:
    result = await _client().add_redaction_report_evidence(
        tenant_id="default", project_id="proj-1", task_id="task-1",
        correlation_id="corr-1", actor_id="user-1", report_block={})
    assert result == (False, RETIRED_ERROR, None)


@pytest.mark.asyncio
async def test_add_codeintel_provenance_evidence_success() -> None:
    result = await _client().add_codeintel_provenance_evidence(
        tenant_id="default", project_id="proj-1", task_id="task-1",
        correlation_id="corr-1", actor_id="user-1", bundle={})
    assert result == (False, RETIRED_ERROR, None)


@pytest.mark.asyncio
async def test_add_codeintel_provenance_evidence_preserves_empty_bundle_shape() -> None:
    result = await _client().add_codeintel_provenance_evidence(
        tenant_id="default", project_id="proj-1", task_id="task-1",
        correlation_id="corr-1", actor_id="user-1", bundle=None)
    assert result == (False, RETIRED_ERROR, None)
