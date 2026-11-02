"""Knowledge id regression (FL-A6): superseded ids never surface as current.

Seeds the live chain shape (v1 rows with no supersedes_id, v2 rows pointing
at them, every v2 verified) and checks search, verified rules, snapshot plus
its artifact file, curate suggestions, and direct id lookup: current ids
only. History keeps the full chain.
"""
import hashlib
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

CANARY = "regresscanary"


def _env(tmp_path):
    """Isolated settings for one file-backed app; restored by _restore."""
    saved = {k: os.environ.get(k) for k in
             ("ROUVR_LEDGER_TOKEN", "ROUVR_INSECURE_DEV", "ROUVR_DATA_DIR")}
    os.environ["ROUVR_LEDGER_TOKEN"] = "stack-secret"
    os.environ.pop("ROUVR_INSECURE_DEV", None)
    os.environ["ROUVR_DATA_DIR"] = str(tmp_path / "data")
    return saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _titles(i):
    if i < 2:
        return "Shared deploy rule"  # identical pair: curate sees one duplicate
    return f"Deploy rule {i} {CANARY}"


def _seed(c, amy):
    old_ids, new_ids = [], []
    for i in range(5):
        v1 = c.post("/v1/knowledge", json={
            "tenant": "acme", "project": "site", "kind": "rule",
            "title": _titles(i),
            "body": f"Always verify deploy step {i} {CANARY}."}, headers=amy).json()
        assert c.post(f"/v1/knowledge/{v1['id']}/confirm",
                      json={}, headers=amy).status_code == 200
        v2 = c.post(f"/v1/knowledge/{v1['id']}/versions", json={
            "title": _titles(i),
            "body": f"Always verify deploy step {i} twice {CANARY}."},
            headers=amy).json()
        assert (v2["version"], v2["supersedes_id"]) == (2, v1["id"])
        assert c.post(f"/v1/knowledge/{v2['id']}/confirm",
                      json={}, headers=amy).status_code == 200
        old_ids.append(v1["id"])
        new_ids.append(v2["id"])
    return old_ids, new_ids


def test_superseded_ids_never_in_current_results(tmp_path):
    saved = _env(tmp_path)
    try:
        from ledger.app import make_app
        app = make_app(f"sqlite:///{tmp_path}/regress.db")
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
            amy = {"X-Rouvr-Token": "amy-tok"}
            old_ids, new_ids = _seed(c, amy)
            old, new = set(old_ids), set(new_ids)

            hits = c.get("/v1/knowledge/search",
                         params={"q": CANARY, "n": 100, "full": "true"},
                         headers=amy).json()["items"]
            got = {x["id"] for x in hits}
            assert not (got & old), got & old
            assert got == new, (got, new)

            short = c.get("/v1/knowledge/search", params={"q": CANARY, "n": 100},
                          headers=amy).json()["items"]
            cites = {x["citation"] for x in short}
            assert cites == {f"knowledge:{i}@v2" for i in new}, cites

            rules = c.get("/v1/knowledge-rules", headers=amy).json()
            rids = {r["id"] for r in rules["items"]}
            assert not (rids & old), rids & old
            assert rids == new, (rids, new)

            snap = c.post("/v1/knowledge/snapshot",
                          json={"tenant": "acme", "project": "site"},
                          headers=amy).json()
            assert snap["count"] == 5, snap
            sids = {int(x.split(":")[1].split("@")[0]) for x in snap["citations"]}
            assert sids == new, (sids, new)
            artifact = json.loads(Path(snap["path"]).read_text())
            aids = {r["id"] for r in artifact["items"]}
            assert aids == new, (aids, new)
            blob = json.dumps(snap) + Path(snap["path"]).read_text()
            for oid in old:
                assert f"knowledge:{oid}@" not in blob, oid

            cur = c.post("/v1/knowledge/curate",
                         json={"tenant": "acme", "project": "site"},
                         headers=amy).json()
            assert cur["count"] >= 1, cur  # duplicate pair: latest rows scanned
            for s in cur["suggestions"]:
                assert not (set(s["entries"]) & old), s
                assert set(s["entries"]) <= new, s

            for oid, nid in zip(old_ids, new_ids):
                r = c.get(f"/v1/knowledge/{oid}", headers=amy)
                assert r.status_code == 200, (oid, r.status_code)
                assert (r.json()["id"], r.json()["version"]) == (nid, 2)

            hist = c.get(f"/v1/knowledge/{new_ids[0]}/history",
                         headers=amy).json()["items"]
            assert [(x["id"], x["version"]) for x in hist] == [
                (old_ids[0], 1), (new_ids[0], 2)]
    finally:
        _restore(saved)
