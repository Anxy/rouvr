"""FL-A3 contract: /api/v1 aliases wrap ledger handlers without duplication."""
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute
from ledger.app import make_app
from ledger.lite import MAPPING, lite_path


def _client(tmp_path):
    import os
    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)
    app = make_app(f"sqlite:///{tmp_path}/l.db")
    c = TestClient(app)
    c.post("/v1/tenants", json={"tenant": "acme"})
    c.post("/v1/projects", json={"tenant": "acme", "project": "site"})
    return c, app


def _routes(app):
    out = {}
    for r in app.routes:
        if isinstance(r, APIRoute):
            for m in (r.methods or set()) - {"HEAD", "OPTIONS"}:
                out[(m, r.path)] = r.endpoint
    return out


def test_every_ledger_route_has_lite_alias_with_same_endpoint(tmp_path):
    _, app = _client(tmp_path)
    routes = _routes(app)
    for method, ledger, _handler in MAPPING:
        lite = lite_path(ledger)
        assert (method, ledger) in routes, f"missing ledger {method} {ledger}"
        assert (method, lite) in routes, f"missing lite {method} {lite}"
        assert routes[(method, lite)] is routes[(method, ledger)], f"duplicated handler for {lite}"


def test_mapping_doc_covers_all_lite_paths():
    from pathlib import Path
    doc = Path(__file__).with_name("LITE_API_MAPPING.md").read_text()
    for _m, ledger, _h in MAPPING:
        assert lite_path(ledger) in doc, f"mapping doc misses {lite_path(ledger)}"


def test_openapi_includes_lite_prefix(tmp_path):
    c, _ = _client(tmp_path)
    paths = c.get("/openapi.json").json()["paths"]
    assert "/api/v1/tasks" in paths and "/v1/tasks" in paths
    assert "/api/v1/knowledge/search" in paths


def test_health_alias_open_and_identical(tmp_path):
    c, _ = _client(tmp_path)
    assert c.get("/health").json() == c.get("/api/v1/health").json() == {"status": "ok", "service": "rouvr-ledger"}


def test_task_crud_parity_across_prefixes(tmp_path):
    c, _ = _client(tmp_path)
    t = c.post("/api/v1/tasks", json={"title": "a", "intent": "i", "tenant": "acme", "project": "site"}).json()
    assert c.get(f"/v1/tasks/{t['id']}").json()["id"] == t["id"]
    t2 = c.post("/v1/tasks", json={"title": "b", "intent": "i", "tenant": "acme", "project": "site"}).json()
    assert c.get(f"/api/v1/tasks/{t2['id']}").json()["id"] == t2["id"]
    ids_v1 = {x["id"] for x in c.get("/v1/tasks").json()["items"]}
    ids_lite = {x["id"] for x in c.get("/api/v1/tasks").json()["items"]}
    assert ids_v1 == ids_lite == {t["id"], t2["id"]}


def test_transition_evidence_contract_parity(tmp_path):
    c, _ = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y", "tenant": "acme", "project": "site"}).json()["id"]
    assert c.post(f"/api/v1/tasks/{tid}/transition", json={"to": "claimed", "idempotency_key": "k1"}).status_code == 200
    # done via lite without contract+evidence -> 409, same as ledger
    assert c.post(f"/api/v1/tasks/{tid}/transition", json={"to": "done", "idempotency_key": "k2"}).status_code == 409
    c.post(f"/api/v1/tasks/{tid}/contracts", json={"checks": ["a"]})
    c.post(f"/v1/tasks/{tid}/evidence", json={"kind": "check", "body": {"a": True}})
    r1 = c.post(f"/api/v1/tasks/{tid}/transition", json={"to": "done", "idempotency_key": "k3"}).json()
    r2 = c.post(f"/v1/tasks/{tid}/transition", json={"to": "done", "idempotency_key": "k3"}).json()
    assert r1["replayed"] is False and r2["replayed"] is True
    assert c.get(f"/api/v1/tasks/{tid}").json()["state"] == "done"


def test_error_parity_422_404_409(tmp_path):
    c, _ = _client(tmp_path)
    for prefix in ("/v1", "/api/v1"):
        assert c.post(f"{prefix}/tasks", json={"title": "x"}).status_code == 422  # missing scope
        assert c.get(f"{prefix}/tasks/9999").status_code == 404
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y", "tenant": "acme", "project": "site"}).json()["id"]
    for prefix in ("/v1", "/api/v1"):
        assert c.post(f"{prefix}/tasks/{tid}/transition", json={"to": "done", "idempotency_key": "e"}).status_code == 409


def test_claim_attempt_result_parity(tmp_path):
    c, _ = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y", "tenant": "acme", "project": "site"}).json()["id"]
    assert c.post(f"/api/v1/tasks/{tid}/assignments/claim", json={"key": "c1", "principal": "p", "attempt_no": 1}).status_code == 200
    att = c.post(f"/api/v1/tasks/{tid}/attempts", json={"key": "a1", "attempt_no": 1, "lease_epoch": 1}).json()["attempt"]
    assert c.post(f"/v1/tasks/{tid}/results", json={"key": "r1", "attempt_id": att["id"], "lease_epoch": 1, "verdict": "pass", "result": {}}).status_code == 200
    assert c.get(f"/api/v1/tasks/{tid}/binding").json()["assignment"]["principal"] == "p"


def test_knowledge_and_grants_parity(tmp_path):
    c, _ = _client(tmp_path)
    k = c.post("/api/v1/knowledge", json={"tenant": "acme", "project": "site", "kind": "note", "title": "t", "body": "b hello world"}).json()
    assert c.get(f"/v1/knowledge/{k['id']}", params={"project": "site"}).json()["id"] == k["id"]
    assert c.get("/api/v1/knowledge-rules", params={"project": "site"}).status_code == 200
    assert c.get("/api/v1/knowledge/search", params={"q": "hello", "project": "site"}).status_code == 200
    g = c.post("/api/v1/grants", json={"scope": "s", "expires_at": "2999-01-01T00:00:00+00:00", "approved_by": "o"}).json()
    assert g["scope"] == "s"
    assert c.get("/v1/grants/active", params={"scope": "s"}).json()["active"] is True
    assert c.get("/api/v1/grants/active", params={"scope": "s"}).json()["active"] is True


def test_auth_parity_when_token_configured(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "sek")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    app = make_app(f"sqlite:///{tmp_path}/l.db")
    c = TestClient(app)
    for path in ("/v1/tasks", "/api/v1/tasks"):
        assert c.get(path).status_code == 401
    for path in ("/health", "/api/v1/health"):
        assert c.get(path).status_code == 200
