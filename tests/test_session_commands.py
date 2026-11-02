"""Session commands: store / compact / snapshot / store-and-close + curator."""
import hashlib

from fastapi.testclient import TestClient


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    monkeypatch.setenv("ROUVR_DATA_DIR", str(tmp_path / "data"))
    from ledger.app import make_app
    return make_app(f"sqlite:///{tmp_path}/s.db")


def _seed(app):
    stack = {"X-Rouvr-Token": "stack-secret"}
    with TestClient(app) as c:
        assert c.post("/v1/tenants", json={"tenant": "acme"}, headers=stack).status_code == 200
        assert c.post("/v1/projects", json={"tenant": "acme", "project": "site"},
                      headers=stack).status_code == 200
        h = hashlib.sha256(b"amy-tok").hexdigest()
        assert c.post("/v1/users", json={"username": "amy", "token_hash": h,
                                         "tenant": "acme"}, headers=stack).status_code == 200
    return stack, {"X-Rouvr-Token": "amy-tok"}


def test_store_compact_snapshot_and_close(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    stack, amy = _seed(app)
    with TestClient(app) as c:
        r = c.post("/v1/knowledge/store", json={"tenant": "acme", "project": "site",
                   "items": [{"kind": "decision", "title": "D1", "body": "chose sqlite"},
                             {"kind": "note", "title": "N1", "body": "note body"}]},
                   headers=amy)
        assert r.status_code == 200, r.text
        assert len(r.json()["items"]) == 2
        eids = [e["id"] for e in r.json()["items"]]
        for eid in eids:
            assert c.post(f"/v1/knowledge/{eid}/confirm", json={}, headers=amy).status_code == 200
        comp = c.post("/v1/knowledge/compact",
                      json={"tenant": "acme", "project": "site"}, headers=amy).json()
        assert comp["snapshot"]["kind"] == "snapshot"
        assert len(comp["sources"]) == 2 and all(s.startswith("knowledge:") for s in comp["sources"])
        assert "knowledge:" in comp["snapshot"]["body"]
        snap = c.post("/v1/knowledge/snapshot",
                      json={"tenant": "acme", "project": "site"}, headers=amy).json()
        assert snap["count"] >= 2 and snap["path"].endswith(".json")
        sc = c.post("/v1/knowledge/store-and-close",
                    json={"tenant": "acme", "project": "site",
                          "items": [{"kind": "rule", "title": "R1", "body": "must cite"}]},
                    headers=amy)
        assert sc.status_code == 200, sc.text
        assert len(sc.json()["stored"]) == 1


def test_curate_suggests_only(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    stack, amy = _seed(app)
    with TestClient(app) as c:
        c.post("/v1/knowledge", json={"tenant": "acme", "project": "site", "kind": "decision",
               "title": "Same title", "body": "first"}, headers=amy)
        c.post("/v1/knowledge", json={"tenant": "acme", "project": "site", "kind": "note",
               "title": "Same Title", "body": "second"}, headers=amy)
        before = c.get("/v1/knowledge/search", params={"q": "Same"},
                       headers=stack).json()["items"]
        cur = c.post("/v1/knowledge/curate",
                     json={"tenant": "acme", "project": "site"}, headers=amy).json()
        assert cur["count"] >= 1
        assert any(s["type"] == "duplicate" for s in cur["suggestions"])
        after = c.get("/v1/knowledge/search", params={"q": "Same"},
                      headers=stack).json()["items"]
        assert len(after) == len(before)  # never deletes
        assert all(e["verified"] == 0 for e in after)  # never verifies
