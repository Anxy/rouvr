"""FL-A3 contract: /api/v1 aliases wrap ledger handlers without duplication.

All tests run against the real app (TestClient + SQLite); no doubles, no
network. Env is scoped per test by saving and restoring os.environ around
app construction (make_app reads ROUVR_* at construction, so each test
builds its own app).
"""
import os

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from ledger.app import make_app
from ledger.lite import MAPPING, lite_path, mount_lite_routes


@pytest.fixture()
def app(tmp_path):
    saved_insecure = os.environ.get("ROUVR_INSECURE_DEV")
    saved_token = os.environ.get("ROUVR_LEDGER_TOKEN")
    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)
    try:
        application = make_app(f"sqlite:///{tmp_path}/l.db")
        seed = TestClient(application)
        seed.post("/v1/tenants", json={"tenant": "acme"})
        seed.post("/v1/projects", json={"tenant": "acme", "project": "site"})
        yield application
    finally:
        if saved_insecure is None:
            os.environ.pop("ROUVR_INSECURE_DEV", None)
        else:
            os.environ["ROUVR_INSECURE_DEV"] = saved_insecure
        if saved_token is None:
            os.environ.pop("ROUVR_LEDGER_TOKEN", None)
        else:
            os.environ["ROUVR_LEDGER_TOKEN"] = saved_token


@pytest.fixture()
def c(app):
    return TestClient(app)


def _routes(application):
    out = {}
    for r in application.routes:
        if isinstance(r, APIRoute):
            for m in (r.methods or set()) - {"HEAD", "OPTIONS"}:
                out[(m, r.path)] = r.endpoint
    return out


def test_every_ledger_route_has_lite_alias_with_same_endpoint(app):
    routes = _routes(app)
    for method, ledger, handler in MAPPING:
        lite = lite_path(ledger)
        assert lite is not None
        assert (method, ledger) in routes, f"missing ledger {method} {ledger}"
        assert (method, lite) in routes, f"missing lite {method} {lite}"
        assert routes[(method, lite)] is routes[(method, ledger)], \
            f"duplicated handler for {method} {lite}"
        assert routes[(method, ledger)].__name__ == handler, \
            f"handler renamed for {method} {ledger}"


def test_mapping_lists_every_aliased_route(app):
    routes = _routes(app)
    mapped = {(m, ledger) for m, ledger, _ in MAPPING}
    for method, path in routes:
        ledger_path = "/health" if path == "/api/v1/health" \
            else path[4:] if path.startswith("/api/v1/") else path
        if lite_path(ledger_path) is None:
            continue  # intentionally unaliased (demo page, docs)
        assert (method, ledger_path) in mapped, \
            f"MAPPING misses {method} {ledger_path}"


def test_mapping_doc_covers_all_lite_paths():
    from pathlib import Path
    doc = Path(__file__).with_name("LITE_API_MAPPING.md").read_text()
    for _m, ledger, handler in MAPPING:
        assert lite_path(ledger) in doc, f"mapping doc misses {lite_path(ledger)}"
        assert handler in doc, f"mapping doc misses handler {handler}"


def test_mount_is_idempotent(app):
    before = [(m, p) for m, p in _routes(app)]
    mount_lite_routes(app)
    after = [(m, p) for m, p in _routes(app)]
    assert sorted(before) == sorted(after)


def test_openapi_includes_lite_prefix(c):
    paths = c.get("/openapi.json").json()["paths"]
    assert "/api/v1/tasks" in paths and "/v1/tasks" in paths
    assert "/api/v1/knowledge/search" in paths
    assert "/api/v1/health" in paths


def test_health_alias_open_and_identical(c):
    assert c.get("/health").json() == c.get("/api/v1/health").json() == \
        {"status": "ok", "service": "rouvr-ledger"}


