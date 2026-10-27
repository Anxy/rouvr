"""Wave 4 Agent2 (Index Jobs) acceptance tests.

Runnable via ``pytest backend/codeintel_v2/tests/ -q`` from hyeve-aix-service/.
Covers the three ACCEPT criteria:
  1. job bound to commit_sha (missing => rejected),
  2. full lifecycle transitions recorded in order,
  3. invalid repo => classified failure (never an exception leak).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.codeintel_v2 import index_jobs
from backend.codeintel_v2.index_jobs import (
    ILLEGAL_TRANSITION,
    INVALID_COMMIT,
    INVALID_REPO,
    REPO_CHECK_FAILED,
    CodeIntelIndexJob,
    IllegalTransitionError,
    configure_repo_exists,
    create_job,
    fail,
    get_job_record,
    indexing_enabled,
    reset_store,
    transition,
)
from backend.codeintel_v2.registry import (
    register_repo,
    repo_id_exists,
    reset_codeintel_registry,
)
from backend.routers.codeintel_v2_router import reset_loop_state
from backend.routers.codeintel_v2_router import router as codeintel_v2_router


AUTH = {"Authorization": "Test ci10-header"}


@pytest.fixture(autouse=True)
def _clean_state():
    """Isolate registry + job/snapshot stores per test."""
    reset_codeintel_registry()
    reset_loop_state()
    yield
    reset_codeintel_registry()
    reset_loop_state()


def _lib_repo_id() -> str:
    """Known repo for lib-level tests (fixture checker is registry-backed)."""
    return _registered_repo_id("t", "https://example.com/lib.git")


def _registered_repo_id(tenant: str, url: str) -> str:
    """Register a tenant repo directly; returns its tenant-scoped id."""
    return register_repo(
        tenant_id=tenant, project_id=None, repo_url=url
    ).id


@pytest.fixture()
def advisory(monkeypatch: pytest.MonkeyPatch):
    """Advisory posture, isolated store, known-repo checker; restore after."""
    monkeypatch.setattr(index_jobs, "_codeintel_posture", lambda: "advisory")
    previous = index_jobs._repo_exists
    configure_repo_exists(lambda repo_id: repo_id_exists(repo_id=repo_id))
    reset_store()
    yield
    reset_store()
    configure_repo_exists(previous)


@pytest.fixture()
def client(advisory) -> TestClient:  # noqa: ANN001, ANN201
    app = FastAPI()
    app.include_router(codeintel_v2_router)
    return TestClient(app)


# --- frozen model spec -------------------------------------------------------


def test_model_has_exactly_four_fields() -> None:
    assert set(CodeIntelIndexJob.model_fields) == {
        "repo_id",
        "commit_sha",
        "status",
        "error_class",
    }


def test_model_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CodeIntelIndexJob(
            repo_id="r",
            commit_sha="abc",
            status="queued",
            error_class=None,
            job_id="x",  # type: ignore[call-arg]
        )


def test_model_rejects_bad_status() -> None:
    with pytest.raises(ValidationError):
        CodeIntelIndexJob(repo_id="r", commit_sha="abc", status="nope")


# --- creation ----------------------------------------------------------------


def test_create_job_is_queued_and_bound_to_commit(advisory) -> None:
    known = _lib_repo_id()
    record = create_job(known, "deadbeef")
    assert record.job.status == "queued"
    assert record.job.error_class is None
    assert record.job.repo_id == known
    assert record.job.commit_sha == "deadbeef"
    assert record.transitions == ["queued"]
    assert get_job_record(record.job_id) is record


# --- lifecycle ----------------------------------------------------------------


def test_full_lifecycle_to_done_recorded_in_order(advisory) -> None:
    record = create_job(_lib_repo_id(), "deadbeef")
    transition(record.job, "running", history=record.transitions)
    transition(record.job, "done", history=record.transitions)
    assert record.job.status == "done"
    assert record.transitions == ["queued", "running", "done"]


def test_lifecycle_to_failure_records_error_class(advisory) -> None:
    record = create_job(_lib_repo_id(), "deadbeef")
    transition(record.job, "running", history=record.transitions)
    fail(record.job, "INDEX_FAILED", history=record.transitions)
    assert record.job.status == "failed"
    assert record.job.error_class == "INDEX_FAILED"
    assert record.transitions == ["queued", "running", "failed"]


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        ("queued", "done"),
        ("queued", "failed"),
        ("queued", "queued"),
        ("running", "queued"),
        ("running", "running"),
        ("done", "running"),
        ("done", "done"),
        ("done", "failed"),
        ("failed", "running"),
        ("failed", "done"),
        ("failed", "failed"),
        ("queued", "bogus"),
    ],
)
def test_illegal_transitions_raise_classified(
    advisory, from_status: str, to_status: str
) -> None:
    job = CodeIntelIndexJob(
        repo_id="t:whatever",
        commit_sha="deadbeef",
        status=from_status,  # type: ignore[arg-type]
        error_class=None,
    )
    with pytest.raises(IllegalTransitionError) as excinfo:
        transition(job, to_status)
    assert excinfo.value.code == ILLEGAL_TRANSITION
    assert ILLEGAL_TRANSITION in str(excinfo.value)


def test_fail_requires_non_blank_error_class(advisory) -> None:
    record = create_job(_lib_repo_id(), "deadbeef")
    with pytest.raises(ValueError):
        fail(record.job, "  ")


def test_fail_on_terminal_job_raises_classified(advisory) -> None:
    record = create_job(_lib_repo_id(), "deadbeef")
    transition(record.job, "running", history=record.transitions)
    transition(record.job, "done", history=record.transitions)
    with pytest.raises(IllegalTransitionError) as excinfo:
        fail(record.job, "INDEX_FAILED")
    assert excinfo.value.code == ILLEGAL_TRANSITION


# --- invalid repo / commit: classified failures, never leaks ------------------


def test_invalid_repo_is_classified_failure(advisory) -> None:
    record = create_job("t:does-not-exist", "deadbeef")
    assert record.job.status == "failed"
    assert record.job.error_class == INVALID_REPO
    assert record.transitions == ["queued", "failed"]
    assert get_job_record(record.job_id) is record


def test_blank_repo_is_classified_failure(advisory) -> None:
    record = create_job("   ", "deadbeef")
    assert record.job.status == "failed"
    assert record.job.error_class == INVALID_REPO


def test_blank_commit_is_classified_failure(advisory) -> None:
    record = create_job(_lib_repo_id(), "   ")
    assert record.job.status == "failed"
    assert record.job.error_class == INVALID_COMMIT


def test_crashing_repo_checker_never_leaks(advisory) -> None:
    def _boom(repo_id: str) -> bool:
        raise RuntimeError("registry down")

    record = create_job(_lib_repo_id(), "deadbeef", repo_exists=_boom)
    assert record.job.status == "failed"
    assert record.job.error_class == REPO_CHECK_FAILED


def test_default_stub_is_fail_closed_documented() -> None:
    """Without wiring, the default stub knows no repo (merge lead wires it)."""
    reset_store()
    try:
        record = create_job("any-repo", "deadbeef")
        assert record.job.status == "failed"
        assert record.job.error_class == INVALID_REPO
    finally:
        reset_store()


# --- AIX_CODEINTEL flag -------------------------------------------------------


def test_indexing_enabled_only_in_advisory_posture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(index_jobs, "_codeintel_posture", lambda: "advisory")
    assert indexing_enabled() is True
    monkeypatch.setattr(index_jobs, "_codeintel_posture", lambda: "off")
    assert indexing_enabled() is False


# --- HTTP surface --------------------------------------------------------------


def test_post_rejects_missing_commit_sha(client: TestClient) -> None:
    response = client.post(
        "/api/v1/codeintel/index-jobs",
        json={"tenant_id": "t", "repo_id": "repo-ok"},
        headers=AUTH,
    )
    assert response.status_code == 422


def test_post_rejects_empty_commit_sha(client: TestClient) -> None:
    response = client.post(
        "/api/v1/codeintel/index-jobs",
        json={"tenant_id": "t", "repo_id": "repo-ok", "commit_sha": ""},
        headers=AUTH,
    )
    assert response.status_code == 422


def test_post_creates_queued_job_and_get_shows_it(client: TestClient) -> None:
    repo_id = _registered_repo_id("t", "https://example.com/r.git")
    created = client.post(
        "/api/v1/codeintel/index-jobs",
        json={"tenant_id": "t", "repo_id": repo_id, "commit_sha": "deadbeef"},
        headers=AUTH,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "queued"
    assert body["error_class"] is None
    assert body["commit_sha"] == "deadbeef"
    assert body["transitions"] == ["queued"]

    fetched = client.get(
        f"/api/v1/codeintel/index-jobs/{body['job_id']}",
        params={"tenant_id": "t"},
        headers=AUTH,
    )
    assert fetched.status_code == 200
    assert fetched.json() == body


def test_post_invalid_repo_returns_classified_failure(client: TestClient) -> None:
    created = client.post(
        "/api/v1/codeintel/index-jobs",
        json={
            "tenant_id": "t",
            "repo_id": "t:does-not-exist",
            "commit_sha": "deadbeef",
        },
        headers=AUTH,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "failed"
    assert body["error_class"] == INVALID_REPO
    assert body["transitions"] == ["queued", "failed"]


def test_post_refused_when_indexing_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(index_jobs, "_codeintel_posture", lambda: "off")
    reset_store()
    try:
        app = FastAPI()
        app.include_router(codeintel_v2_router)
        response = TestClient(app).post(
            "/api/v1/codeintel/index-jobs",
            json={
                "tenant_id": "t",
                "repo_id": "repo-ok",
                "commit_sha": "deadbeef",
            },
            headers=AUTH,
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "INDEXING_DISABLED"
    finally:
        reset_store()


def test_get_unknown_job_is_404(client: TestClient) -> None:
    response = client.get(
        "/api/v1/codeintel/index-jobs/idx-does-not-exist",
        params={"tenant_id": "t"},
        headers=AUTH,
    )
    assert response.status_code == 404


def test_lifecycle_visible_through_get_in_order(client: TestClient) -> None:
    repo_id = _registered_repo_id("t", "https://example.com/r.git")
    job_id = client.post(
        "/api/v1/codeintel/index-jobs",
        json={"tenant_id": "t", "repo_id": repo_id, "commit_sha": "deadbeef"},
        headers=AUTH,
    ).json()["job_id"]
    record = get_job_record(job_id)
    assert record is not None
    transition(record.job, "running", history=record.transitions)
    seen = client.get(
        f"/api/v1/codeintel/index-jobs/{job_id}",
        params={"tenant_id": "t"},
        headers=AUTH,
    ).json()["transitions"]
    assert seen == ["queued", "running"]
    fail(record.job, "INDEX_FAILED", history=record.transitions)
    final = client.get(
        f"/api/v1/codeintel/index-jobs/{job_id}",
        params={"tenant_id": "t"},
        headers=AUTH,
    ).json()
    assert final["status"] == "failed"
    assert final["error_class"] == "INDEX_FAILED"
    assert final["transitions"] == ["queued", "running", "failed"]
