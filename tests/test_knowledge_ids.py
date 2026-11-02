"""Knowledge id regression (FL-A6): superseded ids never surface in current reads.

Seeds the production chain shape (five v1 entries superseded by five v2
entries, both generations verified) and proves the old ids appear in no
current-result path: search, knowledge-rules, snapshot (response and file),
curate, compact sources, and direct id lookup, which resolves forward to
the live version. History still replays the full chain (append-only).
"""
import hashlib
import json
import os
import re
from pathlib import Path

from fastapi.testclient import TestClient

SHARED = "kaviaq"


def _cite_id(citation):
    return int(citation.split(":")[1].split("@")[0])


def _app(tmp_path):
    from ledger.app import make_app
    prev = os.environ.get("ROUVR_LEDGER_TOKEN")
    os.environ["ROUVR_LEDGER_TOKEN"] = "stack-secret"
    try:
        return make_app(f"sqlite:///{tmp_path}/k.db")
    finally:
        if prev is None:
            del os.environ["ROUVR_LEDGER_TOKEN"]
        else:
            os.environ["ROUVR_LEDGER_TOKEN"] = prev


def test_superseded_ids_absent_from_current_results(tmp_path):
    app = _app(tmp_path)
    stack = {"X-Rouvr-Token": "stack-secret"}
    with TestClient(app) as c:
        h = hashlib.sha256(b"amy-tok").hexdigest()
        assert c.post("/v1/tenants", json={"tenant": "acme"}, headers=stack).status_code == 200
        assert c.post("/v1/projects", json={"tenant": "acme", "project": "site"},
                      headers=stack).status_code == 200
        assert c.post("/v1/users", json={"username": "amy", "token_hash": h,
                                         "tenant": "acme"}, headers=stack).status_code == 200
        amy = {"X-Rouvr-Token": "amy-tok"}
        kinds = ["rule", "rule", "decision", "note", "decision"]
        old_ids, new_ids = [], []
        for i in range(5):
            title = "Shared probe title" if i < 2 else f"Probe {i} title"
            v1 = c.post("/v1/knowledge", json={
                "tenant": "acme", "project": "site", "kind": kinds[i],
                "title": title, "body": f"legacy wording {SHARED}-old-{i} keeps history"},
                headers=amy)
            assert v1.status_code == 200, v1.text
            e1 = v1.json()
            assert c.post(f"/v1/knowledge/{e1['id']}/confirm", json={},
                          headers=amy).status_code == 200
            v2 = c.post(f"/v1/knowledge/{e1['id']}/versions", json={
                "title": title, "body": f"current wording {SHARED}-new-{i} is live"},
                headers=amy)
            assert v2.status_code == 200, v2.text
            e2 = v2.json()
            assert (e2["version"], e2["supersedes_id"]) == (2, e1["id"])
            assert c.post(f"/v1/knowledge/{e2['id']}/confirm", json={},
                          headers=amy).status_code == 200
            old_ids.append(e1["id"])
            new_ids.append(e2["id"])
        old, new = set(old_ids), set(new_ids)
        # search: shared current term finds exactly the live generation
        hits = c.get("/v1/knowledge/search", params={"q": SHARED, "n": 50},
                     headers=amy).json()["items"]
        assert {_cite_id(x["citation"]) for x in hits} == new
        # search: even an old-only term never surfaces a superseded row
        stale = c.get("/v1/knowledge/search", params={"q": f"{SHARED}-old-0 legacy", "n": 50},
                      headers=amy).json()["items"]
        assert not ({_cite_id(x["citation"]) for x in stale} & old)
        # rules: verified rules list only the live rule generation
        rules = c.get("/v1/knowledge-rules", params={"project": "site"}, headers=amy).json()
        assert {_cite_id(x) for x in rules["citations"]} == {new_ids[0], new_ids[1]}
        # snapshot: response and artifact file carry live ids only
        snap = c.post("/v1/knowledge/snapshot",
                      json={"tenant": "acme", "project": "site"}, headers=amy).json()
        assert {_cite_id(x) for x in snap["citations"]} == new
        assert snap["count"] == 5
        spath = Path(snap["path"])
        try:
            doc = json.loads(spath.read_text())
            assert {e["id"] for e in doc["items"]} == new
        finally:
            if spath.exists():
                spath.unlink()
            for parent in (spath.parent, spath.parent.parent):
                try:
                    parent.rmdir()
                except OSError:
                    break
        # curate: the shared-title duplicate is flagged on live ids, and no
        # suggestion entry or knowledge reference names a superseded id
        cur = c.post("/v1/knowledge/curate",
                     json={"tenant": "acme", "project": "site"}, headers=amy).json()
        dupes = [s for s in cur["suggestions"] if s.get("type") == "duplicate"]
        assert dupes
        assert {new_ids[0], new_ids[1]} in [set(s["entries"]) for s in dupes]
        for s in cur["suggestions"]:
            assert not (set(s.get("entries", [])) & old)
        for m in re.finditer(r"knowledge:(\d+)", json.dumps(cur)):
            assert int(m.group(1)) not in old
        # id lookup: an old id resolves forward to the live version
        for oid, nid in zip(old_ids, new_ids):
            got = c.get(f"/v1/knowledge/{oid}", headers=amy)
            assert got.status_code == 200, got.text
            assert got.json()["id"] == nid
            assert got.json()["version"] == 2
        # history still replays the full append-only chain
        hist = c.get(f"/v1/knowledge/{new_ids[0]}/history", headers=amy).json()["items"]
        assert [(x["id"], x["version"]) for x in hist] == [(old_ids[0], 1), (new_ids[0], 2)]
        # compact sources cite the live generation only
        comp = c.post("/v1/knowledge/compact",
                      json={"tenant": "acme", "project": "site", "title": "Probe compact"},
                      headers=amy).json()
        assert {_cite_id(x) for x in comp["sources"]} == new