def test_tenants_projects_users_parity(c):
    assert c.post("/api/v1/tenants", json={"tenant": "globex"}).status_code == 200
    r = c.post("/api/v1/projects", json={"tenant": "globex", "project": "web"})
    assert r.json()["project"] == "web"
    assert c.post("/v1/projects", json={"tenant": "globex", "project": "web"}).status_code == 200
    lite = c.get("/api/v1/projects", params={"tenant": "globex"}).json()["projects"]
    v1 = c.get("/v1/projects", params={"tenant": "globex"}).json()["projects"]
    assert lite == v1 and len(lite) == 1
    u = c.post("/api/v1/users", json={"username": "ada", "token_hash": "h" * 16,
                                      "tenant": "globex"}).json()
    assert u["tenant"] == "globex"
    assert "ada" in [x["username"] for x in c.get("/v1/users").json()["items"]]
    assert c.post("/api/v1/users/ada/disable").json() == {"disabled": "ada"}


def test_task_crud_parity_across_prefixes(c):
    t = c.post("/api/v1/tasks", json={"title": "a", "intent": "i",
                                      "tenant": "acme", "project": "site"}).json()
    assert c.get(f"/v1/tasks/{t['id']}").json()["id"] == t["id"]
    t2 = c.post("/v1/tasks", json={"title": "b", "intent": "i",
                                   "tenant": "acme", "project": "site"}).json()
    assert c.get(f"/api/v1/tasks/{t2['id']}").json()["id"] == t2["id"]
    ids_v1 = {x["id"] for x in c.get("/v1/tasks").json()["items"]}
    ids_lite = {x["id"] for x in c.get("/api/v1/tasks").json()["items"]}
    assert ids_v1 == ids_lite == {t["id"], t2["id"]}
    tc = c.post("/api/v1/tasks_with_contract",
                json={"title": "w", "intent": "i", "tenant": "acme", "project": "site",
                      "contract": {"checks": ["a"], "acceptance_criteria": ["a"]}}).json()
    assert len(tc["contracts"]) == 1
    assert c.get(f"/v1/tasks/{tc['id']}").json()["id"] == tc["id"]


def test_transition_evidence_contract_parity(c):
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y",
                                    "tenant": "acme", "project": "site"}).json()["id"]
    assert c.post(f"/api/v1/tasks/{tid}/transition",
                  json={"to": "claimed", "idempotency_key": "k1"}).status_code == 200
    # done via lite without contract+evidence -> 409, same as ledger
    assert c.post(f"/api/v1/tasks/{tid}/transition",
                  json={"to": "done", "idempotency_key": "k2"}).status_code == 409
    c.post(f"/api/v1/tasks/{tid}/contracts", json={"checks": ["a"]})
    c.post(f"/v1/tasks/{tid}/evidence", json={"kind": "check", "body": {"a": True}})
    r1 = c.post(f"/api/v1/tasks/{tid}/transition",
                json={"to": "done", "idempotency_key": "k3"}).json()
    r2 = c.post(f"/v1/tasks/{tid}/transition",
                json={"to": "done", "idempotency_key": "k3"}).json()
    assert r1["replayed"] is False and r2["replayed"] is True
    assert c.get(f"/api/v1/tasks/{tid}").json()["state"] == "done"


def test_error_parity_422_404_409(c):
    for prefix in ("/v1", "/api/v1"):
        assert c.post(f"{prefix}/tasks", json={"title": "x"}).status_code == 422
        assert c.get(f"{prefix}/tasks/9999").status_code == 404
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y",
                                    "tenant": "acme", "project": "site"}).json()["id"]
    for prefix in ("/v1", "/api/v1"):
        assert c.post(f"{prefix}/tasks/{tid}/transition",
                      json={"to": "done", "idempotency_key": "e"}).status_code == 409


def test_claim_attempt_result_renew_parity(c):
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y",
                                    "tenant": "acme", "project": "site"}).json()["id"]
    assert c.post(f"/api/v1/tasks/{tid}/assignments/claim",
                  json={"key": "c1", "principal": "p", "attempt_no": 1}).status_code == 200
    assert c.post(f"/v1/tasks/{tid}/leases/renew",
                  json={"key": "rn1", "holder": "p",
                        "expected_lease_epoch": 1, "ttl_s": 60}).status_code == 200
    att = c.post(f"/api/v1/tasks/{tid}/attempts",
                 json={"key": "a1", "attempt_no": 1, "lease_epoch": 1}).json()["attempt"]
    assert c.post(f"/v1/tasks/{tid}/results",
                  json={"key": "r1", "attempt_id": att["id"], "lease_epoch": 1,
                        "verdict": "pass", "result": {}}).status_code == 200
    assert c.get(f"/api/v1/tasks/{tid}/binding").json()["assignment"]["principal"] == "p"


