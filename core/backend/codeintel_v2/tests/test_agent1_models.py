"""Wave 4 Agent1 — Models + Repo Registration acceptance tests.

Run from hyeve-aix-service/:  pytest backend/codeintel_v2/tests/ -q
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from backend.codeintel_v2.registry import (
    DuplicateRepoError,
    count_repos,
    get_repo,
    register_repo,
    reset_codeintel_registry,
)
from backend.codeintel_v2.schemas import (
    CodeIntelIndexJob,
    CodeIntelSnapshot,
    Freshness,
    evaluate_freshness,
)
from backend.models.codeintel_v2_models import CodeIntelBase, CodeIntelRepo
from backend.routers.codeintel_v2_router import get_codeintel_posture, router


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_codeintel_registry()
    yield
    reset_codeintel_registry()


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


AUTH = {"Authorization": "Test ci10-header"}


def _register(client: TestClient, tenant: str, url: str, project: str = "proj-1"):
    return client.post(
        "/api/v1/codeintel/repos/register",
        json={"tenant_id": tenant, "project_id": project, "repo_url": url},
        headers=AUTH,
    )


# --- ACCEPT: register returns tenant-scoped id --------------------------------

def test_register_returns_tenant_scoped_id(client: TestClient):
    resp = _register(client, "tenant-a", "https://example.com/repo.git")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["repo_url"] == "https://example.com/repo.git"
    assert body["id"].startswith("tenant-a:")
    assert body["registered_at"]
    # Round-trip through tenant-scoped GET.
    got = client.get(
        f"/api/v1/codeintel/repos/{body['id']}",
        params={"tenant_id": "tenant-a"},
        headers=AUTH,
    )
    assert got.status_code == 200
    assert got.json()["id"] == body["id"]


def test_register_row_persisted_with_expected_columns():
    row = register_repo(tenant_id="t1", project_id="p1", repo_url="https://example.com/x.git")
    assert isinstance(row, CodeIntelRepo)
    assert row.tenant_id == "t1"
    assert row.project_id == "p1"
    assert row.repo_url == "https://example.com/x.git"
    assert row.registered_at is not None
    assert get_repo(repo_id=row.id, tenant_id="t1") is not None
    # Cross-tenant read is isolated.
    assert get_repo(repo_id=row.id, tenant_id="other") is None


# --- ACCEPT: duplicate tenant+url blocked --------------------------------------

def test_duplicate_tenant_url_rejected_no_duplicate_rows(client: TestClient):
    first = _register(client, "tenant-a", "https://example.com/dup.git")
    assert first.status_code == 201
    dup = _register(client, "tenant-a", "https://example.com/dup.git")
    assert dup.status_code == 409, dup.text
    assert dup.json()["detail"]["code"] == "CODEINTEL_REPO_DUPLICATE"
    assert count_repos(tenant_id="tenant-a") == 1


def test_duplicate_raises_at_registry_level():
    register_repo(tenant_id="t1", project_id=None, repo_url="https://example.com/d.git")
    with pytest.raises(DuplicateRepoError):
        register_repo(tenant_id="t1", project_id=None, repo_url="https://example.com/d.git")
    assert count_repos(tenant_id="t1") == 1


def test_same_url_different_tenant_allowed(client: TestClient):
    r1 = _register(client, "tenant-a", "https://example.com/shared.git")
    r2 = _register(client, "tenant-b", "https://example.com/shared.git")
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]
    assert count_repos() == 2


def test_unique_constraint_enforced_by_database():
    from backend.codeintel_v2.registry import _sessions

    register_repo(tenant_id="t9", project_id=None, repo_url="https://example.com/u.git")
    factory = _sessions()
    with factory() as session:
        session.add(
            CodeIntelRepo(
                id="t9:forced-duplicate",
                tenant_id="t9",
                project_id=None,
                repo_url="https://example.com/u.git",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


# --- Frozen specs other agents build to -----------------------------------------

def test_frozen_index_job_shape():
    for status in ("queued", "running", "done", "failed"):
        job = CodeIntelIndexJob(repo_id="r1", commit_sha="abc123", status=status)
        assert job.error_class is None
    failed = CodeIntelIndexJob(
        repo_id="r1", commit_sha="abc123", status="failed", error_class="CLONE_FAILED"
    )
    assert failed.error_class == "CLONE_FAILED"
    with pytest.raises(Exception):
        CodeIntelIndexJob(repo_id="r1", commit_sha="abc123", status="bogus")  # type: ignore[arg-type]


def test_frozen_snapshot_shape():
    snap = CodeIntelSnapshot(repo_id="r1", commit_sha="abc123", snapshot_id="snap-1")
    assert (snap.repo_id, snap.commit_sha, snap.snapshot_id) == ("r1", "abc123", "snap-1")


def test_frozen_freshness_shape_and_helper():
    snap = CodeIntelSnapshot(repo_id="r1", commit_sha="abc123", snapshot_id="snap-1")
    current = evaluate_freshness(
        repo_id="r1", commit_sha="abc123", snapshot=snap, workspace_head="abc123"
    )
    assert isinstance(current, Freshness)
    assert current.freshness == "current" and current.snapshot_id == "snap-1"
    stale = evaluate_freshness(
        repo_id="r1", commit_sha="abc123", snapshot=snap, workspace_head="def456"
    )
    assert stale.freshness == "stale" and stale.reason
    unknown = evaluate_freshness(
        repo_id="r1", commit_sha="abc123", snapshot=None, workspace_head="def456"
    )
    assert unknown.freshness == "unknown" and unknown.reason


def test_table_metadata_matches_contract():
    table = CodeIntelRepo.__table__
    assert table.name == "codeintel_repos"
    assert {c.name for c in table.columns} >= {"id", "tenant_id", "project_id", "repo_url", "registered_at"}
    assert any(
        c.name == "uq_codeintel_repos_tenant_url"
        for c in table.constraints
    )
    assert CodeIntelRepo.__tablename__ in CodeIntelBase.metadata.tables


def test_posture_reads_wave0_flag_default_off():
    assert get_codeintel_posture() in ("off", "advisory")
