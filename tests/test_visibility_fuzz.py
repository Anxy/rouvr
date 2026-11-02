"""Visibility fuzz: 3 tenants x 3 users x 3 visibilities, zero leaks."""
import hashlib

from fastapi.testclient import TestClient


def _mkuser(c, stack, user, tok, tenant):
    h = hashlib.sha256(tok.encode()).hexdigest()
    r = c.post("/v1/users", json={"username": user, "token_hash": h, "tenant": tenant},
               headers=stack)
    assert r.status_code == 200, r.text


def test_visibility_fuzz_no_leaks(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    from ledger.app import make_app
    app = make_app(f"sqlite:///{tmp_path}/v.db")
    stack = {"X-Rouvr-Token": "stack-secret"}
    tenants = ["t1", "t2", "t3"]
    users = {}
    with TestClient(app) as c:
        for t in tenants:
            assert c.post("/v1/tenants", json={"tenant": t}, headers=stack).status_code == 200
            assert c.post("/v1/projects", json={"tenant": t, "project": "p"},
                          headers=stack).status_code == 200
        for t in tenants:
            for u in ("a", "b", "c"):
                name, tok = f"{t}-{u}", f"{t}-{u}-tok"
                _mkuser(c, stack, name, tok, t)
                users[(t, u)] = {"X-Rouvr-Token": tok}
        ids = {}
        for (t, u) in users:
            for vis in ("restricted", "company", "public"):
                kw = {"visibility": vis}
                if vis == "restricted" and u == "a":
                    kw["allowed_users"] = [f"{t}-b"]  # a shares with b, not c
                canary = f"vis{t}{u}{vis}".replace("-", "")
                r = c.post("/v1/knowledge", json={
                    "tenant": t, "project": "p", "kind": "note",
                    "title": f"VIS {canary} canary", "body": f"secret {canary} body",
                    **kw}, headers=users[(t, u)])
                assert r.status_code == 200, (t, u, vis, r.text)
                ids[(t, u, vis)] = (r.json()["id"], canary)
        # every user: search/get/history only see allowed entries
        for (ct, cu), hdr in users.items():
            for (et, eu, evis), (eid, canary) in ids.items():
                hits = c.get("/v1/knowledge/search", params={"q": canary},
                             headers=hdr).json()["items"]
                e = c.get(f"/v1/knowledge/{eid}", headers=hdr)
                h = c.get(f"/v1/knowledge/{eid}/history", headers=hdr)
                if evis == "public":
                    assert [x["id"] for x in hits] == [eid], (ct, cu, et, eu, evis)
                    assert e.status_code == 200 and h.status_code == 200
                elif et == ct and evis == "company":
                    assert [x["id"] for x in hits] == [eid], (ct, cu, et, eu, evis)
                    assert e.status_code == 200 and h.status_code == 200
                elif evis == "restricted" and et == ct and (
                        eu == cu or (eu == "a" and cu == "b")):
                    assert [x["id"] for x in hits] == [eid], (ct, cu, et, eu, evis)
                    assert e.status_code == 200 and h.status_code == 200
                else:
                    assert hits == [], (ct, cu, et, eu, evis, hits)
                    assert e.status_code == 404, (ct, cu, et, eu, evis, e.status_code)
                    assert h.status_code == 404
        # anonymous never sees anything (fail-closed)
        anon = c.get("/v1/knowledge/search", params={"q": "secret"})
        assert anon.status_code == 401
        # visibility change only by creator or admin
        some, _can = ids[("t1", "a", "company")]
        r = c.post(f"/v1/knowledge/{some}/versions",
                   json={"title": "x", "body": "y", "visibility": "public"},
                   headers=users[("t1", "b")])
        assert r.status_code == 403
        r = c.post(f"/v1/knowledge/{some}/versions",
                   json={"title": "x", "body": "y", "visibility": "public"},
                   headers=users[("t1", "a")])
        assert r.status_code == 200 and r.json()["visibility"] == "public"
