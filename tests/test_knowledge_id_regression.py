"""A6 knowledge id regression: superseded entries never surface as current.

Seeds five version chains mirroring the live 165/171-174 -> 267-271 case
(old v1 verified, new v2 verified pointing at the old via supersedes_id)
and asserts the old ids appear in no current-result path: search
(shortlist and full), knowledge-rules, snapshot (response and file),
compact, and curate. History still replays the full chain by design.
"""
import os
from pathlib import Path

from fastapi.testclient import TestClient
from ledger.app import make_app

TOKENS = ("Alphakreg", "Betakreg", "Gammakreg", "Deltakreg", "Epsilonkreg")
REMOVED = "Zqqremovedkreg"


def _seed(c):
    assert c.post("/v1/tenants", json={"tenant": "acme"}).status_code == 200
    assert c.post("/v1/projects", json={"tenant": "acme", "project": "site"}).status_code == 200
    old_ids, new_ids = [], []
    for tok in TOKENS:
        body = f"Background on {tok} processing workflow."
        if tok == TOKENS[-1]:
            body += f" Legacy marker {REMOVED}."
        e1 = c.post("/v1/knowledge", json={
            "tenant": "acme", "project": "site", "kind": "rule",
            "title": f"Standing {tok} handling note", "body": body}).json()
        assert c.post(f"/v1/knowledge/{e1['id']}/confirm", json={}).status_code == 200
        e2 = c.post(f"/v1/knowledge/{e1['id']}/versions", json={
            "title": f"Standing {tok} handling note",
            "body": f"Revised background on {tok} processing workflow."}).json()
        assert (e2["version"], e2["supersedes_id"]) == (2, e1["id"])
        assert e2["slug"] == e1["slug"]
        assert c.post(f"/v1/knowledge/{e2['id']}/confirm", json={}).status_code == 200
        old_ids.append(e1["id"])
        new_ids.append(e2["id"])
    return old_ids, new_ids


def _cite_ids(cites):
    return [int(s.split(":")[1].split("@")[0]) for s in cites]


def test_superseded_ids_never_in_current_results(tmp_path):
    prev_insecure = os.environ.get("ROUVR_INSECURE_DEV")
    prev_token = os.environ.get("ROUVR_LEDGER_TOKEN")
    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)
    snap_path = None
    try:
        app = make_app(f"sqlite:///{tmp_path}/kid.db")
        with TestClient(app) as c:
            old_ids, new_ids = _seed(c)
            old, new = set(old_ids), set(new_ids)

            for tok, oid, nid in zip(TOKENS, old_ids, new_ids):
                cites = [h["citation"] for h in c.get(
                    "/v1/knowledge/search", params={"q": tok}).json()["items"]]
                assert any(f"knowledge:{nid}@" in s for s in cites), cites
                assert not any(f"knowledge:{oid}@" in s for s in cites), cites
                full = [h["id"] for h in c.get(
                    "/v1/knowledge/search",
                    params={"q": tok, "full": "true"}).json()["items"]]
                assert nid in full and oid not in full, full

            # Token kept only in a superseded body: fuzzy search may still
            # return current entries, but never the superseded holder.
            cites = [h["citation"] for h in c.get(
                "/v1/knowledge/search", params={"q": REMOVED}).json()["items"]]
            assert not any(f"knowledge:{old_ids[-1]}@" in s for s in cites), cites

            rules = c.get("/v1/knowledge-rules",
                          params={"project": "site"}).json()
            assert set(_cite_ids(rules["citations"])) == new
            assert {r["id"] for r in rules["items"]} == new

            snap = c.post("/v1/knowledge/snapshot",
                          json={"tenant": "acme", "project": "site"}).json()
            assert snap["count"] == len(new_ids)
            assert set(_cite_ids(snap["citations"])) == new
            snap_path = snap["path"]
            text = Path(snap_path).read_text()
            for nid in new_ids:
                assert f"knowledge:{nid}@" in text, snap["citations"]
            for oid in old_ids:
                assert f"knowledge:{oid}@" not in text, snap["citations"]

            comp = c.post("/v1/knowledge/compact",
                          json={"tenant": "acme", "project": "site"}).json()
            assert set(_cite_ids(comp["sources"])) == new
            for oid in old_ids:
                assert f"knowledge:{oid}@" not in comp["snapshot"]["body"]

            cur = c.post("/v1/knowledge/curate",
                         json={"tenant": "acme", "project": "site"}).json()
            entries = [e for s in cur["suggestions"] for e in s.get("entries", [])]
            assert entries, cur
            assert not (set(entries) & old), cur["suggestions"]
            assert set(entries) == new, cur["suggestions"]

            # Append-only history still replays the chain by design.
            hist = c.get(f"/v1/knowledge/{new_ids[0]}/history").json()["items"]
            assert [(h["id"], h["version"]) for h in hist] == [
                (old_ids[0], 1), (new_ids[0], 2)]
    finally:
        if snap_path is not None:
            Path(snap_path).unlink(missing_ok=True)
        if prev_insecure is None:
            os.environ.pop("ROUVR_INSECURE_DEV", None)
        else:
            os.environ["ROUVR_INSECURE_DEV"] = prev_insecure
        if prev_token is not None:
            os.environ["ROUVR_LEDGER_TOKEN"] = prev_token
