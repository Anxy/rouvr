"""A6 regression: superseded ids never surface in current results.

Live chain (2026-11-02): 165/171-174 superseded by 267-271 (v2 verified).
Seeds the same shape (5 old -> 5 new via versions, both verified so the
snapshot/rules paths are meaningfully exercised) and asserts old ids are
absent from search, knowledge-rules, snapshot (+compact), and curate,
while the new ids remain current.
"""
import hashlib
import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    monkeypatch.setenv("ROUVR_DATA_DIR", str(tmp_path / "data"))
    from ledger.app import make_app
    app = make_app(f"sqlite:///{tmp_path}/a6.db")
    stack = {"X-Rouvr-Token": "stack-secret"}
    with TestClient(app) as c:
        assert c.post("/v1/tenants", json={"tenant": "acme"}, headers=stack).status_code == 200
        assert c.post("/v1/projects", json={"tenant": "acme", "project": "site"},
                      headers=stack).status_code == 200
        h = hashlib.sha256(b"amy-tok").hexdigest()
        assert c.post("/v1/users", json={"username": "amy", "token_hash": h,
                                         "tenant": "acme"}, headers=stack).status_code == 200
        yield c, {"X-Rouvr-Token": "amy-tok"}


def _ids_from_citations(items):
    out = set()
    for it in items:
        cit = it.get("citation", "") if isinstance(it, dict) else str(it)
        m = re.match(r"knowledge:(\d+)@v\d+", cit)
        if m:
            out.add(int(m.group(1)))
    return out


def _seed_chain(c, amy):
    """5 old entries (mix of rule/decision, all verified) each superseded
    by a new version (also verified). Returns (old_ids, new_ids)."""
    olds, news = [], []
    for i in range(5):
        kind = "rule" if i % 2 == 0 else "decision"
        title = f"A6 probe entry {i} needle-a6-{i}"
        body = f"Standing instruction {i}: always cite needle-a6-{i} sources."
        e1 = c.post("/v1/knowledge", json={
            "tenant": "acme", "project": "site", "kind": kind,
            "title": title, "body": body}, headers=amy).json()
        assert c.post(f"/v1/knowledge/{e1['id']}/confirm", json={}, headers=amy).status_code == 200
        e2 = c.post(f"/v1/knowledge/{e1['id']}/versions", json={
            "title": title, "body": body + " Revised v2."}, headers=amy).json()
        assert e2["supersedes_id"] == e1["id"]
        assert c.post(f"/v1/knowledge/{e2['id']}/confirm", json={}, headers=amy).status_code == 200
        olds.append(e1["id"])
        news.append(e2["id"])
    return olds, news


def test_superseded_ids_never_in_current_results(ctx):
    c, amy = ctx
    olds, news = _seed_chain(c, amy)
    old, new = set(olds), set(news)

    # search (shortlist + full): old absent, new present
    for params in ({"q": "needle-a6", "n": 50},
                   {"q": "needle-a6", "n": 50, "full": "true"}):
        hits = c.get("/v1/knowledge/search", params=params, headers=amy).json()["items"]
        got = _ids_from_citations(hits)
        assert not (got & old), f"search leaked superseded ids: {got & old}"
        assert new <= got, f"search missing current ids: {new - got}"

    # rules: old absent, current rules present
    rules = c.get("/v1/knowledge-rules", params={"project": "site"}, headers=amy).json()
    got = _ids_from_citations(rules["items"])
    assert not (got & old), f"rules leaked superseded ids: {got & old}"
    current_rules = {nid for nid, i in zip(news, range(5)) if i % 2 == 0}
    assert current_rules <= got, f"rules missing current ids: {current_rules - got}"
    for cit in rules.get("citations", []):
        assert not any(cit.startswith(f"knowledge:{o}@") for o in olds), cit

    # snapshot: old absent, new present
    snap = c.post("/v1/knowledge/snapshot",
                  json={"tenant": "acme", "project": "site"}, headers=amy).json()
    got = _ids_from_citations(snap["items"] if "items" in snap else []) | _ids_from_citations(
        [{"citation": x} for x in snap.get("citations", [])])
    assert not (got & old), f"snapshot leaked superseded ids: {got & old}"
    assert new <= got, f"snapshot missing current ids: {new - got}"

    # compact: same latest-only contract as snapshot
    comp = c.post("/v1/knowledge/compact",
                  json={"tenant": "acme", "project": "site"}, headers=amy).json()
    got = _ids_from_citations([{"citation": x} for x in comp.get("sources", [])])
    assert not (got & old), f"compact leaked superseded ids: {got & old}"
    assert new <= got, f"compact missing current ids: {new - got}"

    # curate: no suggestion may reference a superseded id
    cur = c.post("/v1/knowledge/curate",
                 json={"tenant": "acme", "project": "site"}, headers=amy).json()
    for s in cur.get("suggestions", []):
        refs = set(s.get("entries", []))
        assert not (refs & old), f"curate leaked superseded ids: {refs & old}"
        blob = (s.get("note", "") or "") + (s.get("proposal", "") or "")
        assert not any(f"knowledge:{o}" in blob for o in olds), blob
