"""Step 1: cookie sessions via FastAPI Users + argon2, CSRF, rate limit."""
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_AUTH_SECRET", "test-secret-for-auth-step-10123456789")
    monkeypatch.setenv("ROUVR_ADMIN_PASSWORD", "admin-test-password-0123456789")
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    from ledger.app import make_app

    app = make_app(f"sqlite:///{tmp_path}/a.db")
    # The seed creates no ledger rows (S022): register the demo scope
    # explicitly with the stack token before exercising sessions.
    reg = TestClient(app)
    headers = {"x-rouvr-token": "stack-secret"}
    reg.post("/v1/tenants", json={"tenant": "demo"}, headers=headers).raise_for_status()
    reg.post("/v1/projects", json={"tenant": "demo", "project": "demo"},
             headers=headers).raise_for_status()
    return app


@pytest.fixture()
def user_client(app):
    from ledger.app import make_app  # noqa
    return TestClient(app)


def test_anonymous_api_requires_session(user_client):
    r = user_client.get("/api/v1/tasks")
    assert r.status_code == 401, (r.status_code, r.text)


def test_health_stays_open_without_session(user_client):
    # A3 hidden contract: /api/v1/health mirrors ledger /health, no auth.
    r1 = user_client.get("/api/v1/health")
    r2 = user_client.get("/health")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json()


def _login(user_client, username="admin", password="admin-test-password-0123456789"):
    return user_client.post("/auth/login", data={"username": username, "password": password})


