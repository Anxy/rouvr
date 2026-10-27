"""CodeIntel v2 API shapes (Wave 4 Agent1).

Frozen specs other Wave 4 agents build to — implement exactly these shapes:
- CodeIntelIndexJob[repo_id: str, commit_sha: str, status: queued|running|done|failed,
  error_class: str|None]
- CodeIntelSnapshot[repo_id: str, commit_sha: str, snapshot_id: str]
- Freshness{repo_id, commit_sha, snapshot_id, workspace_head,
  freshness: current|stale|unknown, reason}
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IndexJobStatus = Literal["queued", "running", "done", "failed"]
FreshnessState = Literal["current", "stale", "unknown"]


class CodeIntelIndexJob(BaseModel):
    """Frozen spec: index job for a repo at a commit."""

    model_config = ConfigDict(extra="forbid")

    repo_id: str = Field(..., min_length=1)
    commit_sha: str = Field(..., min_length=1)
    status: IndexJobStatus
    error_class: str | None = Field(default=None)


class CodeIntelSnapshot(BaseModel):
    """Frozen spec: content snapshot for a repo at a commit."""

    model_config = ConfigDict(extra="forbid")

    repo_id: str = Field(..., min_length=1)
    commit_sha: str = Field(..., min_length=1)
    snapshot_id: str = Field(..., min_length=1)


class Freshness(BaseModel):
    """Frozen spec: freshness of a snapshot against a workspace head."""

    model_config = ConfigDict(extra="forbid")

    repo_id: str = Field(..., min_length=1)
    commit_sha: str = Field(..., min_length=1)
    # Empty when freshness is unknown (no snapshot recorded yet).
    snapshot_id: str = Field(...)
    workspace_head: str = Field(..., min_length=1)
    freshness: FreshnessState
    reason: str = Field(..., min_length=1)


class RegisterRepoRequest(BaseModel):
    """POST /repos/register request body."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(..., min_length=1)
    project_id: str | None = Field(default=None, min_length=1)
    repo_url: str = Field(..., min_length=1)


class RegisterRepoResponse(BaseModel):
    """POST /repos/register response body (tenant-scoped id)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    project_id: str | None = Field(default=None)
    repo_url: str = Field(..., min_length=1)
    registered_at: datetime
    posture: str = Field(..., min_length=1)


def evaluate_freshness(
    *,
    repo_id: str,
    commit_sha: str,
    snapshot: CodeIntelSnapshot | None,
    workspace_head: str,
) -> Freshness:
    """Compare a snapshot against the workspace head (advisory only)."""
    if snapshot is None:
        return Freshness(
            repo_id=repo_id,
            commit_sha=commit_sha,
            snapshot_id="",
            workspace_head=workspace_head,
            freshness="unknown",
            reason="no snapshot recorded for repo/commit",
        )
    if snapshot.commit_sha == workspace_head:
        return Freshness(
            repo_id=repo_id,
            commit_sha=commit_sha,
            snapshot_id=snapshot.snapshot_id,
            workspace_head=workspace_head,
            freshness="current",
            reason="snapshot commit matches workspace head",
        )
    return Freshness(
        repo_id=repo_id,
        commit_sha=commit_sha,
        snapshot_id=snapshot.snapshot_id,
        workspace_head=workspace_head,
        freshness="stale",
        reason="workspace head advanced past snapshot commit",
    )


__all__ = [
    "CodeIntelIndexJob",
    "CodeIntelSnapshot",
    "Freshness",
    "FreshnessState",
    "IndexJobStatus",
    "RegisterRepoRequest",
    "RegisterRepoResponse",
    "evaluate_freshness",
]
