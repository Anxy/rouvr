"""CI10 loop-closure acceptance tests (AIX lane, read-only first, then wired).

End-to-end: register -> index job (queued, NOT INVALID_REPO) -> transition
running -> done (snapshot bound) -> snapshots list/get -> freshness
current/stale by construction -> citations query with provenance + pack.
Plus: 401 without auth, tenant isolation, illegal-transition 409.

Run from hyeve-aix-service/:  pytest backend/codeintel_v2/tests/ -q
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.codeintel_v2 import index_jobs
from backend.codeintel_v2.registry import register_repo, reset_codeintel_registry
from backend.hyeve_aix_service.config import settings
from backend.routers.codeintel_v2_router import reset_loop_state
from backend.routers.codeintel_v2_router import router as codeintel_v2_router

AUTH = {"Authorization": "Test ci10-header"}
COMMIT = "deadbeef" * 5  # 40-hex style sha


@pytest.fixture(autouse=True)
def _clean():
    reset_codeintel_registry()
    reset_loop_state()
    yield
    reset_codeintel_registry()
    reset_loop_state()


@pytest.fixture()
def advisory(monkeypatch: pytest.MonkeyPatch):
    """Advisory posture on both posture readers (router + jobs + packs)."""
    monkeypatch.setattr(settings, "AIX_CODEINTEL", "advisory")
    monkeypatch.setattr(index_jobs, "_codeintel_posture", lambda: "advisory")
    yield


@pytest.fixture()
def client(advisory) -> TestClient:
    app = FastAPI()
    app.include_router(codeintel_v2_router)
    return TestClient(app)


def _register(client: TestClient, tenant: str, url: str) -> dict:
    resp = client.post(
        "/api/v1/codeintel/repos/register",
        json={"tenant_id": tenant, "project_id": None, "repo_url": url},
        headers=AUTH,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _repo_id(tenant: str, url: str) -> str:
    return register_repo(tenant_id=tenant, project_id=None, repo_url=url).id


# 1. register returns a tenant-scoped id ---------------------------------------


def test_register_201_tenant_scoped(client: TestClient) -> None:
    body = _register(client, "tenant-a", "https://example.com/loop.git")
    assert body["id"].startswith("tenant-a:")
    assert body["tenant_id"] == "tenant-a"


# 2. missing auth is 401 everywhere --------------------------------------------


def test_missing_auth_401_on_all_routes(client: TestClient) -> None:
    repo_id = _repo_id("t", "https://example.com/r.git")
    # Valid bodies, no Authorization header: every route must 401.
    assert (
        client.post(
            "/api/v1/codeintel/repos/register",
            json={
                "tenant_id": "t",
                "project_id": None,
                "repo_url": "https://example.com/r.git",
            },
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/codeintel/index-jobs",
            json={"tenant_id": "t", "repo_id": repo_id, "commit_sha": COMMIT},
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/api/v1/codeintel/index-jobs/idx-x", params={"tenant_id": "t"}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/api/v1/codeintel/snapshots",
            params={"repo_id": repo_id, "tenant_id": "t"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/codeintel/citations/query",
            json={
                "tenant_id": "t",
                "repo_id": repo_id,
                "commit_sha": COMMIT,
                "query": "x",
                "workspace_root": "/tmp",
            },
        ).status_code
        == 401
    )


# 3. real repo -> queued, NOT INVALID_REPO (was failing pre-CI10) --------------


def test_index_job_real_repo_queued_not_invalid(client: TestClient) -> None:
    repo_id = _repo_id("t", "https://example.com/r.git")
    created = client.post(
        "/api/v1/codeintel/index-jobs",
        json={"tenant_id": "t", "repo_id": repo_id, "commit_sha": COMMIT},
        headers=AUTH,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "queued"
    assert body["error_class"] is None
    assert body["transitions"] == ["queued"]


# 4. tenant isolation on jobs ---------------------------------------------------


def test_tenant_isolation_jobs(client: TestClient) -> None:
    repo_a = _repo_id("tenant-a", "https://example.com/r.git")
    created = client.post(
        "/api/v1/codeintel/index-jobs",
        json={"tenant_id": "tenant-a", "repo_id": repo_a, "commit_sha": COMMIT},
        headers=AUTH,
    )
    job_id = created.json()["job_id"]
    # Cross-tenant create: classified failure, still 201, no leak.
    foreign = client.post(
        "/api/v1/codeintel/index-jobs",
        json={"tenant_id": "tenant-b", "repo_id": repo_a, "commit_sha": COMMIT},
        headers=AUTH,
    )
    assert foreign.status_code == 201
    assert foreign.json()["status"] == "failed"
    assert foreign.json()["error_class"] == "INVALID_REPO"
    # Cross-tenant read: same 404 as unknown.
    assert (
        client.get(
            f"/api/v1/codeintel/index-jobs/{job_id}",
            params={"tenant_id": "tenant-b"},
            headers=AUTH,
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/codeintel/index-jobs/{job_id}",
            params={"tenant_id": "tenant-a"},
            headers=AUTH,
        ).status_code
        == 200
    )


# 5. running -> done binds a snapshot -------------------------------------------


def test_transition_running_done_creates_snapshot(client: TestClient) -> None:
    repo_id = _repo_id("t", "https://example.com/r.git")
    job_id = client.post(
        "/api/v1/codeintel/index-jobs",
        json={"tenant_id": "t", "repo_id": repo_id, "commit_sha": COMMIT},
        headers=AUTH,
    ).json()["job_id"]
    running = client.post(
        f"/api/v1/codeintel/index-jobs/{job_id}/transition",
        json={"tenant_id": "t", "to_status": "running"},
        headers=AUTH,
    )
    assert running.status_code == 200
    assert running.json()["status"] == "running"
    assert running.json()["snapshot_id"] is None
    done = client.post(
        f"/api/v1/codeintel/index-jobs/{job_id}/transition",
        json={"tenant_id": "t", "to_status": "done"},
        headers=AUTH,
    )
    assert done.status_code == 200
    body = done.json()
    assert body["status"] == "done"
    assert body["transitions"] == ["queued", "running", "done"]
    assert body["snapshot_id"]


# 6. snapshots list + get round-trip --------------------------------------------


def _job_done(client: TestClient, tenant: str, repo_id: str, commit: str) -> dict:
    job_id = client.post(
        "/api/v1/codeintel/index-jobs",
        json={"tenant_id": tenant, "repo_id": repo_id, "commit_sha": commit},
        headers=AUTH,
    ).json()["job_id"]
    client.post(
        f"/api/v1/codeintel/index-jobs/{job_id}/transition",
        json={"tenant_id": tenant, "to_status": "running"},
        headers=AUTH,
    )
    done = client.post(
        f"/api/v1/codeintel/index-jobs/{job_id}/transition",
        json={"tenant_id": tenant, "to_status": "done"},
        headers=AUTH,
    )
    assert done.status_code == 200
    return done.json()


def test_snapshots_list_and_get_roundtrip(client: TestClient) -> None:
    repo_id = _repo_id("t", "https://example.com/r.git")
    done = _job_done(client, "t", repo_id, COMMIT)
    listed = client.get(
        "/api/v1/codeintel/snapshots",
        params={"repo_id": repo_id, "tenant_id": "t", "workspace_head": COMMIT},
        headers=AUTH,
    )
    assert listed.status_code == 200
    snaps = listed.json()["snapshots"]
    assert len(snaps) == 1
    assert snaps[0]["snapshot_id"] == done["snapshot_id"]
    assert snaps[0]["freshness"] == "current"
    one = client.get(
        f"/api/v1/codeintel/snapshots/{done['snapshot_id']}",
        params={"tenant_id": "t", "workspace_head": COMMIT},
        headers=AUTH,
    )
    assert one.status_code == 200
    assert one.json()["commit_sha"] == COMMIT
    # Cross-tenant snapshot read is 404.
    assert (
        client.get(
            f"/api/v1/codeintel/snapshots/{done['snapshot_id']}",
            params={"tenant_id": "intruder"},
            headers=AUTH,
        ).status_code
        == 404
    )


# 7. freshness current vs stale by construction ---------------------------------


def test_freshness_current_vs_stale_by_construction(client: TestClient) -> None:
    repo_id = _repo_id("t", "https://example.com/r.git")
    _job_done(client, "t", repo_id, COMMIT)
    base = {"repo_id": repo_id, "tenant_id": "t", "commit_sha": COMMIT}
    current = client.get(
        "/api/v1/codeintel/freshness",
        params={**base, "workspace_head": COMMIT},
        headers=AUTH,
    )
    assert current.status_code == 200
    assert current.json()["freshness"] == "current"
    stale = client.get(
        "/api/v1/codeintel/freshness",
        params={**base, "workspace_head": "cafef00d" * 5},
        headers=AUTH,
    )
    assert stale.status_code == 200
    assert stale.json()["freshness"] == "stale"
    unknown = client.get(
        "/api/v1/codeintel/freshness",
        params={**base, "commit_sha": "ab12cd34", "workspace_head": COMMIT},
        headers=AUTH,
    )
    assert unknown.json()["freshness"] == "unknown"


# 8. citations query: provenance + pack + candidate-only -------------------------


def test_citations_query_provenance_and_pack(
    client: TestClient, tmp_path
) -> None:
    target = tmp_path / "registry.py"
    target.write_text("x = 1\n")
    repo_id = _repo_id("t", "https://example.com/r.git")
    done = _job_done(client, "t", repo_id, COMMIT)
    resp = client.post(
        "/api/v1/codeintel/citations/query",
        json={
            "tenant_id": "t",
            "repo_id": repo_id,
            "commit_sha": COMMIT,
            "query": "registry",
            "workspace_root": str(tmp_path),
            "workspace_head": COMMIT,
        },
        headers=AUTH,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["snapshot_id"] == done["snapshot_id"]
    assert body["freshness"] == "current"
    assert body["advisory_degraded"] is False
    assert body["citations"], "expected at least one citation"
    for citation in body["citations"]:
        assert citation["path"]
        assert citation["line_range"] == [1, 1]
        assert citation["snapshot_id"] == done["snapshot_id"]
        assert citation["commit_sha"] == COMMIT
        assert 0.0 <= citation["confidence"] <= 1.0
        assert citation["freshness"]["freshness"] == "current"
    # Candidate-only still holds: nothing is ever selected.
    assert all(item["selected"] is False for item in body["candidates"])
    assert "selected" not in str(body["pack"])
    pack = body["pack"]
    assert pack["freshness"]["freshness"] == "current"
    assert pack["truncated"] is False


# 9. illegal transition is 409 ---------------------------------------------------


def test_illegal_transition_409(client: TestClient) -> None:
    repo_id = _repo_id("t", "https://example.com/r.git")
    job_id = client.post(
        "/api/v1/codeintel/index-jobs",
        json={"tenant_id": "t", "repo_id": repo_id, "commit_sha": COMMIT},
        headers=AUTH,
    ).json()["job_id"]
    resp = client.post(
        f"/api/v1/codeintel/index-jobs/{job_id}/transition",
        json={"tenant_id": "t", "to_status": "done"},
        headers=AUTH,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "ILLEGAL_TRANSITION"


# 10. full loop end to end --------------------------------------------------------


def test_end_to_end_loop(client: TestClient, tmp_path) -> None:
    (tmp_path / "snapshots.py").write_text("y = 2\n")
    reg = _register(client, "tenant-e2e", "https://example.com/e2e.git")
    repo_id = reg["id"]
    created = client.post(
        "/api/v1/codeintel/index-jobs",
        json={"tenant_id": "tenant-e2e", "repo_id": repo_id, "commit_sha": COMMIT},
        headers=AUTH,
    )
    assert created.json()["status"] == "queued"
    done = _job_done(client, "tenant-e2e", repo_id, COMMIT)
    listed = client.get(
        "/api/v1/codeintel/snapshots",
        params={
            "repo_id": repo_id,
            "tenant_id": "tenant-e2e",
            "workspace_head": COMMIT,
        },
        headers=AUTH,
    ).json()["snapshots"]
    assert [s["freshness"] for s in listed] == ["current"]
    query = client.post(
        "/api/v1/codeintel/citations/query",
        json={
            "tenant_id": "tenant-e2e",
            "repo_id": repo_id,
            "commit_sha": COMMIT,
            "query": "snapshots",
            "workspace_root": str(tmp_path),
            "workspace_head": COMMIT,
        },
        headers=AUTH,
    )
    assert query.status_code == 200
    assert query.json()["pack"]["freshness"]["freshness"] == "current"
    assert query.json()["snapshot_id"] == done["snapshot_id"]
