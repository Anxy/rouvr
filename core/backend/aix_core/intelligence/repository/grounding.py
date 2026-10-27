"""Commit-addressed CodeIntel grounding for the governed objective cycle."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

GROUNDING_POLICY_VERSION = "aix.objective-cycle.codeintel-grounding.v1"


class GroundingDisposition(StrEnum):
    GROUNDED = "GROUNDED"
    BLOCKED = "BLOCKED"


class GroundingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    commit_sha: str = Field(min_length=7)
    query: str = Field(min_length=1)
    limit: int = Field(default=8, ge=1, le=20)


class GroundedCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_id: str
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    snapshot_id: str
    commit_sha: str
    confidence: float = Field(ge=0.0, le=1.0)


class GroundingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = GROUNDING_POLICY_VERSION
    disposition: GroundingDisposition
    reason_codes: tuple[str, ...] = Field(min_length=1)
    tenant_id: str
    project_id: str
    repository_id: str
    commit_sha: str
    query_fingerprint: str | None = None
    citations: tuple[GroundedCitation, ...] = ()
    context: dict[str, Any] = Field(default_factory=dict)


class CodeIntelProvider(Protocol):
    async def get_context(
        self,
        *,
        tenant_id: str,
        project_id: str,
        entity_query: str | None = None,
        limit: int = 8,
        retrieval_policy: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str | None]: ...


def _text(value: Any) -> str:
    return str(value or "").strip()


class CodeIntelGroundingAdapter:
    """Require CodeIntel proof for the exact requested repository commit."""

    def __init__(self, provider: CodeIntelProvider) -> None:
        self.provider = provider

    async def ground(self, request: GroundingRequest) -> GroundingResult:
        context, error = await self.provider.get_context(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            entity_query=request.query,
            limit=request.limit,
            retrieval_policy={
                "mode": "commit_addressed",
                "repo_id": request.repository_id,
                "required_commit_sha": request.commit_sha,
            },
        )
        if error:
            return self._blocked(request, "CODEINTEL_UNAVAILABLE")
        if not isinstance(context, dict) or not context:
            return self._blocked(request, "CODEINTEL_EMPTY_CONTEXT")
        if _text(context.get("tenant_id")) != request.tenant_id:
            return self._blocked(request, "CODEINTEL_TENANT_MISMATCH")
        if _text(context.get("project_id")) != request.project_id:
            return self._blocked(request, "CODEINTEL_PROJECT_MISMATCH")

        raw_citations = context.get("citations")
        if not isinstance(raw_citations, list) or not raw_citations:
            return self._blocked(request, "CODEINTEL_CITATIONS_REQUIRED")

        citations: list[GroundedCitation] = []
        for raw in raw_citations:
            if not isinstance(raw, dict):
                return self._blocked(request, "CODEINTEL_CITATION_INVALID")
            repository_id = _text(raw.get("repo_id") or context.get("primary_repo_id"))
            commit_sha = _text(raw.get("commit_sha"))
            snapshot_id = _text(raw.get("snapshot_id") or raw.get("snapshot"))
            file_path = _text(raw.get("file_path") or raw.get("file"))
            freshness = _text(raw.get("freshness_status")).lower()
            if repository_id != request.repository_id:
                return self._blocked(request, "CODEINTEL_REPOSITORY_MISMATCH")
            if commit_sha != request.commit_sha:
                return self._blocked(request, "CODEINTEL_STALE_COMMIT")
            if freshness not in {"fresh", "current"}:
                return self._blocked(request, "CODEINTEL_STALE_CONTEXT")
            if not snapshot_id or not file_path:
                return self._blocked(request, "CODEINTEL_PROVENANCE_INCOMPLETE")
            start_line = raw.get("start_line", raw.get("line"))
            end_line = raw.get("end_line", start_line)
            citations.append(
                GroundedCitation(
                    repository_id=repository_id,
                    file_path=file_path,
                    start_line=int(start_line) if start_line is not None else None,
                    end_line=int(end_line) if end_line is not None else None,
                    snapshot_id=snapshot_id,
                    commit_sha=commit_sha,
                    confidence=float(raw.get("confidence", raw.get("confidence_score", 0.0)) or 0.0),
                )
            )

        query_identity = context.get("query_identity")
        query_fingerprint = (
            _text(query_identity.get("query_fingerprint"))
            if isinstance(query_identity, dict)
            else None
        )
        return GroundingResult(
            disposition=GroundingDisposition.GROUNDED,
            reason_codes=("COMMIT_ADDRESSED_CODEINTEL_CONTEXT",),
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            repository_id=request.repository_id,
            commit_sha=request.commit_sha,
            query_fingerprint=query_fingerprint or None,
            citations=tuple(citations),
            context=context,
        )

    @staticmethod
    def _blocked(request: GroundingRequest, reason: str) -> GroundingResult:
        return GroundingResult(
            disposition=GroundingDisposition.BLOCKED,
            reason_codes=(reason,),
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            repository_id=request.repository_id,
            commit_sha=request.commit_sha,
        )
