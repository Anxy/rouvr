"""CodeIntel v2 repo registration router (Wave 4 Agent1).

Advisory-only: registers repo metadata for indexing. Retrieval posture is
governed by the Wave 0 ``AIX_CODEINTEL`` flag (off|advisory|on, default off);
``on`` is illegal in v2 MVP and this router fails closed on it.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, status

from backend.codeintel_v2.registry import (
    DuplicateRepoError,
    get_repo,
    register_repo,
    repo_id_exists,
)
from backend.codeintel_v2.schemas import RegisterRepoRequest, RegisterRepoResponse
from backend.codeintel_v2 import index_jobs
from backend.codeintel_v2.index_jobs import (
    INDEX_FAILED,
    INDEXING_DISABLED,
    INVALID_REPO,
    CodeIntelIndexJob,
)
from backend.codeintel_v2.snapshots import InMemorySnapshotStore
from pydantic import BaseModel, ConfigDict, Field

try:
    from backend.hyeve_aix_service.config import settings as _settings

    _SETTINGS_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - config must import in service
    _settings = None  # type: ignore[assignment]
    _SETTINGS_IMPORT_ERROR = exc

router = APIRouter(
    prefix="/api/v1/codeintel",
    tags=["codeintel v2"],
    responses={404: {"description": "Not found"}},
)


def get_codeintel_posture() -> str:
    """Read the Wave 0 authority-freeze posture (default off when unreadable)."""
    if _settings is None:
        return "off"
    try:
        return str(getattr(_settings, "AIX_CODEINTEL", "off") or "off").strip().lower() or "off"
    except Exception:
        return "off"


def _require_legal_posture(posture: str) -> None:
    if posture == "on":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "CODEINTEL_POSTURE_ILLEGAL",
                "message": "AIX_CODEINTEL='on' is illegal in v2 MVP (Wave 0 authority freeze)",
            },
        )


def _require_authorization(authorization: str | None) -> str:
    """Inbound auth for /api/v1/codeintel/* (AIX lane presence-check pattern).

    Mirrors ``_require_auth_header`` in the AIX integration routers: a missing
    or blank Authorization header is a 401 and learns nothing about posture.
    Token validation stays upstream at the gateway; the tenant signal travels
    explicitly per route and is always re-checked against the registry.
    """
    if not authorization or not authorization.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "CODEINTEL_AUTH_REQUIRED",
                "message": "Authorization header required",
            },
        )
    return authorization


def _registry_contains_repo_id(repo_id: str) -> bool:
    """Prod repo-existence check: CI10 wiring for the index-job stub."""
    return repo_id_exists(repo_id=repo_id)


# CI10 loop-closure: wire the real Agent1 registry check into the index-job
# intake path, so INVALID_REPO fires only for truly missing repos.
index_jobs.configure_repo_exists(_registry_contains_repo_id)


def _resolve_tenant_repo(*, repo_id: str, tenant_id: str):
    """Return the repo row when ``repo_id`` belongs to ``tenant_id``.

    Raises 404 with the same code for absent and foreign-tenant ids so
    tenants cannot probe each other's repos.
    """
    row = get_repo(repo_id=repo_id, tenant_id=tenant_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CODEINTEL_REPO_NOT_FOUND", "message": "Repo not found"},
        )
    return row


_SNAPSHOT_STORE = InMemorySnapshotStore()


def reset_loop_state() -> None:
    """Clear CI10 runtime state (jobs + snapshots; tests / ephemeral use)."""
    index_jobs.reset_store()
    _SNAPSHOT_STORE.clear()


@router.post(
    "/repos/register",
    response_model=RegisterRepoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a repo for CodeIntel indexing",
    description="Tenant-scoped repo registration. Duplicate tenant+url is rejected with 409.",
    responses={409: {"description": "Repo already registered for tenant"}},
)
def register_codeintel_repo(
    data: RegisterRepoRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> RegisterRepoResponse:
    """Register a repo and return its tenant-scoped id."""
    _require_authorization(authorization)
    posture = get_codeintel_posture()
    _require_legal_posture(posture)
    try:
        row = register_repo(
            tenant_id=data.tenant_id,
            project_id=data.project_id,
            repo_url=data.repo_url,
        )
    except DuplicateRepoError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CODEINTEL_REPO_DUPLICATE",
                "message": str(exc),
                "tenant_id": exc.tenant_id,
                "repo_url": exc.repo_url,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "CODEINTEL_REPO_INVALID", "message": str(exc)},
        ) from exc
    return RegisterRepoResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        repo_url=row.repo_url,
        registered_at=row.registered_at,
        posture=posture,
    )


@router.get(
    "/repos/{repo_id}",
    response_model=RegisterRepoResponse,
    summary="Get a registered repo (tenant-scoped)",
)
def get_codeintel_repo(
    repo_id: str,
    tenant_id: str = Query(..., min_length=1, description="Owning tenant"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> RegisterRepoResponse:
    """Fetch one repo; 404 when absent or owned by another tenant."""
    _require_authorization(authorization)
    posture = get_codeintel_posture()
    _require_legal_posture(posture)
    row = _resolve_tenant_repo(repo_id=repo_id, tenant_id=tenant_id)
    return RegisterRepoResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        repo_url=row.repo_url,
        registered_at=row.registered_at,
        posture=posture,
    )


class CreateIndexJobRequest(BaseModel):
    """POST /index-jobs body. tenant_id + commit_sha are required, non-empty."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(
        min_length=1, description="Owning tenant; repo must belong to it."
    )
    repo_id: str = Field(description="Repository id the index job is bound to.")
    commit_sha: str = Field(
        min_length=1, description="Exact commit the index job is bound to."
    )


class IndexJobResponse(BaseModel):
    """GET / POST response: job state plus ordered transition history."""

    job_id: str
    repo_id: str
    commit_sha: str
    status: str
    error_class: str | None
    transitions: list[str]


def _to_response(record: index_jobs.JobRecord) -> IndexJobResponse:
    job: CodeIntelIndexJob = record.job
    return IndexJobResponse(
        job_id=record.job_id,
        repo_id=job.repo_id,
        commit_sha=job.commit_sha,
        status=job.status,
        error_class=job.error_class,
        transitions=list(record.transitions),
    )


@router.post("/index-jobs", status_code=201, response_model=IndexJobResponse)
def create_index_job(
    body: CreateIndexJobRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> IndexJobResponse:
    """Create an index job bound to (tenant, repo_id, commit_sha).

    Empty commit_sha is rejected by request validation (422). Repos that are
    absent or owned by another tenant do not leak: they yield a stored failed
    job carrying ``INVALID_REPO`` (still 201). When AIX_CODEINTEL is ``off``,
    no indexing work is accepted (409 INDEXING_DISABLED).
    """
    _require_authorization(authorization)
    _require_legal_posture(get_codeintel_posture())
    if not index_jobs.indexing_enabled():
        raise HTTPException(
            status_code=409,
            detail={
                "code": INDEXING_DISABLED,
                "message": "Indexing work is disabled (AIX_CODEINTEL is off).",
            },
        )
    if get_repo(repo_id=body.repo_id, tenant_id=body.tenant_id) is None:
        failed = index_jobs.create_job("", body.commit_sha)
        failed.job.repo_id = body.repo_id
        failed.job.error_class = INVALID_REPO
        return _to_response(failed)
    record = index_jobs.create_job(body.repo_id, body.commit_sha)
    return _to_response(record)


def _resolve_tenant_job(*, job_id: str, tenant_id: str) -> index_jobs.JobRecord:
    """Return a job record only when its repo belongs to ``tenant_id``.

    Unknown ids and foreign-tenant jobs share one 404 code (no probing).
    """
    record = index_jobs.get_job_record(job_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "INDEX_JOB_NOT_FOUND",
                "message": f"Unknown index job {job_id!r}.",
            },
        )
    if get_repo(repo_id=record.job.repo_id, tenant_id=tenant_id) is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "INDEX_JOB_NOT_FOUND",
                "message": f"Unknown index job {job_id!r}.",
            },
        )
    return record


@router.get("/index-jobs/{job_id}", response_model=IndexJobResponse)
def get_index_job(
    job_id: str,
    tenant_id: str = Query(..., min_length=1, description="Owning tenant"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> IndexJobResponse:
    """Return a job's current status plus its recorded transitions in order."""
    _require_authorization(authorization)
    _require_legal_posture(get_codeintel_posture())
    return _to_response(_resolve_tenant_job(job_id=job_id, tenant_id=tenant_id))


class TransitionIndexJobRequest(BaseModel):
    """POST /index-jobs/{id}/transition body."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(
        min_length=1, description="Owning tenant; job repo must belong to it."
    )
    to_status: Literal["running", "done", "failed"] = Field(
        description="Target state along queued -> running -> done|failed."
    )
    error_class: str | None = Field(
        default=None,
        description="Classified code when failing; defaults to INDEX_FAILED.",
    )


class TransitionIndexJobResponse(IndexJobResponse):
    """Job state after a transition, plus the bound snapshot when done."""

    snapshot_id: str | None = None


@router.post(
    "/index-jobs/{job_id}/transition",
    response_model=TransitionIndexJobResponse,
    summary="Advance an index job along queued -> running -> done|failed",
)
def transition_index_job(
    job_id: str,
    body: TransitionIndexJobRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> TransitionIndexJobResponse:
    """Move a job forward; reaching ``done`` binds a snapshot to the commit.

    Illegal edges are 409 ILLEGAL_TRANSITION. A ``done`` job creates (or
    reuses) the ``(repo_id, commit_sha)`` snapshot, closing the job→snapshot
    linkage the CI10 loop depends on.
    """
    _require_authorization(authorization)
    _require_legal_posture(get_codeintel_posture())
    record = _resolve_tenant_job(job_id=job_id, tenant_id=body.tenant_id)
    snapshot_id: str | None = None
    try:
        if body.to_status == "failed":
            index_jobs.fail(
                record.job,
                body.error_class or INDEX_FAILED,
                history=record.transitions,
            )
        else:
            index_jobs.transition(
                record.job, body.to_status, history=record.transitions
            )
    except index_jobs.IllegalTransitionError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INDEX_TRANSITION_INVALID", "message": str(exc)},
        ) from exc
    if record.job.status == "done":
        snapshot = _SNAPSHOT_STORE.get_or_create(
            record.job.repo_id, record.job.commit_sha
        )
        snapshot_id = snapshot.snapshot_id
    base = _to_response(record)
    return TransitionIndexJobResponse(**base.model_dump(), snapshot_id=snapshot_id)


class SnapshotResponse(BaseModel):
    """One commit-bound snapshot label."""

    model_config = ConfigDict(extra="forbid")

    repo_id: str
    commit_sha: str
    snapshot_id: str


class SnapshotWithFreshness(SnapshotResponse):
    """Snapshot plus its freshness against the supplied workspace head."""

    freshness: str
    freshness_reason: str
    workspace_head: str | None = None


def _freshness_for(repo_id: str, snapshot: Any, workspace_head: str) -> dict[str, Any]:
    from backend.codeintel_v2.freshness import check_freshness

    record = check_freshness(snapshot, workspace_head or "")
    return {
        "freshness": record.freshness,
        "freshness_reason": record.reason,
        "workspace_head": record.workspace_head,
    }


@router.get(
    "/snapshots/{snapshot_id}",
    response_model=SnapshotWithFreshness,
    summary="Get one snapshot by id (tenant-scoped via its repo)",
)
def get_snapshot(
    snapshot_id: str,
    tenant_id: str = Query(..., min_length=1, description="Owning tenant"),
    workspace_head: str = Query(
        default="", description="Workspace HEAD to classify freshness against"
    ),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> SnapshotWithFreshness:
    """Fetch a snapshot; 404 when unknown or owned by another tenant."""
    _require_authorization(authorization)
    _require_legal_posture(get_codeintel_posture())
    snapshot = _SNAPSHOT_STORE.get_by_id(snapshot_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SNAPSHOT_NOT_FOUND", "message": "Snapshot not found"},
        )
    _resolve_tenant_repo(repo_id=snapshot.repo_id, tenant_id=tenant_id)
    return SnapshotWithFreshness(
        repo_id=snapshot.repo_id,
        commit_sha=snapshot.commit_sha,
        snapshot_id=snapshot.snapshot_id,
        **_freshness_for(snapshot.repo_id, snapshot, workspace_head),
    )


@router.get(
    "/snapshots",
    summary="List snapshots bound to a repo (tenant-scoped)",
)
def list_snapshots(
    repo_id: str = Query(..., min_length=1, description="Repository id"),
    tenant_id: str = Query(..., min_length=1, description="Owning tenant"),
    workspace_head: str = Query(
        default="", description="Workspace HEAD to classify freshness against"
    ),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    """List a repo's snapshots, each labelled current|stale|unknown."""
    _require_authorization(authorization)
    _require_legal_posture(get_codeintel_posture())
    _resolve_tenant_repo(repo_id=repo_id, tenant_id=tenant_id)
    items = []
    for snapshot in _SNAPSHOT_STORE.list_for_repo(repo_id):
        items.append(
            {
                "repo_id": snapshot.repo_id,
                "commit_sha": snapshot.commit_sha,
                "snapshot_id": snapshot.snapshot_id,
                **_freshness_for(repo_id, snapshot, workspace_head),
            }
        )
    return {"repo_id": repo_id, "snapshots": items}


@router.get(
    "/freshness",
    summary="Classify a repo snapshot against a workspace HEAD",
)
def get_freshness(
    repo_id: str = Query(..., min_length=1, description="Repository id"),
    tenant_id: str = Query(..., min_length=1, description="Owning tenant"),
    commit_sha: str = Query(..., min_length=1, description="Snapshot commit"),
    workspace_head: str = Query(
        default="", description="Workspace HEAD to compare against"
    ),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    """HEAD == snapshot commit -> current; mismatch -> stale; none -> unknown."""
    _require_authorization(authorization)
    _require_legal_posture(get_codeintel_posture())
    _resolve_tenant_repo(repo_id=repo_id, tenant_id=tenant_id)
    snapshot = _SNAPSHOT_STORE.get(repo_id, commit_sha)
    from backend.codeintel_v2.freshness import check_freshness

    record = check_freshness(snapshot, workspace_head or "")
    return record.model_dump()


class CitationsQueryRequest(BaseModel):
    """POST /citations/query body: candidate-only file search + pack."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, description="Owning tenant.")
    repo_id: str = Field(description="Repository id to query.")
    commit_sha: str = Field(min_length=1, description="Snapshot commit to query.")
    query: str = Field(min_length=1, description="Filename text to match.")
    workspace_root: str = Field(
        min_length=1, description="Explicit directory to walk."
    )
    workspace_head: str = Field(
        default="", description="Workspace HEAD for freshness classification."
    )
    limit: int = Field(default=20, ge=1, le=50, description="Max citations.")


@router.post(
    "/citations/query",
    summary="Candidate-only file search returning citations + context pack",
)
def query_citations(
    body: CitationsQueryRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    """Search files for one snapshot and bundle citations into a context pack.

    Candidate-only: every candidate carries ``selected=False``; ambiguity is
    rejected (return all, select none). Pack freshness is the worst of its
    members; ``advisory_degraded`` is true exactly when stale or unknown.
    """
    from backend.codeintel_v2.citations import Citation, citation_to_dict
    from backend.codeintel_v2.context_packs import build_pack
    from backend.codeintel_v2.file_candidates import search_files
    from backend.codeintel_v2.freshness import check_freshness

    _require_authorization(authorization)
    _require_legal_posture(get_codeintel_posture())
    if not index_jobs.indexing_enabled():
        raise HTTPException(
            status_code=409,
            detail={
                "code": INDEXING_DISABLED,
                "message": "CodeIntel reads are disabled (AIX_CODEINTEL is off).",
            },
        )
    _resolve_tenant_repo(repo_id=body.repo_id, tenant_id=body.tenant_id)
    snapshot = _SNAPSHOT_STORE.get(body.repo_id, body.commit_sha)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SNAPSHOT_NOT_FOUND", "message": "Snapshot not found"},
        )
    freshness = check_freshness(snapshot, body.workspace_head or "")
    freshness_payload = {
        "repo_id": body.repo_id,
        "commit_sha": body.commit_sha,
        "snapshot_id": snapshot.snapshot_id,
        "workspace_head": freshness.workspace_head or "",
        "freshness": freshness.freshness,
        "reason": freshness.reason,
    }
    candidates = search_files(body.query, body.workspace_root)[: body.limit]
    citations = [
        Citation(
            path=item["path"],
            line_range=(1, 1),
            snapshot_id=snapshot.snapshot_id,
            commit_sha=body.commit_sha,
            confidence=item["confidence"],
            source="file_candidates",
            freshness=freshness_payload,
        )
        for item in candidates
    ]
    pack = build_pack(citations, snapshot)
    return {
        "repo_id": body.repo_id,
        "commit_sha": body.commit_sha,
        "snapshot_id": snapshot.snapshot_id,
        "freshness": freshness.freshness,
        "freshness_reason": freshness.reason,
        "advisory_degraded": pack.advisory_degraded,
        "candidates": candidates,
        "citations": [citation_to_dict(item) for item in citations],
        "pack": pack.to_dict(),
    }


__all__ = ["_SNAPSHOT_STORE", "get_codeintel_posture", "reset_loop_state", "router"]
