"""Adversarial hardening: visibility coercion, snapshot filename, races."""
import hashlib

from fastapi.testclient import TestClient


def _ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    monkeypatch.setenv("ROUVR_DATA_DIR", str(tmp_path / "data"))
    from ledger.app import make_app
    return make_app(f"sqlite:///{tmp_path}/a.db")


def _users(c, stack):
    h = lambda t: hashlib.sha256(t.encode()).hexdigest()
    for t in ("acme",):
        c.post("/v1/tenants", json={"tenant": t}, headers=stack)
        c.post("/v1/projects", json={"tenant": t, "project": "p"}, headers=stack)
    c.post("/v1/users", json={"username": "amy", "token_hash": h("amy-tok"),
                              "tenant": "acme"}, headers=stack)
    return {"X-Rouvr-Token": "amy-tok"}


def test_explicit_bad_visibility_rejected_everywhere(tmp_path, monkeypatch):
    from ledger.app import make_app  # noqa
    app = _ctx(tmp_path, monkeypatch)
    stack = {"X-Rouvr-Token": "stack-secret"}
    with TestClient(app) as c:
        amy = _users(c, stack)
        for payload in ({"visibility": ""}, {"visibility": "Public"},
                        {"visibility": "company "}):
            r = c.post("/v1/knowledge", headers=amy, json={
                "tenant": "acme", "project": "p", "kind": "note",
                "title": "t", "body": "b", **payload})
            assert r.status_code == 422, (payload, r.status_code)
        r = c.post("/v1/knowledge/store", headers=amy, json={
            "tenant": "acme", "project": "p",
            "items": [{"kind": "note", "title": "t", "body": "b", "visibility": ""}]})
        assert r.status_code == 422, r.status_code
        assert c.get("/v1/knowledge/search", params={"q": "t"},
                     headers=amy).json()["items"] == []


def test_snapshot_filename_cannot_escape(tmp_path, monkeypatch):
    from pathlib import Path
    app = _ctx(tmp_path, monkeypatch)
    stack = {"X-Rouvr-Token": "stack-secret"}
    with TestClient(app) as c:
        assert c.post("/v1/tenants", json={"tenant": "../evil"},
                      headers=stack).status_code == 200
        assert c.post("/v1/projects", json={"tenant": "../evil", "project": "../../x"},
                      headers=stack).status_code == 200
        r = c.post("/v1/knowledge/snapshot",
                   json={"tenant": "../evil", "project": "../../x"}, headers=stack)
        assert r.status_code == 200, r.text
        p = Path(r.json()["path"]).resolve()
        assert (tmp_path / "data").resolve() in p.parents, p
        name = r.json()["path"].split("knowledge-snapshots/")[1]
        assert "/" not in name and "\\" not in name, name


def test_unicode_emoji_roundtrip(tmp_path, monkeypatch):
    app = _ctx(tmp_path, monkeypatch)
    stack = {"X-Rouvr-Token": "stack-secret"}
    with TestClient(app) as c:
        amy = _users(c, stack)
        body = "décision café \U0001F980 naïve \"quotes\" <tags> & entities"
        e = c.post("/v1/knowledge", headers=amy, json={
            "tenant": "acme", "project": "p", "kind": "decision",
            "title": "Zürich títle — dash", "body": body}).json()
        assert c.get(f"/v1/knowledge/{e['id']}", headers=amy).json()["body"] == body
        hits = c.get("/v1/knowledge/search", params={"q": "café"},
                     headers=amy).json()["items"]
        assert [h["citation"] for h in hits] == [e["citation"]]
