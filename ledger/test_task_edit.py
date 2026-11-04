"""PATCH /v1/tasks/{id}: edit title/intent of an unclaimed open task only."""
import pytest
from fastapi.testclient import TestClient

from ledger.app import make_app


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_INSECURE_DEV", "1")
    monkeypatch.delenv("ROUVR_LEDGER_TOKEN", raising=False)
    c = TestClient(make_app(f"sqlite:///{tmp_path / 'l.db'}"))
    c.post("/v1/tenants", json={"tenant": "acme"})
    c.post("/v1/projects", json={"tenant": "acme", "project": "site"})
    return c


def _task(c):
    return c.post("/v1/tasks", json={"title": "t", "intent": "y",
                                    "tenant": "acme",
                                    "project": "site"}).json()


def test_edit_requires_key_and_fields(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    t = _task(c)
    assert c.patch(f"/v1/tasks/{t['id']}", json={}).status_code == 422
    assert c.patch(f"/v1/tasks/{t['id']}",
                  json={"idempotency_key": "k"}).status_code == 422
    assert c.patch(f"/v1/tasks/{t['id']}",
                  json={"title": "  ", "idempotency_key": "k"}).status_code == 422


def test_edit_happy_path_and_replay(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    t = _task(c)
    r = c.patch(f"/v1/tasks/{t['id']}",
               json={"title": "t2", "intent": "y2", "idempotency_key": "k1"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "t2"
    assert r.json()["replayed"] is False
    again = c.patch(f"/v1/tasks/{t['id']}",
                   json={"title": "t3", "idempotency_key": "k1"})
    assert again.status_code == 200
    assert again.json()["replayed"] is True
    got = c.get(f"/v1/tasks/{t['id']}").json()
    assert got["title"] == "t2", "replay must not apply the second edit"


def test_edit_refuses_claimed_and_non_open(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    t = _task(c)
    c.post(f"/v1/tasks/{t['id']}/transition",
           json={"to": "claimed", "idempotency_key": "c1"})
    r = c.patch(f"/v1/tasks/{t['id']}",
               json={"title": "t2", "idempotency_key": "k2"})
    assert r.status_code == 409
    c.post(f"/v1/tasks/{t['id']}/transition",
           json={"to": "blocked", "idempotency_key": "b1"})
    r = c.patch(f"/v1/tasks/{t['id']}",
               json={"title": "t2", "idempotency_key": "k3"})
    assert r.status_code == 409
