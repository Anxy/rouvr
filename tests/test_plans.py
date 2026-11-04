"""P0 plans endpoints: derived plan status + approve-to-materialize (task 178).

Lifecycle is derived, not stored: a plan-tagged knowledge entry is draft
until verified, agreed once confirmed, materialized once a plan:convert
receipt links it to a slice. Approve confirms + materializes through the
same _materialize_plan path chat Convert uses, idempotent on the entry.
"""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def no_network_suites(monkeypatch):
    """Seal authoring is a model call (up to the 90 s shared budget):
    hermetic tests use the documented seam instead."""
    import rouvr.mcp_server as mcp

    monkeypatch.setattr(
        mcp, "_author_hidden_suite",
        lambda criteria, timeout=120: {
            "test_rouvr_hidden_sealed.py": "# sealed\ndef test_sealed():\n    assert True\n",
        },
    )


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_AUTH_SECRET", "test-secret-for-plans-step-12345678901")
    monkeypatch.setenv("ROUVR_ADMIN_PASSWORD", "plans-admin-password-0123456789")
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    monkeypatch.delenv("ROUVR_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("META_API_KEY", raising=False)
    from ledger.app import make_app

    app = make_app(f"sqlite:///{tmp_path}/plans.db")
    reg = TestClient(app)
    headers = {"x-rouvr-token": "stack-secret"}
    reg.post("/v1/tenants", json={"tenant": "demo"}, headers=headers).raise_for_status()
    reg.post("/v1/projects", json={"tenant": "demo", "project": "demo"},
             headers=headers).raise_for_status()
    return app


@pytest.fixture()
def stack(app):
    return TestClient(app, headers={"x-rouvr-token": "stack-secret"})


@pytest.fixture()
def session_client(app):
    from ledger.app import make_app  # noqa

    c = TestClient(app)
    login = c.post("/auth/login", data={"username": "admin",
                                        "password": "plans-admin-password-0123456789"})
    assert login.status_code == 200, login.text
    fwd = dict(login.cookies)
    csrf = login.json()["csrf_token"]
    return TestClient(app, cookies=fwd,
                      headers={"Origin": "http://testserver", "x-csrf-token": csrf})


PLAN_BODY = {
    "source": "test",
    "category": "feature",
    "class": "task",
    "slice": {"title": "Ship the widget", "intent": "widget end to end",
              "acceptance_criteria": ["widget ships", "docs updated"]},
    "tasks": [{"title": "Build widget", "intent": "build it"},
              {"title": "Document widget", "intent": "write docs"}],
}


def _plan_entry(client, slug="ship-widget", tenant="demo", project="demo", **kw):
    body = {"kind": "note", "title": "Plan: Ship the widget",
            "body": json.dumps(PLAN_BODY), "tenant": tenant, "project": project,
            "tags": ["plan"], "slug": slug}
    body.update(kw)
    r = client.post("/api/v1/knowledge", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_plan_draft_then_approve_materializes(stack):
    entry = _plan_entry(stack)
    slug = entry["slug"]
    got = stack.get(f"/api/v1/plans/{slug}", params={"tenant": "demo"}).json()
    assert got["status"] == "draft", got
    assert got["slice"] is None and got["tasks"] == []

    before = stack.get("/api/v1/tasks", params={"tenant": "demo"}).json()["items"]
    r = stack.post(f"/api/v1/plans/{slug}/approve")
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["status"] == "materialized" and res["confirmed"] is True
    assert res["slice"]["id"] > 0
    assert [t["title"] for t in res["tasks"]] == ["Build widget", "Document widget"]
    assert all(t["link"] == f"/workspace/tasks/{t['id']}" for t in res["tasks"])
    after = stack.get("/api/v1/tasks", params={"tenant": "demo"}).json()["items"]
    assert len(after) == len(before) + 3  # slice + 2 kids

    got = stack.get(f"/api/v1/plans/{slug}", params={"tenant": "demo"}).json()
    assert got["status"] == "materialized", got
    assert got["slice"]["id"] == res["slice"]["id"]

    # second approve replays: same slice, no new tasks
    r = stack.post(f"/api/v1/plans/{slug}/approve")
    assert r.status_code == 200, r.text
    assert r.json()["replayed"] is True
    assert r.json()["slice"]["id"] == res["slice"]["id"]
    again = stack.get("/api/v1/tasks", params={"tenant": "demo"}).json()["items"]
    assert len(again) == len(after)


def test_plan_chat_convert_then_approve_replays(stack, session_client):
    # entry created by the chatting user (author matches, as the offer path
    # saves), converted through the real chat plan_action, then approved:
    # one slice total.
    entry = _plan_entry(session_client, slug="loop-plan")
    r = session_client.post("/api/v1/chat", json={"message": "convert it",
                                                  "project": "demo", "tenant": "demo",
                                                  "message_id": "plan-loop-convert-1",
                                                  "plan_action": "convert",
                                                  "plan_entry": entry["id"]})
    assert r.status_code == 200, r.text
    frames = [ln for ln in r.text.splitlines() if ln.startswith("data:")]
    res = json.loads(frames[-1][len("data:"):])
    # explicit Convert also files the intake head task (same _do_convert
    # semantics as the UI button): slice + head + 2 kids.
    assert res["created"] == 4, res
    titles = [t["title"] for t in res["tasks"]]
    assert "Build widget" in titles and "Document widget" in titles
    got = session_client.get("/api/v1/plans/loop-plan").json()
    assert got["status"] == "materialized", got
    n = len(session_client.get("/api/v1/tasks").json()["items"])
    r = session_client.post("/api/v1/plans/loop-plan/approve")
    assert r.status_code == 200, r.text
    assert r.json()["replayed"] is True
    assert r.json()["slice"]["id"] == got["slice"]["id"]
    assert len(session_client.get("/api/v1/tasks").json()["items"]) == n


def test_plan_approve_then_chat_convert_replays(stack, session_client):
    # the user-demanded order: UI Approve first, chat Convert after must
    # replay the same slice, never a second one.
    entry = _plan_entry(session_client, slug="approve-first")
    r = session_client.post("/api/v1/plans/approve-first/approve")
    assert r.status_code == 200, r.text
    slice_id = r.json()["slice"]["id"]
    n = len(session_client.get("/api/v1/tasks").json()["items"])
    r = session_client.post("/api/v1/chat", json={"message": "convert it",
                                                  "project": "demo", "tenant": "demo",
                                                  "message_id": "plan-loop-convert-2",
                                                  "plan_action": "convert",
                                                  "plan_entry": entry["id"]})
    assert r.status_code == 200, r.text
    got = session_client.get("/api/v1/plans/approve-first").json()
    assert got["status"] == "materialized", got
    assert got["slice"]["id"] == slice_id, got
    assert len(session_client.get("/api/v1/tasks").json()["items"]) == n


def test_plan_execute_dispatch(stack):
    entry = _plan_entry(stack, slug="exec-plan")
    # draft plans refuse dispatch: approve first
    r = stack.post("/api/v1/plans/exec-plan/execute", json={})
    assert r.status_code == 422, r.text
    stack.post("/api/v1/plans/exec-plan/approve").raise_for_status()

    # dispatch without a worker: tasks stay open and claimable, event filed
    r = stack.post("/api/v1/plans/exec-plan/execute", json={})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["status"] == "materialized"
    assert len(res["dispatched"]) == 2 and res["assigned"] == []
    assert res["contested"] == [] and res["execute_event"] > 0
    assert all(t["worker"] is None for t in res["tasks"])

    # dispatch with a worker assigns each chain task, idempotently
    r = stack.post("/api/v1/plans/exec-plan/execute",
                   json={"worker": "e2e-worker"})
    assert r.status_code == 200, r.text
    res = r.json()
    assert sorted(res["assigned"]) == sorted(res["dispatched"])
    assert all(t["state"] == "claimed" for t in res["tasks"])
    assert all(t["worker"] == "e2e-worker" for t in res["tasks"])
    again = stack.post("/api/v1/plans/exec-plan/execute",
                       json={"worker": "e2e-worker"}).json()
    assert sorted(again["assigned"]) == sorted(res["dispatched"])
    assert again["contested"] == []

    # a rival worker cannot steal the live leases
    rival = stack.post("/api/v1/plans/exec-plan/execute",
                       json={"worker": "rival"}).json()
    assert rival["assigned"] == []
    assert sorted(rival["contested"]) == sorted(res["dispatched"])


def test_plan_endpoints_fail_closed(stack, session_client):
    assert stack.get("/api/v1/plans/nope", params={"tenant": "demo"}).status_code == 404
    assert stack.post("/api/v1/plans/nope/approve").status_code == 404
    assert stack.post("/api/v1/plans/nope/execute", json={}).status_code == 404
    # non-plan entries are not plans: untagged slugs 404 ...
    r = stack.post("/api/v1/knowledge", json={"kind": "note", "title": "Plain",
                                              "body": "just a note", "tenant": "demo",
                                              "project": "demo", "tags": ["note"],
                                              "slug": "plain-note"})
    assert r.status_code == 200, r.text
    assert stack.get("/api/v1/plans/plain-note",
                     params={"tenant": "demo"}).status_code == 404
    assert stack.post("/api/v1/plans/plain-note/approve").status_code == 404
    # ... while plan-tagged entries with a non-plan body are 422
    r = stack.post("/api/v1/knowledge", json={"kind": "note", "title": "Fake plan",
                                              "body": "not a plan body", "tenant": "demo",
                                              "project": "demo", "tags": ["plan"],
                                              "slug": "fake-plan"})
    assert r.status_code == 200, r.text
    assert stack.get("/api/v1/plans/fake-plan",
                     params={"tenant": "demo"}).status_code == 422
    assert stack.post("/api/v1/plans/fake-plan/approve").status_code == 422
    # anonymous callers are refused before any plan logic
    from fastapi.testclient import TestClient

    anon = TestClient(stack.app)
    assert anon.get("/api/v1/plans/x", params={"tenant": "demo"}).status_code == 401
    # session owners cannot reach across tenants
    stack.post("/v1/tenants", json={"tenant": "other"}).raise_for_status()
    stack.post("/v1/projects", json={"tenant": "other", "project": "site"}).raise_for_status()
    _plan_entry(stack, slug="other-plan", tenant="other", project="site")
    r = session_client.post("/api/v1/plans/other-plan/approve")
    assert r.status_code == 404, r.text
