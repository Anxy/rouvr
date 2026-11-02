"""Audit fixes: cross-tenant version write, compact visibility, confirm gate."""
import hashlib

from fastapi.testclient import TestClient


def _ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    monkeypatch.setenv("ROUVR_DATA_DIR", str(tmp_path / "data"))
    from ledger.app import make_app
    app = make_app(f"sqlite:///{tmp_path}/a.db")
    stack = {"X-Rouvr-Token": "stack-secret"}
    with TestClient(app) as c:
        for t in ("acme", "globex"):
            assert c.post("/v1/tenants", json={"tenant": t}, headers=stack).status_code == 200
            assert c.post("/v1/projects", json={"tenant": t, "project": "p"},
                          headers=stack).status_code == 200
        users = {}
        for user, tok, tenant in (("amy", "amy-tok", "acme"), ("abe", "abe-tok", "acme"),
                                  ("gus", "gus-tok", "globex")):
            h = hashlib.sha256(tok.encode()).hexdigest()
            assert c.post("/v1/users", json={"username": user, "token_hash": h,
                                             "tenant": tenant}, headers=stack).status_code == 200
            users[user] = {"X-Rouvr-Token": tok}
    return app, stack, users


def test_cross_tenant_version_write_refused(tmp_path, monkeypatch):
    app, stack, users = _ctx(tmp_path, monkeypatch)
    with TestClient(app) as c:
        e = c.post("/v1/knowledge", json={"tenant": "acme", "project": "p",
                   "kind": "note", "title": "Pub", "body": "public body",
                   "visibility": "public"}, headers=users["amy"]).json()
        r = c.post(f"/v1/knowledge/{e['id']}/versions",
                   json={"title": "Evil", "body": "hijack"}, headers=users["gus"])
        assert r.status_code == 403, r.text
        # same-tenant version still works
        r = c.post(f"/v1/knowledge/{e['id']}/versions",
                   json={"title": "V2", "body": "update"}, headers=users["amy"])
        assert r.status_code == 200


def test_compact_does_not_leak_restricted(tmp_path, monkeypatch):
    app, stack, users = _ctx(tmp_path, monkeypatch)
    with TestClient(app) as c:
        e = c.post("/v1/knowledge", json={"tenant": "acme", "project": "p",
                   "kind": "note", "title": "SecretTitle", "body": "secret body",
                   "visibility": "restricted", "allowed_users": []},
                   headers=users["amy"]).json()
        assert c.post(f"/v1/knowledge/{e['id']}/confirm", json={},
                      headers=users["amy"]).status_code == 200
        comp = c.post("/v1/knowledge/compact", json={"tenant": "acme", "project": "p"},
                      headers=users["amy"]).json()
        assert comp["snapshot"]["visibility"] == "restricted"
        # same-tenant non-member cannot read the snapshot
        assert c.get(f"/v1/knowledge/{comp['snapshot']['id']}",
                     headers=users["abe"]).status_code == 404
        assert c.get("/v1/knowledge/search", params={"q": "SecretTitle"},
                     headers=users["abe"]).json()["items"] == []


def test_confirm_only_creator_or_admin(tmp_path, monkeypatch):
    app, stack, users = _ctx(tmp_path, monkeypatch)
    with TestClient(app) as c:
        e = c.post("/v1/knowledge", json={"tenant": "acme", "project": "p",
                   "kind": "rule", "title": "R", "body": "must cite"},
                   headers=users["amy"]).json()
        assert c.post(f"/v1/knowledge/{e['id']}/confirm", json={},
                      headers=users["abe"]).status_code == 403
        assert c.post(f"/v1/knowledge/{e['id']}/confirm", json={},
                      headers=users["amy"]).status_code == 200
        e2 = c.post("/v1/knowledge", json={"tenant": "acme", "project": "p",
                    "kind": "rule", "title": "Pub", "body": "public rule",
                    "visibility": "public"}, headers=users["amy"]).json()
        assert c.post(f"/v1/knowledge/{e2['id']}/confirm", json={},
                      headers=users["gus"]).status_code in (403, 404)
