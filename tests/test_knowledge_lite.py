"""Knowledge Lite (hackathon cut): append-only versioned entries in the
ledger, FTS5 search, tenant-scoped by caller token, verified flag carried
on every read. The canary test is the point: a tenant's knowledge must
never surface in another tenant's search results or reads."""
import hashlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    from ledger.app import make_app
    app = make_app(f"sqlite:///{tmp_path}/k.db")
    stack = {"X-Rouvr-Token": "stack-secret"}
    with TestClient(app) as c:
        for tenant, user, tok in (("acme", "amy", "amy-tok"), ("globex", "gus", "gus-tok")):
            assert c.post("/v1/tenants", json={"tenant": tenant}, headers=stack).status_code == 200
            assert c.post("/v1/projects", json={"tenant": tenant, "project": "site"},
                          headers=stack).status_code == 200
            h = hashlib.sha256(tok.encode()).hexdigest()
            assert c.post("/v1/users", json={"username": user, "token_hash": h,
                                             "tenant": tenant}, headers=stack).status_code == 200
        yield c, stack, {"X-Rouvr-Token": "amy-tok"}, {"X-Rouvr-Token": "gus-tok"}


def _entry(c, headers, **kw):
    body = {"tenant": "acme", "project": "site", "kind": "decision",
            "title": "Use SQLite", "body": "We chose SQLite for the demo store."}
    body.update(kw)
    return c.post("/v1/knowledge", json=body, headers=headers)


def test_create_search_get_cite(ctx):
    c, stack, amy, _gus = ctx
    r = _entry(c, amy)
    assert r.status_code == 200, r.text
    e = r.json()
    assert (e["version"], e["verified"], e["author"]) == (1, 0, "amy")
    assert e["citation"] == f"knowledge:{e['id']}@v1"
    hits = c.get("/v1/knowledge/search", params={"q": "SQLite"}, headers=amy).json()["items"]
    assert [h["citation"] for h in hits] == [e["citation"]]
    got = c.get(f"/v1/knowledge/{e['id']}", headers=amy)
    assert got.status_code == 200 and got.json()["body"].startswith("We chose")


def test_update_is_new_version_old_kept(ctx):
    c, stack, amy, _gus = ctx
    e1 = _entry(c, amy).json()
    r = c.post(f"/v1/knowledge/{e1['id']}/versions",
               json={"title": "Use SQLite", "body": "Revised: SQLite + FTS5."}, headers=amy)
    assert r.status_code == 200, r.text
    e2 = r.json()
    assert (e2["version"], e2["supersedes_id"]) == (2, e1["id"])
    old = c.get(f"/v1/knowledge/{e1['id']}", headers=amy).json()
    assert old["body"] == "We chose SQLite for the demo store."  # append-only: history intact
    latest = c.get("/v1/knowledge/search", params={"q": "FTS5"}, headers=amy).json()["items"]
    assert [h["citation"] for h in latest] == [e2["citation"]]  # search returns latest version only


def test_history_replays_chain(ctx):
    c, stack, amy, _gus = ctx
    e1 = _entry(c, amy).json()
    e2 = c.post(f"/v1/knowledge/{e1['id']}/versions",
                json={"title": "Use SQLite", "body": "Revised: SQLite + FTS5."}, headers=amy).json()
    hist = c.get(f"/v1/knowledge/{e2['id']}/history", headers=amy).json()["items"]
    assert [(h["id"], h["version"]) for h in hist] == [(e1["id"], 1), (e2["id"], 2)]
    assert hist[1]["citation"] == e2["citation"]


def test_confirm_marks_verified(ctx):
    c, stack, amy, _gus = ctx
    e = _entry(c, amy, kind="rule", title="Rule", body="Always cite sources.").json()
    assert e["verified"] == 0
    r = c.post(f"/v1/knowledge/{e['id']}/confirm", json={}, headers=amy)
    assert r.status_code == 200 and r.json()["verified"] == 1


def test_cross_tenant_canary_never_leaks(ctx):
    c, stack, amy, gus = ctx
    _entry(c, amy, title="CANARY-SECRET-TITLE", body="canary body token xyzzy")
    hits = c.get("/v1/knowledge/search", params={"q": "xyzzy"}, headers=gus).json()["items"]
    assert hits == []
    hits = c.get("/v1/knowledge/search", params={"q": "CANARY"}, headers=gus).json()["items"]
    assert hits == []
    eid = c.get("/v1/knowledge/search", params={"q": "xyzzy"},
                headers=amy).json()["items"][0]["citation"].split("@")[0].split(":")[1]
    assert c.get(f"/v1/knowledge/{eid}", headers=gus).status_code in (403, 404)
    assert c.post(f"/v1/knowledge/{eid}/confirm", json={}, headers=gus).status_code in (403, 404)
    # writing into another tenant is refused even with an explicit scope body
    r = _entry(c, gus)  # body tenant=acme, caller tenant=globex
    assert r.status_code == 403