def test_login_sets_httponly_lax_cookie_and_csrf(user_client):
    r = _login(user_client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["username"] == "admin" and body["tenant"] == "demo" and body["role"] == "owner"
    assert body["csrf_token"]
    raw = r.headers.get("set-cookie", "")
    assert "rouvr_session=" in raw
    assert "HttpOnly" in raw
    assert "SameSite=Lax" in raw or "samesite=lax" in raw.lower()


def test_remember_me_extends_cookie_and_default_stays_short(user_client):
    plain = _login(user_client)
    assert "Max-Age=43200" in plain.headers.get("set-cookie", ""), plain.headers.get("set-cookie")
    remembered = user_client.post("/auth/login", data={
        "username": "admin", "password": "admin-test-password-0123456789",
        "remember": "true"})
    assert remembered.status_code == 200, remembered.text
    raw = remembered.headers.get("set-cookie", "")
    assert "Max-Age=2592000" in raw, raw


def test_session_cookie_accesses_tasks(user_client):
    c = TestClient(user_client.app, cookies=_login(user_client).cookies)
    r = c.get("/api/v1/tasks")
    assert r.status_code == 200, (r.status_code, r.text)


def test_cookie_post_without_origin_or_token_is_403(user_client):
    c = TestClient(user_client.app, cookies=_login(user_client).cookies)
    r = c.post("/api/v1/tasks", json={"title": "x", "intent": "i", "tenant": "demo", "project": "demo"})
    assert r.status_code == 403, (r.status_code, r.text)


def test_cookie_post_with_origin_passes(user_client):
    c = TestClient(user_client.app, cookies=_login(user_client).cookies)
    r = c.post("/api/v1/tasks", json={"title": "x", "intent": "i", "tenant": "demo", "project": "demo"},
               headers={"Origin": "http://testserver"})
    assert r.status_code == 200, (r.status_code, r.text)
    assert r.json()["tenant"] == "demo"


def test_cookie_post_with_csrf_token_passes(user_client):
    login = _login(user_client)
    c = TestClient(user_client.app, cookies=login.cookies)
    r = c.post("/api/v1/tasks", json={"title": "y", "intent": "i", "tenant": "demo", "project": "demo"},
               headers={"X-CSRF-Token": login.json()["csrf_token"]})
    assert r.status_code == 200, (r.status_code, r.text)


def test_login_rate_limit_blocks_after_five_failures(user_client):
    for _ in range(5):
        r = user_client.post("/auth/login", data={"username": "admin", "password": "wrong-password-xyz"})
        assert r.status_code == 401
    r = user_client.post("/auth/login", data={"username": "admin", "password": "wrong-password-xyz"})
    assert r.status_code == 429, (r.status_code, r.text)
    assert "Retry-After" in r.headers


def test_generated_password_never_logged(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("ROUVR_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("ROUVR_AUTH_SECRET", "test-secret-2-for-auth-step-1ab56789012")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    monkeypatch.delenv("ROUVR_LEDGER_TOKEN", raising=False)
    import logging
    from ledger.app import make_app

    with caplog.at_level(logging.INFO):
        make_app(f"sqlite:///{tmp_path}/g.db")
    # emit_secret_once writes only to an explicit stream or /dev/tty: the
    # generated value must never reach the logging subsystem.
    assert "Rouvr admin password (printed once" not in caplog.text
    # and the auth user row holds an argon2 hash, never plaintext
    import sqlite3
    con = sqlite3.connect(tmp_path / "g.db")
    try:
        h = con.execute("SELECT hashed_password FROM user WHERE email='admin@local'").fetchone()[0]
    finally:
        con.close()
    assert h.startswith("$argon2"), h[:12]


def test_emit_secret_once_writes_to_stream_only():
    import io
    from ledger.auth import emit_secret_once

    stream = io.StringIO()
    assert emit_secret_once("s3cr3t-value", stream=stream) is True
    assert "s3cr3t-value" in stream.getvalue()


def test_admin_login_uses_argon2_and_maps_tenant_role(user_client):
    r = _login(user_client)
    assert r.status_code == 200
    assert r.json()["tenant"] == "demo"
    assert r.json()["role"] == "owner"


def test_logout_clears_cookie(user_client):
    c = TestClient(user_client.app, cookies=_login(user_client).cookies)
    r = c.post("/auth/logout", headers={"Origin": "http://testserver"})
    assert r.status_code == 200
    assert "rouvr_session=" in r.headers.get("set-cookie", "")


def _session_task(c, title, project="demo"):
    r = c.post("/api/v1/tasks", json={"title": title, "intent": "web approval",
                                      "tenant": "demo", "project": project},
               headers={"Origin": "http://testserver"})
    assert r.status_code == 200, (r.status_code, r.text)
    return r.json()


def _transition(c, tid, to, key):
    return c.post(f"/api/v1/tasks/{tid}/transition", json={"to": to, "idempotency_key": key},
                  headers={"Origin": "http://testserver"})


def _worker_verify(stack_client, tid, verdict, tag):
    """Drive the worker flow with the stack token: claim -> attempt -> result."""
    h = {"x-rouvr-token": "stack-secret"}
    claim = stack_client.post(f"/v1/tasks/{tid}/assignments/claim",
                              json={"key": f"v-{tag}-claim", "principal": "worker-1",
                                    "attempt_no": 1}, headers=h)
    assert claim.status_code == 200, (claim.status_code, claim.text)
    epoch = claim.json()["lease_epoch"]
    att = stack_client.post(f"/v1/tasks/{tid}/attempts",
                            json={"key": f"v-{tag}-attempt", "attempt_no": 1,
                                  "lease_epoch": epoch}, headers=h)
    assert att.status_code == 200, (att.status_code, att.text)
    res = stack_client.post(f"/v1/tasks/{tid}/results",
                            json={"key": f"v-{tag}-result",
                                  "attempt_id": att.json()["attempt"]["id"],
                                  "lease_epoch": epoch, "verdict": verdict,
                                  "result": {}}, headers=h)
    assert res.status_code == 200, (res.status_code, res.text)


def _ready_for_done(c, tid):
    # done needs a contract and evidence: add both as the session owner
    assert c.post(f"/api/v1/tasks/{tid}/contracts",
                  json={"acceptance_criteria": ["owner approved"]},
                  headers={"Origin": "http://testserver"}).status_code == 200
    assert c.post(f"/api/v1/tasks/{tid}/evidence", json={"kind": "note", "body": {}},
                  headers={"Origin": "http://testserver"}).status_code == 200


def test_session_owner_can_approve_and_retry(user_client):
    stack_client = TestClient(user_client.app)
    c = TestClient(user_client.app, cookies=_login(user_client).cookies)
    t = _session_task(c, "approve me")
    tid = t["id"]
    assert _transition(c, tid, "claimed", "k-claim").status_code == 200
    assert _transition(c, tid, "needs_review", "k-review").status_code == 200
    _ready_for_done(c, tid)
    _worker_verify(stack_client, tid, "pass", "approve")
    assert _transition(c, tid, "needs_review", "k-rereview").status_code == 200
    r = _transition(c, tid, "done", "k-done")
    assert r.status_code == 200, (r.status_code, r.text)
    assert c.get(f"/api/v1/tasks/{tid}").json()["state"] == "done"
    # retry path: claimed -> failed -> open
    t2 = _session_task(c, "retry me")
    assert _transition(c, t2["id"], "claimed", "k2-claim").status_code == 200
    assert _transition(c, t2["id"], "failed", "k2-fail").status_code == 200
    assert _transition(c, t2["id"], "open", "k2-retry").status_code == 200
    assert c.get(f"/api/v1/tasks/{t2['id']}").json()["state"] == "open"


def test_owner_approve_requires_verified_review(user_client):
    stack_client = TestClient(user_client.app)
    c = TestClient(user_client.app, cookies=_login(user_client).cookies)
    # unverified head: needs_review but no result yet
    t = _session_task(c, "unverified")
    assert _transition(c, t["id"], "claimed", "g-claim").status_code == 200
    assert _transition(c, t["id"], "needs_review", "g-review").status_code == 200
    _ready_for_done(c, t["id"])
    r = _transition(c, t["id"], "done", "g-done")
    assert r.status_code == 409, (r.status_code, r.text)
    assert "passing verify" in r.text, r.text
    # failing head: a fail verdict is not a pass
    _worker_verify(stack_client, t["id"], "fail", "guard-fail")
    assert _transition(c, t["id"], "needs_review", "g-rereview").status_code == 200
    r = _transition(c, t["id"], "done", "g-done-2")
    assert r.status_code == 409 and "passing verify" in r.text
    # wrong state: verified but still claimed
    t2 = _session_task(c, "wrong state")
    assert _transition(c, t2["id"], "claimed", "g2-claim").status_code == 200
    _ready_for_done(c, t2["id"])
    _worker_verify(stack_client, t2["id"], "pass", "guard-state")
    r = _transition(c, t2["id"], "done", "g2-done")
    assert r.status_code == 409, (r.status_code, r.text)
    assert "needs_review" in r.text, r.text


def test_session_owner_cannot_touch_foreign_tenant(app):
    stack = TestClient(app)
    headers = {"x-rouvr-token": "stack-secret"}
    stack.post("/v1/tenants", json={"tenant": "acme"}, headers=headers).raise_for_status()
    stack.post("/v1/projects", json={"tenant": "acme", "project": "site"},
               headers=headers).raise_for_status()
    other = stack.post("/v1/tasks", json={"title": "acme work", "intent": "i",
                                          "tenant": "acme", "project": "site"},
                       headers=headers).json()
    c = TestClient(app, cookies=_login(TestClient(app)).cookies)
    r = _transition(c, other["id"], "claimed", "k-x")
    assert r.status_code == 404, (r.status_code, r.text)  # no oracle, not 403