def test_knowledge_parity(c):
    k = c.post("/api/v1/knowledge",
               json={"tenant": "acme", "project": "site", "kind": "note",
                     "title": "t", "body": "b hello world"}).json()
    assert c.get(f"/v1/knowledge/{k['id']}", params={"project": "site"}).json()["id"] == k["id"]
    v2 = c.post(f"/v1/knowledge/{k['id']}/versions",
                json={"title": "t2", "body": "b2 hello world"}).json()
    assert v2["version"] == 2
    hist = c.get(f"/api/v1/knowledge/{v2['id']}/history").json()["items"]
    assert [x["id"] for x in hist] == [k["id"], v2["id"]]
    assert c.post(f"/api/v1/knowledge/{v2['id']}/confirm").json()["verified"] == 1
    assert c.get("/api/v1/knowledge-rules", params={"project": "site"}).status_code == 200
    assert c.get("/v1/knowledge/search",
                 params={"q": "hello", "project": "site"}).status_code == 200
    assert c.get("/api/v1/knowledge/search",
                 params={"q": "hello", "project": "site"}).status_code == 200
    cur = c.post("/api/v1/knowledge/curate",
                 json={"tenant": "acme", "project": "site"}).json()
    assert cur["count"] == len(cur["suggestions"])
    stored = c.post("/v1/knowledge/store",
                    json={"tenant": "acme", "project": "site", "items": [
                        {"kind": "note", "title": "s1", "body": "stored body"}]}).json()
    assert len(stored["items"]) == 1


def test_grants_parity(c):
    g = c.post("/api/v1/grants", json={"scope": "s", "expires_at": "2999-01-01T00:00:00+00:00",
                                       "approved_by": "o"}).json()
    assert g["scope"] == "s"
    assert c.get("/v1/grants/active", params={"scope": "s"}).json()["active"] is True
    assert c.get("/api/v1/grants/active", params={"scope": "s"}).json()["active"] is True


def test_auth_parity_when_token_configured(tmp_path):
    saved_insecure = os.environ.get("ROUVR_INSECURE_DEV")
    saved_token = os.environ.get("ROUVR_LEDGER_TOKEN")
    os.environ["ROUVR_LEDGER_TOKEN"] = "sek"
    os.environ.pop("ROUVR_INSECURE_DEV", None)
    try:
        anon = TestClient(make_app(f"sqlite:///{tmp_path}/l.db"))
        for path in ("/v1/tasks", "/api/v1/tasks"):
            assert anon.get(path).status_code == 401
        for path in ("/health", "/api/v1/health"):
            assert anon.get(path).status_code == 200
        # onboard register/login are pre-account open on both prefixes: empty
        # body reaches handler validation (422), never the 401 guard, and no
        # upstream call happens before that validation.
        for path in ("/v1/onboard/register", "/api/v1/onboard/register",
                     "/v1/onboard/login", "/api/v1/onboard/login"):
            assert anon.post(path, json={}).status_code == 422
        authed = TestClient(make_app(f"sqlite:///{tmp_path}/l.db"),
                            headers={"x-rouvr-token": "sek"})
        assert authed.get("/v1/tasks").status_code == 200
        assert authed.get("/api/v1/tasks").status_code == 200
    finally:
        if saved_insecure is None:
            os.environ.pop("ROUVR_INSECURE_DEV", None)
        else:
            os.environ["ROUVR_INSECURE_DEV"] = saved_insecure
        if saved_token is None:
            os.environ.pop("ROUVR_LEDGER_TOKEN", None)
        else:
            os.environ["ROUVR_LEDGER_TOKEN"] = saved_token
