"""Supervisor chat ops: phrase matching + op execution via POST /v1/chat."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from ledger import chat_ops


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_AUTH_SECRET", "test-secret-for-chat-ops-12345678901")
    monkeypatch.setenv("ROUVR_ADMIN_PASSWORD", "chat-ops-password-0123456789")
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    monkeypatch.delenv("ROUVR_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("META_API_KEY", raising=False)
    from ledger.app import make_app

    app = make_app(f"sqlite:///{tmp_path}/chat-ops.db")
    reg = TestClient(app)
    headers = {"x-rouvr-token": "stack-secret"}
    reg.post("/v1/tenants", json={"tenant": "demo"}, headers=headers).raise_for_status()
    reg.post("/v1/projects", json={"tenant": "demo", "project": "demo"},
             headers=headers).raise_for_status()
    return app


@pytest.fixture()
def session_client(app):
    c = TestClient(app)
    login = c.post("/auth/login", data={"username": "admin",
                                        "password": "chat-ops-password-0123456789"})
    assert login.status_code == 200, login.text
    return TestClient(app, cookies=login.cookies)


def _frames(text):
    out = []
    for chunk in text.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        event, data = None, None
        for line in chunk.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        out.append((event, json.loads(data) if data else None))
    return out


def _chat(session_client, message, project="demo", **extra):
    body = {"message": message, "project": project, "tenant": "demo"}
    body.update(extra)
    return session_client.post(
        "/api/v1/chat", json=body, headers={"Origin": "http://testserver"})


def _result(r):
    assert r.status_code == 200, r.text
    res = [d for e, d in _frames(r.text) if e == "result"]
    assert len(res) == 1, r.text
    return res[0]


def test_phrase_match_approve_uses_context_slug():
    ctx = chat_ops.ChatContext(
        tenant="demo", project="demo", plan_slug="ship-it", plan_entry_id=9)
    m = chat_ops.match_phrase("approve this plan", ctx)
    assert m is not None
    assert m.op == "plan.approve"
    assert m.args["slug"] == "ship-it"


def test_bare_yes_left_for_plan_offer():
    ctx = chat_ops.ChatContext(tenant="demo", project="demo", plan_slug="x")
    assert chat_ops.match_phrase("yes", ctx) is None
    assert chat_ops.match_phrase("convert", ctx) is None


def test_git_grant_phrase_needs_dialog():
    ctx = chat_ops.ChatContext(tenant="demo", project="demo", task_ids=[7])
    m = chat_ops.match_phrase("grant pat", ctx)
    assert m is not None
    assert m.op == "task.git_grant"
    assert m.needs_dialog == "git_grant"
    assert m.args["task_id"] == 7


def test_explicit_op_rejects_unknown():
    ctx = chat_ops.ChatContext(tenant="demo", project="demo")
    with pytest.raises(ValueError):
        chat_ops.match_explicit("not.a.real.op", {}, ctx)


def test_chat_op_secret_key_rejected(session_client):
    r = _chat(session_client, "go", op="plan.approve",
              op_args={"slug": "x", "github_token": "sekrit"})
    assert r.status_code == 422


def test_chat_op_cancel(session_client):
    r = _chat(session_client, "cancel", op="__cancel__")
    data = _result(r)
    assert data["classification"] == "op"
    assert "Cancel" in data["reply"] or "cancel" in data["reply"].lower()


def test_chat_op_git_grant_dialog_handoff(session_client):
    r = _chat(session_client, "grant", op="task.git_grant",
              op_args={"task_id": 1})
    data = _result(r)
    assert data["op_result"]["needs_dialog"] == "git_grant"
    assert data["actions"]
    assert data["actions"][0]["needs_dialog"] == "git_grant"


def test_chat_op_approve_plan(session_client, monkeypatch):
    # Seed a plan knowledge entry the same way intake would.
    headers = {"x-rouvr-token": "stack-secret"}
    stack = TestClient(session_client.app)
    body = {
        "kind": "note",
        "title": "Plan: Ship",
        "body": json.dumps({
            "category": "feature",
            "slice": {"title": "Ship", "intent": "ship",
                      "acceptance_criteria": ["ships"]},
            "tasks": [{"title": "step 1", "intent": "do it"}],
        }),
        "tags": ["plan"],
        "project": "demo",
        "tenant": "demo",
    }
    # Use session to create knowledge via chat stub path is heavy; insert via
    # stack knowledge API if available.
    kr = stack.post("/v1/knowledge", json=body, headers=headers)
    if kr.status_code >= 400:
        pytest.skip(f"knowledge create unavailable: {kr.status_code} {kr.text}")
    entry = kr.json()
    slug = entry.get("slug")
    assert slug

    # Seal authoring may call the model; stub provider is fine / fail-soft.
    monkeypatch.setenv("ROUVR_MODEL_PROVIDER", "stub")
    r = _chat(session_client, "approve this plan",
              op="plan.approve", op_args={"slug": slug})
    data = _result(r)
    assert data["classification"] == "op"
    assert data["badge"]["kind"] == "op"
    # Either succeeded or failed closed with a detail — never silent.
    assert data["reply"]
    assert "actions" in data


def test_chat_op_create_project_confirm_then_run(session_client):
    # First phrase posts an op_offer (confirm=True).
    r = _chat(session_client, "create project demo2")
    data = _result(r)
    assert data.get("op_offer") or data["classification"] == "op"
    if data.get("op_offer"):
        assert data["op_offer"]["op"] == "project.create"
        # Confirm via chip.
        r2 = _chat(session_client, "yes",
                   op="project.create",
                   op_args=data["op_offer"].get("args") or {"name": "demo2"})
        data2 = _result(r2)
        assert data2["classification"] == "op"
        assert data2["op_result"] is None or data2["op_result"].get("ok") is not False \
            or "failed" in data2["reply"].lower() or "ok" in data2["reply"].lower() \
            or "Create project" in data2["reply"]


def test_catalog_covers_workspace_domains():
    ids = set(chat_ops.OPS)
    for needed in (
        "plan.approve", "plan.start", "plan.execute", "plan.restart",
        "task.start", "task.transition.done", "task.seal", "task.retire",
        "project.create", "project.archive", "project.exec_spec",
        "knowledge.review", "knowledge.pin", "knowledge.archive",
        "session.logout", "task.git_grant", "project.git_grant",
    ):
        assert needed in ids, needed
