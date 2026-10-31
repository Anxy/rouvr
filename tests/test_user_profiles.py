"""S016: per-user tokens, task ownership, worker-only operations."""
import hashlib
import threading
import time

import httpx
import pytest
import uvicorn

STACK = {"X-Rouvr-Token": "stack-secret"}


def _tok(s):
    return {"X-Rouvr-Token": s}


@pytest.fixture()
def url(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    import importlib
    import ledger.app as la
    importlib.reload(la)
    config = uvicorn.Config(la.make_app(f"sqlite:///{tmp_path}/u.db"),
                            host="127.0.0.1", port=18251, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            if httpx.get("http://127.0.0.1:18251/health", timeout=1).status_code == 200: break
        except httpx.TransportError: pass
        time.sleep(0.1)
    yield "http://127.0.0.1:18251"
    server.should_exit = True
    monkeypatch.delenv("ROUVR_LEDGER_TOKEN")
    importlib.reload(la)


def _scope(c, tenant="acme", project="site"):
    """S022: register the partition this module's tasks run inside."""
    c.post("/v1/tenants", json={"tenant": tenant}, headers=STACK).raise_for_status()
    c.post("/v1/projects", json={"tenant": tenant, "project": project}, headers=STACK
           ).raise_for_status()


def _seed(c, name, token, tenant="acme"):
    h = hashlib.sha256(token.encode()).hexdigest()
    r = c.post("/v1/users", json={"username": name, "token_hash": h, "tenant": tenant},
               headers=STACK)
    assert r.status_code == 200, r.text


def test_two_user_scoping_and_worker_only(url):
    with httpx.Client(base_url=url, timeout=10) as c:
        _scope(c)
        _scope(c, tenant="globex")
        _seed(c, "alice", "alice-tok", tenant="acme")
        _seed(c, "bob", "bob-tok", tenant="globex")
        ta = c.post("/v1/tasks", json={"title": "a", "intent": "supervisor",
                                       "tenant": "acme", "project": "site"},
                    headers=_tok("alice-tok")).json()
        tb = c.post("/v1/tasks", json={"title": "b", "intent": "supervisor",
                                       "tenant": "globex", "project": "site"},
                    headers=_tok("bob-tok")).json()
        assert ta["created_by"] == "alice" and tb["created_by"] == "bob"
        assert (ta["tenant"], tb["tenant"]) == ("acme", "globex")
        mine = c.get("/v1/tasks", headers=_tok("alice-tok")).json()["items"]
        assert [t["id"] for t in mine] == [ta["id"]]
        assert len(c.get("/v1/tasks", headers=STACK).json()["items"]) == 2
        # cross-tenant access refused, naming the tenant
        r = c.get(f"/v1/tasks/{tb['id']}", headers=_tok("alice-tok"))
        assert r.status_code == 403 and "globex" in r.json()["detail"]
        assert c.post(f"/v1/tasks/{tb['id']}/evidence",
                      json={"kind": "agent_message", "body": {}},
                      headers=_tok("alice-tok")).status_code == 403
        # user token cannot do worker-only operations
        assert c.post(f"/v1/tasks/{ta['id']}/transition",
                      json={"to": "blocked", "idempotency_key": "k1"},
                      headers=_tok("alice-tok")).status_code == 403
        # unknown + disabled tokens rejected
        assert c.get("/v1/tasks", headers=_tok("nope")).status_code == 401
        c.post("/v1/users/bob/disable", headers=STACK)
        assert c.get("/v1/tasks", headers=_tok("bob-tok")).status_code == 401
        # no plaintext token anywhere in the users listing
        assert "alice-tok" not in c.get("/v1/users", headers=STACK).text
