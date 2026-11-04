"""GET /v1/knowledge/{key}/usage: applied-task count + last use per entry."""
from fastapi.testclient import TestClient

from ledger.app import make_app


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_INSECURE_DEV", "1")
    monkeypatch.delenv("ROUVR_LEDGER_TOKEN", raising=False)
    c = TestClient(make_app(f"sqlite:///{tmp_path / 'l.db'}"))
    c.post("/v1/tenants", json={"tenant": "acme"})
    c.post("/v1/projects", json={"tenant": "acme", "project": "site"})
    return c


def test_usage_counts_citing_tasks(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    k = c.post("/v1/knowledge", json={"tenant": "acme", "project": "site",
                                      "kind": "rule", "title": "Use hints",
                                      "body": "all functions need type hints"}).json()
    for _ in range(2):
        t = c.post("/v1/tasks", json={"title": "t", "intent": "y",
                                      "tenant": "acme", "project": "site"}).json()
        r = c.post(f"/v1/tasks/{t['id']}/evidence",
                   json={"kind": "knowledge_citations",
                         "body": {"citations": [f"knowledge:{k['id']}@v1"]}})
        assert r.status_code == 200, r.text
    got = c.get(f"/v1/knowledge/{k['id']}/usage").json()
    assert got["applied_to_tasks"] == 2
    assert got["version"] == 1
    assert got["citation"] == f"knowledge:{k['id']}@v1"
    assert got["last_used_at"]
    slug = c.get(f"/v1/knowledge/{k['id']}").json()["slug"]
    assert c.get(f"/v1/knowledge/{slug}/usage").json()["applied_to_tasks"] == 2


def test_usage_zero_and_unknown(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    k = c.post("/v1/knowledge", json={"tenant": "acme", "project": "site",
                                      "kind": "note", "title": "Unused",
                                      "body": "never cited"}).json()
    got = c.get(f"/v1/knowledge/{k['id']}/usage").json()
    assert got["applied_to_tasks"] == 0 and got["last_used_at"] is None
    assert c.get("/v1/knowledge/999999/usage").status_code == 404
