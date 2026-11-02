"""Knowledge Lite fuzzy search + slug/shortlist contract.

Typo ("curatr"), partial ("type hin"), near-miss ("idmpotent"), slug
lookup, shortlist shape/size, cross-tenant zero leaks, slug stability
across versions, and explicit-slug collisions.
"""
import hashlib

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
    body = {"tenant": "acme", "project": "site", "kind": "note",
            "title": "t", "body": "b"}
    body.update(kw)
    return c.post("/v1/knowledge", json=body, headers=headers)


def test_typo_curatr_finds_curator(ctx):
    c, _stack, amy, _gus = ctx
    e = _entry(c, amy, title="Curator flags contradictions",
               body="The curator lists duplicates and stale entries.").json()
    hits = c.get("/v1/knowledge/search", params={"q": "curatr"},
                 headers=amy).json()["items"]
    assert [h["citation"] for h in hits] == [e["citation"]]


def test_partial_type_hin_matches_type_hints(ctx):
    c, _stack, amy, _gus = ctx
    e = _entry(c, amy, kind="rule", title="All functions need type hints",
               body="Every function signature carries annotations.").json()
    hits = c.get("/v1/knowledge/search", params={"q": "type hin"},
                 headers=amy).json()["items"]
    assert e["citation"] in [h["citation"] for h in hits]


def test_trigram_reach_idmpotent(ctx):
    c, _stack, amy, _gus = ctx
    e = _entry(c, amy, title="Retry policy",
               body="Retries must be idempotent or carry idempotency keys.").json()
    # "idmpotent" is not a prefix of anything stored: only the trigram
    # index (shared interior trigrams) can reach it.
    hits = c.get("/v1/knowledge/search", params={"q": "idmpotent"},
                 headers=amy).json()["items"]
    assert [h["citation"] for h in hits] == [e["citation"]]


def test_synonymish_wordform_idempotence(ctx):
    c, _stack, amy, _gus = ctx
    e = _entry(c, amy, title="Retry policy",
               body="Retries must be idempotent or carry idempotency keys.").json()
    # "idempotence" shares the stem trigrams but is neither a prefix nor a
    # full trigram cover: only the overlap expansion reaches it.
    hits = c.get("/v1/knowledge/search", params={"q": "idempotence"},
                 headers=amy).json()["items"]
    assert [h["citation"] for h in hits] == [e["citation"]]


def test_porter_stemming_contradiction_matches_contradictions(ctx):
    c, _stack, amy, _gus = ctx
    e = _entry(c, amy, title="Curator flags contradictions",
               body="The curator lists duplicates.").json()
    hits = c.get("/v1/knowledge/search", params={"q": "contradiction"},
                 headers=amy).json()["items"]
    assert [h["citation"] for h in hits] == [e["citation"]]


def test_semantic_layer_is_honest_stub():
    from ledger.app import _semantic_candidates
    assert _semantic_candidates("anything at all") == []


def test_verified_outranks_unverified_near_tie(ctx):
    c, _stack, amy, _gus = ctx
    a = _entry(c, amy, title="Common probe entry",
               body="Shared wording for the ranking probe.").json()
    b = _entry(c, amy, title="Common probe entry",
               body="Shared wording for the ranking probe.").json()
    assert c.post(f"/v1/knowledge/{a['id']}/confirm", json={},
                  headers=amy).status_code == 200
    hits = c.get("/v1/knowledge/search", params={"q": "common probe"},
                 headers=amy).json()["items"]
    assert [h["citation"] for h in hits] == [a["citation"], b["citation"]]


def test_slug_lookup_and_stability(ctx):
    c, _stack, amy, _gus = ctx
    e = _entry(c, amy, slug="type-hints", title="Type hints",
               body="All functions need type hints.").json()
    assert e["slug"] == "type-hints"
    by_slug = c.get("/v1/knowledge/type-hints", headers=amy).json()
    assert by_slug["id"] == e["id"]
    v2 = c.post(f"/v1/knowledge/{e['id']}/versions",
                json={"title": "Type hints everywhere",
                      "body": "All functions need type hints, no exceptions."},
                headers=amy).json()
    assert (v2["version"], v2["slug"]) == (2, "type-hints")  # slug never moves
    again = c.get("/v1/knowledge/type-hints", headers=amy).json()
    assert (again["id"], again["version"]) == (v2["id"], 2)  # slug tracks latest
    hist = c.get(f"/v1/knowledge/{v2['id']}/history", headers=amy).json()["items"]
    assert [(h["version"], h["slug"]) for h in hist] == [(1, "type-hints"), (2, "type-hints")]


