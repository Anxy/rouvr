from __future__ import annotations

import pytest

from backend.aix_core.intelligence.repository.grounding import (
    CodeIntelGroundingAdapter,
    GroundingDisposition,
    GroundingRequest,
)

SHA = "a" * 40


class FakeCodeIntel:
    def __init__(self, context: dict, error: str | None = None) -> None:
        self.context = context
        self.error = error
        self.calls: list[dict] = []

    async def get_context(self, **kwargs):
        self.calls.append(kwargs)
        return self.context, self.error


def _request() -> GroundingRequest:
    return GroundingRequest(
        tenant_id="tenant-1",
        project_id="project-1",
        repository_id="aix",
        commit_sha=SHA,
        query="TaskClassPolicy.evaluate",
    )


def _context() -> dict:
    return {
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "primary_repo_id": "aix",
        "query_identity": {"query_fingerprint": "fp-1"},
        "citations": [
            {
                "repo_id": "aix",
                "file_path": "backend/policy.py",
                "start_line": 10,
                "end_line": 20,
                "snapshot_id": "snapshot-1",
                "commit_sha": SHA,
                "freshness_status": "fresh",
                "confidence": 0.95,
            }
        ],
    }


@pytest.mark.asyncio
async def test_grounding_requires_exact_commit_addressed_codeintel_context() -> None:
    provider = FakeCodeIntel(_context())
    result = await CodeIntelGroundingAdapter(provider).ground(_request())

    assert result.disposition is GroundingDisposition.GROUNDED
    assert result.commit_sha == SHA
    assert result.citations[0].snapshot_id == "snapshot-1"
    assert provider.calls[0]["retrieval_policy"]["required_commit_sha"] == SHA
    assert provider.calls[0]["retrieval_policy"]["repo_id"] == "aix"


@pytest.mark.asyncio
async def test_grounding_fails_closed_when_codeintel_is_unavailable() -> None:
    result = await CodeIntelGroundingAdapter(FakeCodeIntel({}, "connection refused")).ground(_request())

    assert result.disposition is GroundingDisposition.BLOCKED
    assert result.reason_codes == ("CODEINTEL_UNAVAILABLE",)
    assert result.context == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("tenant_id", "other-tenant", "CODEINTEL_TENANT_MISMATCH"),
        ("project_id", "other-project", "CODEINTEL_PROJECT_MISMATCH"),
    ],
)
async def test_grounding_rejects_identity_mismatch(field: str, value: str, reason: str) -> None:
    context = _context()
    context[field] = value
    result = await CodeIntelGroundingAdapter(FakeCodeIntel(context)).ground(_request())

    assert result.reason_codes == (reason,)


@pytest.mark.asyncio
async def test_grounding_rejects_cross_repository_citation() -> None:
    context = _context()
    context["citations"][0]["repo_id"] = "other"
    result = await CodeIntelGroundingAdapter(FakeCodeIntel(context)).ground(_request())

    assert result.reason_codes == ("CODEINTEL_REPOSITORY_MISMATCH",)


@pytest.mark.asyncio
async def test_grounding_rejects_stale_commit() -> None:
    context = _context()
    context["citations"][0]["commit_sha"] = "b" * 40
    result = await CodeIntelGroundingAdapter(FakeCodeIntel(context)).ground(_request())

    assert result.reason_codes == ("CODEINTEL_STALE_COMMIT",)


@pytest.mark.asyncio
async def test_grounding_rejects_stale_freshness_status() -> None:
    context = _context()
    context["citations"][0]["freshness_status"] = "stale"
    result = await CodeIntelGroundingAdapter(FakeCodeIntel(context)).ground(_request())

    assert result.reason_codes == ("CODEINTEL_STALE_CONTEXT",)


@pytest.mark.asyncio
async def test_grounding_rejects_missing_freshness_proof() -> None:
    context = _context()
    context["citations"][0].pop("freshness_status")
    result = await CodeIntelGroundingAdapter(FakeCodeIntel(context)).ground(_request())

    assert result.reason_codes == ("CODEINTEL_STALE_CONTEXT",)


@pytest.mark.asyncio
async def test_grounding_rejects_missing_provenance() -> None:
    context = _context()
    context["citations"][0]["snapshot_id"] = None
    result = await CodeIntelGroundingAdapter(FakeCodeIntel(context)).ground(_request())

    assert result.reason_codes == ("CODEINTEL_PROVENANCE_INCOMPLETE",)


@pytest.mark.asyncio
async def test_grounding_rejects_context_without_citations() -> None:
    context = _context()
    context["citations"] = []
    result = await CodeIntelGroundingAdapter(FakeCodeIntel(context)).ground(_request())

    assert result.reason_codes == ("CODEINTEL_CITATIONS_REQUIRED",)
