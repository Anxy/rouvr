"""FL-A6 regression: superseded knowledge ids never surface in current results.

Live chain (2026-11-02): 165/171-174 superseded by 267-271, all v2 verified.
Seeds the same shape (five v1 entries, each superseded by a v2; both sides
verified so the latest-only filter is the only thing excluding the olds) and
asserts the old ids never appear across search, knowledge-rules, snapshot
(response citations plus the file on disk), compact sources, and curate,
while the new ids remain current.

Setup uses a file-backed app on tmp_path with the insecure local-dev auth
bypass captured at make_app time; the snapshot file is read back from the
path in the response and removed, with no data-dir redirect.
"""
import os
from pathlib import Path

from fastapi.testclient import TestClient


def _client(tmp_path):
    saved_insecure = os.environ.get("ROUVR_INSECURE_DEV")
    saved_token = os.environ.get("ROUVR_LEDGER_TOKEN")
    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)
    try:
        from ledger.app import make_app
        app = make_app(f"sqlite:///{tmp_path}/a6.db")
    finally:
        if saved_insecure is None:
            os.environ.pop("ROUVR_INSECURE_DEV", None)
        else:
            os.environ["ROUVR_INSECURE_DEV"] = saved_insecure
        if saved_token is None:
            os.environ.pop("ROUVR_LEDGER_TOKEN", None)
        else:
            os.environ["ROUVR_LEDGER_TOKEN"] = saved_token
    c = TestClient(app)
    assert c.post("/v1/tenants", json={"tenant": "acme"}).status_code == 200
    assert c.post("/v1/projects", json={"tenant": "acme", "project": "site"}).status_code == 200
    return c


def _cited_id(citation):
    return int(citation.split("@")[0].split(":")[1])


def test_superseded_ids_never_in_current_results(tmp_path):
    c = _client(tmp_path)
    olds, news = [], []
    for i in range(5):
        r = c.post("/v1/knowledge", json={
            "tenant": "acme", "project": "site", "kind": "rule",
            "title": f"Quorum rule {i}",
            "body": f"Rule {i} quorum must hold alpha{i}."})
        assert r.status_code == 200, r.text
        e = r.json()
        olds.append(e["id"])
        assert e["supersedes_id"] is None
        assert c.post(f"/v1/knowledge/{e['id']}/confirm", json={}).status_code == 200
        r = c.post(f"/v1/knowledge/{e['id']}/versions", json={
            "title": f"Quorum rule {i}",
            "body": f"Rule {i} quorum revised must hold beta{i}."})
        assert r.status_code == 200, r.text
        v = r.json()
        assert v["supersedes_id"] == e["id"]
        news.append(v["id"])
        assert c.post(f"/v1/knowledge/{v['id']}/confirm", json={}).status_code == 200
    oldset, newset = set(olds), set(news)

    # search: a token shared by old+new bodies hits current versions only
    for params in ({"q": "quorum", "n": 100},
                   {"q": "quorum", "n": 100, "full": "true"}):
        hits = c.get("/v1/knowledge/search", params=params).json()["items"]
        assert {_cited_id(h["citation"]) for h in hits} == newset
    # search: an old-body-only token never surfaces an old id
    hits = c.get("/v1/knowledge/search", params={"q": "alpha0", "n": 100}).json()["items"]
    assert not ({_cited_id(h["citation"]) for h in hits} & oldset)

    # rules: verified current rules only
    rules = c.get("/v1/knowledge-rules", params={"project": "site"}).json()
    assert {_cited_id(x) for x in rules["citations"]} == newset

    # snapshot: citations and the file on disk carry current ids only
    snap = c.post("/v1/knowledge/snapshot",
                  json={"tenant": "acme", "project": "site"}).json()
    try:
        assert {_cited_id(x) for x in snap["citations"]} == newset
        body = Path(snap["path"]).read_text()
        for o in olds:
            assert f"knowledge:{o}@" not in body
        for n in news:
            assert f"knowledge:{n}@" in body
    finally:
        Path(snap["path"]).unlink(missing_ok=True)

    # compact: same latest-only contract as snapshot
    comp = c.post("/v1/knowledge/compact",
                  json={"tenant": "acme", "project": "site"}).json()
    assert {_cited_id(x) for x in comp["sources"]} == newset

    # curate: old+new share titles, so any leaked old would flag a duplicate
    cur = c.post("/v1/knowledge/curate",
                 json={"tenant": "acme", "project": "site"}).json()
    refd = {e for s in cur["suggestions"] for e in s["entries"]}
    assert not (refd & oldset)
    assert not any(s["type"] == "duplicate" for s in cur["suggestions"])
