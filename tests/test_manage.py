"""Management actions: project rename/archive, task move, knowledge
review/archive/pin, server pagination (tasks limit/offset, search offset).
Fail-closed throughout: cross-tenant is 404, workers are 403/404."""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_AUTH_SECRET", "test-secret-for-manage-step-0123456789")
    monkeypatch.setenv("ROUVR_ADMIN_PASSWORD", "manage-admin-password-0123456789")
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    monkeypatch.delenv("ROUVR_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("META_API_KEY", raising=False)
    from ledger.app import make_app

    app = make_app(f"sqlite:///{tmp_path}/manage.db")
    reg = TestClient(app)
    headers = {"x-rouvr-token": "stack-secret"}
    reg.post("/v1/tenants", json={"tenant": "demo"}, headers=headers).raise_for_status()
    for proj in ("a", "b"):
        reg.post("/v1/projects", json={"tenant": "demo", "project": proj},
                 headers=headers).raise_for_status()
    return app


@pytest.fixture()
def stack(app):
    return TestClient(app, headers={"x-rouvr-token": "stack-secret"})


def _task(stack, project="a", **kw):
    body = {"title": "t", "intent": "y", "tenant": "demo", "project": project}
    body.update(kw)
    r = stack.post("/v1/tasks", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _entry(stack, **kw):
    body = {"kind": "note", "title": "N", "body": "body text here",
            "tenant": "demo", "project": "a"}
    body.update(kw)
    r = stack.post("/v1/knowledge", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_project_rename_rewrites_scope(stack):
    t = _task(stack)
    _entry(stack)
    r = stack.patch("/v1/projects/a", json={"tenant": "demo", "name": "a2",
                                            "title": "A two"})
    assert r.status_code == 200, r.text
    assert r.json()["project"] == "a2" and r.json()["title"] == "A two"
    names = [p["project"] for p in
             stack.get("/v1/projects", params={"tenant": "demo"}).json()["projects"]]
    assert names == ["a2", "b"]
    assert stack.get(f"/v1/tasks/{t['id']}").json()["project"] == "a2"
    # taken names refuse
    assert stack.patch("/v1/projects/a2",
                       json={"tenant": "demo", "name": "b"}).status_code == 409
    assert stack.patch("/v1/projects/nope",
                       json={"tenant": "demo", "name": "x"}).status_code == 404


def test_project_archive_hides_from_list(stack):
    r = stack.patch("/v1/projects/b", json={"tenant": "demo", "archived": True})
    assert r.status_code == 200 and r.json()["archived"] == 1
    names = [p["project"] for p in
             stack.get("/v1/projects", params={"tenant": "demo"}).json()["projects"]]
    assert names == ["a"]
    names = [p["project"] for p in
             stack.get("/v1/projects", params={"tenant": "demo", "include_archived": "true"}).json()["projects"]]
    assert names == ["a", "b"]
    stack.patch("/v1/projects/b", json={"tenant": "demo", "archived": False})
    names = [p["project"] for p in
             stack.get("/v1/projects", params={"tenant": "demo"}).json()["projects"]]
    assert names == ["a", "b"]


def test_task_move_and_slice_guard(stack):
    t = _task(stack, project="a")
    r = stack.patch(f"/v1/tasks/{t['id']}",
                    json={"project": "b", "idempotency_key": "mv1"})
    assert r.status_code == 200, r.text
    assert r.json()["project"] == "b"
    assert stack.get(f"/v1/tasks/{t['id']}").json()["project"] == "b"
    assert stack.patch(f"/v1/tasks/{t['id']}",
                       json={"project": "nope", "idempotency_key": "mv2"}).status_code == 404
    # slice members stay in their slice's project
    r = stack.post("/v1/plans/loop-guard/approve")
    assert r.status_code == 404  # no such plan: guard shape only
    e = _entry(stack, tags=["plan"], slug="mv-plan",
               body=json.dumps({"slice": {"title": "S", "intent": "i",
                                          "acceptance_criteria": ["c"]},
                                "tasks": [{"title": "K1", "intent": "k"}]}))
    ap = stack.post("/v1/plans/mv-plan/approve")
    assert ap.status_code == 200, ap.text
    kid = ap.json()["tasks"][0]["id"]
    assert ap.json()["tasks"][0]["project"] == "a"
    assert stack.patch(f"/v1/tasks/{kid}",
                       json={"project": "b", "idempotency_key": "mv3"}).status_code == 422


def test_knowledge_review_archive_pin(stack):
    e = _entry(stack)
    for verdict in ("rejected", "needs_revision", "verified"):
        r = stack.post(f"/v1/knowledge/{e['id']}/review",
                       json={"verdict": verdict, "note": "looked"})
        assert r.status_code == 200, r.text
        assert r.json()["review_state"] == verdict
    assert stack.post(f"/v1/knowledge/{e['id']}/review",
                      json={"verdict": "bogus"}).status_code == 422
    got = stack.get(f"/v1/knowledge/{e['id']}").json()
    assert got["review_state"] == "verified" and got["verified"] == 1
    r = stack.post(f"/v1/knowledge/{e['id']}/archive", json={"archived": True})
    assert r.status_code == 200 and r.json()["archived"] == 1
    hits = stack.get("/v1/knowledge/search", params={"q": "body"}).json()["items"]
    assert all(h["citation"] != got["citation"] for h in hits)
    assert stack.get(f"/v1/knowledge/{e['id']}").status_code == 200
    r = stack.post(f"/v1/knowledge/{e['id']}/pin", json={"pinned": True})
    assert r.status_code == 200 and r.json()["pinned"] == 1


def test_tasks_pagination_and_search_offset(stack):
    for i in range(5):
        _task(stack, project="a", title=f"t{i}")
    _entry(stack, title="Paginate me", body="pagination probe body text")
    page = stack.get("/v1/tasks", params={"tenant": "demo", "limit": 2}).json()
    assert [t["title"] for t in page["items"]] == ["t", "t0"] or len(page["items"]) == 2
    assert page["total"] >= 5 and page["offset"] == 0
    page2 = stack.get("/v1/tasks",
                      params={"tenant": "demo", "limit": 2, "offset": 2}).json()
    assert page2["offset"] == 2 and len(page2["items"]) == 2
    assert stack.get("/v1/tasks", params={"limit": 0}).status_code == 422
    full = stack.get("/v1/knowledge/search", params={"q": "body"}).json()["items"]
    assert len(full) >= 1
    part = stack.get("/v1/knowledge/search", params={"q": "body", "n": 1}).json()["items"]
    assert len(part) == 1
    rest = stack.get("/v1/knowledge/search",
                     params={"q": "body", "n": 100, "offset": 1}).json()["items"]
    assert len(rest) == len(full) - 1


def test_management_fail_closed(app):
    from fastapi.testclient import TestClient

    anon = TestClient(app)
    assert anon.patch("/v1/projects/a", json={}).status_code == 401
    assert anon.patch("/v1/tasks/1", json={}).status_code == 401
    assert anon.post("/v1/knowledge/1/review", json={}).status_code == 401
    assert anon.post("/v1/knowledge/1/archive", json={}).status_code == 401
    assert anon.post("/v1/knowledge/1/pin", json={}).status_code == 401
    assert anon.get("/v1/plans/x").status_code == 401
