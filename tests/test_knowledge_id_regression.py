"""A6 knowledge id regression: superseded ids never in current results.

Mirrors the live 165/171-174 -> 267-271 shape from
docs/plans/2026-11-02-frontend-lite.md: five v1 entries superseded by five
v2 entries (all v2 verified). Every current-result path -- search, rules,
snapshot, curate, id lookup -- must surface only the current ids, while
/history still replays the full append-only chain.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

CANARY = "zxqregress"
KINDS = ["rule", "note", "decision", "note", "rule"]


@pytest.fixture()
def live_app(tmp_path):
    """File-backed ledger app with real process config, restored after."""
    saved = {k: os.environ.get(k) for k in (
        "ROUVR_LEDGER_TOKEN", "ROUVR_INSECURE_DEV", "ROUVR_DATA_DIR")}
    os.environ["ROUVR_LEDGER_TOKEN"] = "stack-secret"
    os.environ.pop("ROUVR_INSECURE_DEV", None)
    os.environ["ROUVR_DATA_DIR"] = str(tmp_path / "data")
    try:
        from ledger.app import make_app
        yield make_app(f"sqlite:///{tmp_path}/id-regression.db")
    finally:
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


def _seed(app):
    stack = {"X-Rouvr-Token": "stack-secret"}
    with TestClient(app) as c:
        assert c.post("/v1/tenants", json={"tenant": "acme"},
                      headers=stack).status_code == 200
        assert c.post("/v1/projects", json={"tenant": "acme", "project": "site"},
                      headers=stack).status_code == 200
        h = hashlib.sha256(b"amy-tok").hexdigest()
        assert c.post("/v1/users", json={"username": "amy", "token_hash": h,
                                         "tenant": "acme"},
                      headers=stack).status_code == 200
    return stack, {"X-Rouvr-Token": "amy-tok"}


def _cite_id(citation):
    return int(citation.split("@")[0].split(":")[1])


def test_superseded_ids_never_in_current_results(live_app):
    stack, amy = _seed(live_app)
    with TestClient(live_app) as c:
        old_ids, new_ids = [], []
        for i, kind in enumerate(KINDS):
            # entries 0 and 1 share a title so curate has a duplicate pair
            title = (f"{CANARY} shared title" if i < 2
                     else f"{CANARY} entry {i} title")
            e1 = c.post("/v1/knowledge", json={
                "tenant": "acme", "project": "site", "kind": kind,
                "title": title, "body": f"{CANARY} body {i} original"},
                headers=amy).json()
            assert e1["version"] == 1
            old_ids.append(e1["id"])
        for i, eid in enumerate(old_ids):
            title = (f"{CANARY} shared title" if i < 2
                     else f"{CANARY} entry {i} title")
            e2 = c.post(f"/v1/knowledge/{eid}/versions", json={
                "title": title, "body": f"{CANARY} body {i} revised"},
                headers=amy).json()
            assert (e2["version"], e2["supersedes_id"]) == (2, eid)
            assert c.post(f"/v1/knowledge/{e2['id']}/confirm", json={},
                          headers=amy).status_code == 200
            new_ids.append(e2["id"])
        old, new = set(old_ids), set(new_ids)

        # search (shortlist + full) returns latest versions only
        short = c.get("/v1/knowledge/search", params={"q": CANARY, "n": 100},
                      headers=amy).json()["items"]
        assert {_cite_id(h["citation"]) for h in short} == new
        full = c.get("/v1/knowledge/search",
                     params={"q": CANARY, "n": 100, "full": "true"},
                     headers=amy).json()["items"]
        assert {h["id"] for h in full} == new
        assert all(h["version"] == 2 for h in full)

        # rules returns current verified rules only
        rules = c.get("/v1/knowledge-rules", params={"project": "site"},
                      headers=amy).json()
        rule_new = {nid for nid, kind in zip(new_ids, KINDS) if kind == "rule"}
        assert {r["id"] for r in rules["items"]} == rule_new
        assert not ({_cite_id(x) for x in rules["citations"]} & old)

        # snapshot cites current rows only, on disk too
        snap = c.post("/v1/knowledge/snapshot",
                      json={"tenant": "acme", "project": "site"},
                      headers=amy).json()
        assert snap["count"] == 5
        assert {_cite_id(x) for x in snap["citations"]} == new
        disk = json.loads(Path(snap["path"]).read_text())
        assert {e["id"] for e in disk["items"]} == new

        # curate references current ids only
        cur = c.post("/v1/knowledge/curate",
                     json={"tenant": "acme", "project": "site"},
                     headers=amy).json()
        blob = json.dumps(cur)
        for oid in old:
            assert f"knowledge:{oid}@v" not in blob
        seen: set = set()
        for s in cur["suggestions"]:
            seen.update(i for i in s.get("entries", []) if isinstance(i, int))
        assert not (seen & old)
        assert {new_ids[0], new_ids[1]} <= seen  # forced duplicate pair

        # id lookup resolves a superseded id forward to the current row
        for oid, nid in zip(old_ids, new_ids):
            got = c.get(f"/v1/knowledge/{oid}", headers=amy)
            assert got.status_code == 200
            assert (got.json()["id"], got.json()["version"]) == (nid, 2)
            assert got.json()["citation"] == f"knowledge:{nid}@v2"

        # history still replays the append-only chain
        hist = c.get(f"/v1/knowledge/{new_ids[0]}/history",
                     headers=amy).json()["items"]
        assert [(h["id"], h["version"]) for h in hist] == [
            (old_ids[0], 1), (new_ids[0], 2)]