def test_hostile_search_input_never_500s(ctx):
    c, _stack, amy, _gus = ctx
    _entry(c, amy)
    for q in ['"', "'", "*", "(", ")", ":", "^", "~", "****",
              "'; DROP TABLE knowledge_entries;--", "a OR 1=1"]:
        r = c.get("/v1/knowledge/search", params={"q": q}, headers=amy)
        assert r.status_code in (200, 422), (q, r.status_code)
    n = c.get("/v1/knowledge/search", params={"q": "SQLite"}, headers=amy)
    assert n.status_code == 200 and n.json()["items"]  # store intact


def test_superseded_ids_never_in_current_results(ctx, tmp_path):
    """FL-A6: five v1 rules superseded by five v2 (the live 165/171-174 ->
    267-271 shape: olds carry no supersedes_id, news point at them, all v2
    verified). Olds are confirmed too, so the latest-only filter is the only
    thing excluding them from search/rules/snapshot/curate/compact."""
    c, _stack, amy, _gus = ctx
    olds, news = [], []
    for i in range(5):
        e = c.post("/v1/knowledge", json={
            "tenant": "acme", "project": "site", "kind": "rule",
            "title": f"Quorum rule {i}",
            "body": f"Rule {i} quorum must hold alpha{i}."}, headers=amy).json()
        olds.append(e["id"])
        assert e["supersedes_id"] is None
        assert c.post(f"/v1/knowledge/{e['id']}/confirm", json={},
                      headers=amy).status_code == 200
        v = c.post(f"/v1/knowledge/{e['id']}/versions", json={
            "title": f"Quorum rule {i}",
            "body": f"Rule {i} quorum revised must hold beta{i}."}, headers=amy).json()
        assert v["supersedes_id"] == e["id"]
        news.append(v["id"])
        assert c.post(f"/v1/knowledge/{v['id']}/confirm", json={},
                      headers=amy).status_code == 200
    oldset, newset = set(olds), set(news)

    def _cited(rows):
        return {int(r["citation"].split("@")[0].split(":")[1]) for r in rows}

    def _cited_strs(strs):
        return {int(x.split("@")[0].split(":")[1]) for x in strs}

    # search: a token shared by old+new bodies hits current versions only
    hits = c.get("/v1/knowledge/search", params={"q": "quorum", "n": 100},
                 headers=amy).json()["items"]
    assert _cited(hits) == newset
    # search: an old-body-only token never surfaces an old id
    hits = c.get("/v1/knowledge/search", params={"q": "alpha0", "n": 100},
                 headers=amy).json()["items"]
    assert not (_cited(hits) & oldset)
    # rules: verified current rules only
    rules = c.get("/v1/knowledge-rules", params={"project": "site"},
                  headers=amy).json()
    assert _cited_strs(rules["citations"]) == newset
    # snapshot: citations and the file on disk carry current ids only
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    _prev = os.environ.get("ROUVR_DATA_DIR")
    os.environ["ROUVR_DATA_DIR"] = str(snap_dir)
    try:
        snap = c.post("/v1/knowledge/snapshot",
                      json={"tenant": "acme", "project": "site"}, headers=amy).json()
    finally:
        if _prev is None:
            os.environ.pop("ROUVR_DATA_DIR", None)
        else:
            os.environ["ROUVR_DATA_DIR"] = _prev
    assert _cited_strs(snap["citations"]) == newset
    body = Path(snap["path"]).read_text()
    for o in olds:
        assert f"knowledge:{o}@" not in body
    # compact: sources and body cite current ids only
    comp = c.post("/v1/knowledge/compact",
                  json={"tenant": "acme", "project": "site"}, headers=amy).json()
    assert not (_cited_strs(comp["sources"]) & oldset)
    assert newset <= _cited_strs(comp["sources"])
    for o in olds:
        assert f"knowledge:{o}@v" not in comp["snapshot"]["body"]
    # curate: old+new share titles, so any leaked old would flag a duplicate
    cur = c.post("/v1/knowledge/curate",
                 json={"tenant": "acme", "project": "site"}, headers=amy).json()
    refd = {e for s in cur["suggestions"] for e in s["entries"]}
    assert not (refd & oldset)
    assert not any(s["type"] == "duplicate" for s in cur["suggestions"])


def test_scope_and_kind_validation(ctx):
    c, stack, amy, _gus = ctx
    assert c.post("/v1/knowledge", json={"kind": "note", "title": "t", "body": "b"},
                  headers=amy).status_code == 422  # no scope, no default
    assert _entry(c, amy, kind="bogus").status_code == 422
    assert _entry(c, amy, project="ghost").status_code == 404
    assert _entry(c, amy, title=" ").status_code == 422
