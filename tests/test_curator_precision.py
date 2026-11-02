"""Curator precision: contradictions need a shared anchor + opposing claims."""
import hashlib

from fastapi.testclient import TestClient


def _ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    monkeypatch.setenv("ROUVR_DATA_DIR", str(tmp_path / "data"))
    from ledger.app import make_app
    app = make_app(f"sqlite:///{tmp_path}/c.db")
    stack = {"X-Rouvr-Token": "stack-secret"}
    with TestClient(app) as c:
        assert c.post("/v1/tenants", json={"tenant": "acme"}, headers=stack).status_code == 200
        assert c.post("/v1/projects", json={"tenant": "acme", "project": "site"},
                      headers=stack).status_code == 200
        h = hashlib.sha256(b"amy-tok").hexdigest()
        assert c.post("/v1/users", json={"username": "amy", "token_hash": h,
                                         "tenant": "acme"}, headers=stack).status_code == 200
    return app, stack, {"X-Rouvr-Token": "amy-tok"}


def _add(c, amy, title, body, kind="decision"):
    r = c.post("/v1/knowledge", json={"tenant": "acme", "project": "site",
               "kind": kind, "title": title, "body": body}, headers=amy)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_true_contradiction_flagged_with_confidence(tmp_path, monkeypatch):
    app, stack, amy = _ctx(tmp_path, monkeypatch)
    with TestClient(app) as c:
        _add(c, amy, "D14 rule authority", "D14: rules are binding once approved.")
        _add(c, amy, "D14 rejected", "D14 is rejected: rules are never binding.")
        cur = c.post("/v1/knowledge/curate", json={"tenant": "acme", "project": "site"},
                     headers=amy).json()
        contras = [s for s in cur["suggestions"] if s["type"] == "contradiction"]
        assert len(contras) == 1
        assert contras[0]["confidence"] == "high"
        assert "d14" in contras[0]["reason"]
        assert cur["contradictions_mode"] == "flags"


def test_agreement_and_unrelated_negation_not_flagged(tmp_path, monkeypatch):
    app, stack, amy = _ctx(tmp_path, monkeypatch)
    with TestClient(app) as c:
        _add(c, amy, "SQLite store", 'We store state in "per-tenant SQLite files".')
        _add(c, amy, "SQLite forever", 'We must keep using "per-tenant SQLite files".')
        _add(c, amy, "Unrelated veto", "We never allow deploys on Fridays.")
        cur = c.post("/v1/knowledge/curate", json={"tenant": "acme", "project": "site"},
                     headers=amy).json()
        contras = [s for s in cur["suggestions"] if s["type"] == "contradiction"]
        assert contras == []


def test_medium_confidence_shown_as_review_hints(tmp_path, monkeypatch):
    app, stack, amy = _ctx(tmp_path, monkeypatch)
    with TestClient(app) as c:
        _add(c, amy, "Store stays", 'We store state in "per-tenant SQLite files".')
        _add(c, amy, "Store moved", 'We do not store state in "per-tenant SQLite files".')
        cur = c.post("/v1/knowledge/curate", json={"tenant": "acme", "project": "site"},
                     headers=amy).json()
        contras = [s for s in cur["suggestions"] if s["type"] == "contradiction"]
        assert len(contras) == 1 and contras[0]["confidence"] == "medium"
        assert cur["contradictions_mode"] == "review_hints"


def test_contradiction_overflow_becomes_ranked_hints(tmp_path, monkeypatch):
    app, stack, amy = _ctx(tmp_path, monkeypatch)
    with TestClient(app) as c:
        for i in range(20):
            _add(c, amy, f"D{i} stands", f"D{i}: feature {i} is required.")
            _add(c, amy, f"D{i} falls", f"D{i}: feature {i} is not required.")
        cur = c.post("/v1/knowledge/curate", json={"tenant": "acme", "project": "site"},
                     headers=amy).json()
        contras = [s for s in cur["suggestions"] if s["type"] == "contradiction"]
        assert cur["contradictions_mode"] == "review_hints"
        assert len(contras) == 10
        assert all(s["confidence"] == "high" for s in contras)
