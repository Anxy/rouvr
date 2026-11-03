"""Knowledge id regression (FL-A6): superseded ids never surface as current.

Mirrors the live chain shape (old 165/171-174 superseded by current
267-271, new versions verified): five v1 rule entries each superseded by
a verified v2. Search, verified rules, snapshot and curate must expose
only the current ids; direct id lookup resolves a superseded id forward
to its current version; history still replays the full chain.
"""
import os
import re


def test_superseded_ids_never_in_current_results(tmp_path):
    prev_insecure = os.environ.get("ROUVR_INSECURE_DEV")
    prev_token = os.environ.get("ROUVR_LEDGER_TOKEN")
    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)
    try:
        from fastapi.testclient import TestClient

        from ledger.app import make_app

        app = make_app(f"sqlite:///{tmp_path}/ids.db")
        with TestClient(app) as c:
            assert c.post("/v1/tenants", json={"tenant": "acme"}).status_code == 200
            assert c.post("/v1/projects", json={"tenant": "acme", "project": "site"}).status_code == 200
            old_ids, new_ids = [], []
            for i in range(5):
                title = f"Regression decree {chr(65 + i)} regresscanary"
                body = f"Old body {i} regresscanary: agents must cite sources."
                e = c.post("/v1/knowledge", json={
                    "tenant": "acme", "project": "site", "kind": "rule",
                    "title": title, "body": body}).json()
                assert e["version"] == 1 and e["supersedes_id"] is None
                old_ids.append(e["id"])
                assert c.post(f"/v1/knowledge/{e['id']}/confirm", json={}).status_code == 200
            for i, oid in enumerate(old_ids):
                v2 = c.post(f"/v1/knowledge/{oid}/versions", json={
                    "title": f"Regression decree {chr(65 + i)} regresscanary",
                    "body": f"New body {i} regresscanary v2: agents must cite sources."}).json()
                assert (v2["version"], v2["supersedes_id"]) == (2, oid)
                assert c.post(f"/v1/knowledge/{v2['id']}/confirm", json={}).status_code == 200
                new_ids.append(v2["id"])
            old_set, new_set = set(old_ids), set(new_ids)

            def _cited_ids(citations):
                return {int(m) for cit in citations
                        for m in re.findall(r"knowledge:(\d+)@", cit)}

            # search: latest only
            hits = c.get("/v1/knowledge/search",
                         params={"q": "regresscanary", "n": 100, "full": "true"}).json()["items"]
            got = {h["id"] for h in hits}
            assert got == new_set and not (got & old_set)

            # verified rules: latest only
            rules = c.get("/v1/knowledge-rules").json()
            rids = {r["id"] for r in rules["items"]}
            assert rids == new_set and not (rids & old_set)
            assert _cited_ids(rules["citations"]) == new_set

            # snapshot: latest only, on disk and in citations
            snap = c.post("/v1/knowledge/snapshot",
                          json={"tenant": "acme", "project": "site"}).json()
            assert _cited_ids(snap["citations"]) == new_set
            assert snap["count"] == 5
            disk = open(snap["path"]).read()
            assert _cited_ids(re.findall(r"knowledge:\d+@v\d+", disk)) == new_set
            os.unlink(snap["path"])

            # curate: suggestions reference current ids only
            cur = c.post("/v1/knowledge/curate",
                         json={"tenant": "acme", "project": "site"}).json()
            ref = set()
            for s in cur["suggestions"]:
                ref.update(s.get("entries", []))
                ref.update(int(m) for m in re.findall(r"knowledge:(\d+)", s.get("proposal", "")))
            assert not (ref & old_set)

            # id lookup: superseded id resolves forward to current
            for oid, nid in zip(old_ids, new_ids):
                got_one = c.get(f"/v1/knowledge/{oid}").json()
                assert got_one["id"] == nid
            for nid in new_ids:
                assert c.get(f"/v1/knowledge/{nid}").json()["id"] == nid

            # history still replays the full chain (not filtered)
            for oid, nid in zip(old_ids, new_ids):
                hist = c.get(f"/v1/knowledge/{nid}/history").json()["items"]
                assert [(h["id"], h["version"]) for h in hist] == [(oid, 1), (nid, 2)]
    finally:
        if prev_insecure is None:
            os.environ.pop("ROUVR_INSECURE_DEV", None)
        else:
            os.environ["ROUVR_INSECURE_DEV"] = prev_insecure
        if prev_token is None:
            os.environ.pop("ROUVR_LEDGER_TOKEN", None)
        else:
            os.environ["ROUVR_LEDGER_TOKEN"] = prev_token
