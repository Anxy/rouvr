"""POST /v1/tasks/{id}/retire: mandatory reason, terminal retired state."""
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


def test_retire_requires_reason_and_key(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    t = _task(c)
    assert c.post(f"/v1/tasks/{t['id']}/retire", json={}).status_code == 422
    assert c.post(f"/v1/tasks/{t['id']}/retire",
                  json={"reason": "x"}).status_code == 422
    assert c.post(f"/v1/tasks/{t['id']}/retire",
                  json={"reason": "   ",
                        "idempotency_key": "k"}).status_code == 422


def test_retire_happy_path_and_replay(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    t = _task(c)
    c.post(f"/v1/tasks/{t['id']}/transition",
           json={"to": "blocked", "idempotency_key": "b1"})
    r = c.post(f"/v1/tasks/{t['id']}/retire",
               json={"reason": "superseded by #9",
                     "idempotency_key": "r1"})
    assert r.status_code == 200, r.text
    assert r.json() == {"replayed": False, "task_id": t["id"]}
    detail = c.get(f"/v1/tasks/{t['id']}").json()
    assert detail["state"] == "retired"
    notes = [e for e in detail["evidence"] if e["kind"] == "retire_note"]
    assert len(notes) == 1
    r = c.post(f"/v1/tasks/{t['id']}/retire",
               json={"reason": "other", "idempotency_key": "r1"})
    assert r.json()["replayed"] is True
    detail = c.get(f"/v1/tasks/{t['id']}").json()
    assert len([e for e in detail["evidence"]
                if e["kind"] == "retire_note"]) == 1


def test_retired_is_terminal_and_scoped(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    t = _task(c)
    c.post(f"/v1/tasks/{t['id']}/retire",
           json={"reason": "no longer needed", "idempotency_key": "r1"})
    r = c.post(f"/v1/tasks/{t['id']}/transition",
               json={"to": "open", "idempotency_key": "o1"})
    assert r.status_code == 409
    t2 = _task(c)
    c.post(f"/v1/tasks/{t2['id']}/transition",
           json={"to": "claimed", "idempotency_key": "c1"})
    r = c.post(f"/v1/tasks/{t2['id']}/retire",
               json={"reason": "try", "idempotency_key": "r2"})
    assert r.status_code == 409  # step down through blocked first
    assert c.get("/v1/tasks?state=blocked").json()["items"] == []
    assert c.get("/v1/tasks?state=retired").json()["items"][0]["id"] == t["id"]


def test_done_never_retires(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    d = c.post("/v1/tasks_with_contract", json={"title": "v", "intent": "y",
                                                "tenant": "acme",
                                                "project": "site",
                                                "contract": {"project_id": "site",
                                                             "description": "v",
                                                             "acceptance_criteria": ["v works"]}}).json()
    c.post(f"/v1/tasks/{d['id']}/evidence",
           json={"kind": "note", "body": {"text": "built"}})
    c.post(f"/v1/tasks/{d['id']}/transition",
           json={"to": "claimed", "idempotency_key": "c9"})
    c.post(f"/v1/tasks/{d['id']}/transition",
           json={"to": "needs_review", "idempotency_key": "n9"})
    assert c.post(f"/v1/tasks/{d['id']}/transition",
                  json={"to": "done", "idempotency_key": "d9"}).status_code == 200
    r = c.post(f"/v1/tasks/{d['id']}/retire",
               json={"reason": "try", "idempotency_key": "r9"})
    assert r.status_code == 409