def test_shortlist_shape_and_default_n(ctx):
    c, _stack, amy, _gus = ctx
    for i in range(20):
        _entry(c, amy, title=f"Shortlist probe {i:02d}",
               body=f"filler entry number {i} for shortlist sizing")
    hits = c.get("/v1/knowledge/search", params={"q": "shortlist"},
                 headers=amy).json()["items"]
    assert len(hits) == 15  # default top N
    assert all(set(h) == {"slug", "title", "kind", "summary", "verified", "citation"}
               for h in hits)
    assert all(len(h["summary"]) <= 160 and "\n" not in h["summary"] for h in hits)
    five = c.get("/v1/knowledge/search", params={"q": "shortlist", "n": 5},
                 headers=amy).json()["items"]
    assert len(five) == 5
    full = c.get("/v1/knowledge/search",
                 params={"q": "shortlist", "n": 1, "full": "true"},
                 headers=amy).json()["items"]
    assert full and "body" in full[0] and "allowed_users" in full[0]


def test_cross_tenant_slug_and_search_zero_leaks(ctx):
    c, _stack, amy, gus = ctx
    e = _entry(c, amy, slug="acme-secret", title="CANARY acme only",
               body="canary body token xyzzy").json()
    assert c.get("/v1/knowledge/search", params={"q": "xyzzy"},
                 headers=gus).json()["items"] == []
    assert c.get("/v1/knowledge/acme-secret", headers=gus).status_code == 404
    assert c.get(f"/v1/knowledge/{e['id']}", headers=gus).status_code == 404


def test_explicit_slug_collision_is_409_auto_suffixes(ctx):
    c, _stack, amy, _gus = ctx
    assert _entry(c, amy, slug="dupe", title="First", body="one").status_code == 200
    r = _entry(c, amy, slug="dupe", title="Second", body="two")
    assert r.status_code == 409
    a = _entry(c, amy, title="Same title", body="one").json()
    b = _entry(c, amy, title="Same title", body="two").json()
    assert (a["slug"], b["slug"]) == ("same-title", "same-title-2")
    assert c.get("/v1/knowledge/same-title-2", headers=amy).json()["id"] == b["id"]
    long_slug = "x" * 61
    assert _entry(c, amy, slug=long_slug, title="Long", body="b").status_code == 422
    assert _entry(c, amy, slug="!!!", title=" Punct ", body="b").status_code == 422


def test_legacy_db_migrates_in_place(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    dbp = tmp_path / "legacy.db"
    con = sqlite3.connect(dbp)
    con.execute("CREATE TABLE knowledge_entries (id INTEGER PRIMARY KEY,"
                " tenant TEXT NOT NULL, project TEXT NOT NULL, kind TEXT NOT NULL,"
                " title TEXT NOT NULL, body TEXT NOT NULL,"
                " version INTEGER NOT NULL DEFAULT 1,"
                " supersedes_id INTEGER REFERENCES knowledge_entries(id),"
                " verified INTEGER NOT NULL DEFAULT 0, author TEXT NOT NULL,"
                " visibility TEXT NOT NULL DEFAULT 'company',"
                " allowed_users TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL)")
    con.execute("CREATE VIRTUAL TABLE knowledge_fts USING fts5(title, body)")
    con.execute("INSERT INTO knowledge_entries(tenant,project,kind,title,body,version,"
                "verified,author,visibility,allowed_users,created_at)"
                " VALUES('acme','site','rule','All functions need type hints',"
                "'Every function carries annotations.',1,1,'amy','company','[]',"
                "'2026-01-01T00:00:00+00:00')")
    con.execute("INSERT INTO knowledge_fts(rowid,title,body)"
                " VALUES(1,'All functions need type hints','Every function carries annotations.')")
    con.commit()
    con.close()
    from ledger.app import make_app
    app = make_app(f"sqlite:///{dbp}")
    with TestClient(app) as c:
        stack = {"X-Rouvr-Token": "stack-secret"}
        e = c.get("/v1/knowledge/1", headers=stack).json()
        assert e["slug"] == "all-functions-need-type-hints"
        assert e["tags"] == ["rule"] and e["summary"] == "Every function carries annotations."
        assert (e["verified"], e["version"]) == (1, 1)  # in place: no new version minted
        hits = c.get("/v1/knowledge/search", params={"q": "type hin"},
                     headers=stack).json()["items"]
        assert [h["slug"] for h in hits] == ["all-functions-need-type-hints"]


def test_tags_summary_roundtrip(ctx):
    c, _stack, amy, _gus = ctx
    e = _entry(c, amy, title="Tagged entry", body="First line summary.\nSecond line.",
               tags=["workflow", "demo"], summary="Custom one-liner").json()
    assert e["tags"] == ["workflow", "demo"]
    assert e["summary"] == "Custom one-liner"
    auto = _entry(c, amy, title="Auto fields", body="Derived summary here.\nrest").json()
    assert auto["tags"] == ["note"] and auto["summary"] == "Derived summary here."
