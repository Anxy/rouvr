from fastapi.testclient import TestClient
from ledger.app import make_app


def _client(tmp_path):
    return TestClient(make_app(f"sqlite:///{tmp_path}/l.db"))


def test_done_requires_contract_and_evidence_and_replay_is_idempotent(tmp_path):
    c = _client(tmp_path)
    t = c.post("/v1/tasks", json={"title": "morning report", "intent": "send ops summary"}).json()
    tid = t["id"]
    assert c.post(f"/v1/tasks/{tid}/transition", json={"to": "claimed", "idempotency_key": "k1"}).status_code == 200
    assert c.post(f"/v1/tasks/{tid}/transition", json={"to": "done", "idempotency_key": "k2"}).status_code == 409
    c.post(f"/v1/tasks/{tid}/contracts", json={"checks": ["report_sent"]})
    c.post(f"/v1/tasks/{tid}/evidence", json={"kind": "check", "body": {"report_sent": True}})
    r1 = c.post(f"/v1/tasks/{tid}/transition", json={"to": "done", "idempotency_key": "k3"}).json()
    r2 = c.post(f"/v1/tasks/{tid}/transition", json={"to": "done", "idempotency_key": "k3"}).json()
    assert r1["replayed"] is False and r2["replayed"] is True
    full = c.get(f"/v1/tasks/{tid}").json()
    assert full["state"] == "done" and len(full["receipts"]) == 2


def test_illegal_transition_rejected(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    assert c.post(f"/v1/tasks/{tid}/transition", json={"to": "done", "idempotency_key": "a"}).status_code == 409


def test_claim_moves_task_to_claimed_and_review_cycle(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    r = c.post(f"/v1/tasks/{tid}/assignments/claim",
               json={"key": "claim-1", "principal": "svc-b", "attempt_no": 1})
    assert r.status_code == 200
    assert c.get(f"/v1/tasks/{tid}").json()["state"] == "claimed"
    assert c.post(f"/v1/tasks/{tid}/transition",
                  json={"to": "needs_review", "idempotency_key": "nr1"}).status_code == 200
    assert c.get(f"/v1/tasks/{tid}").json()["state"] == "needs_review"
    # rework: claiming the next attempt returns the task to claimed
    r = c.post(f"/v1/tasks/{tid}/assignments/claim",
               json={"key": "claim-2", "principal": "svc-b", "attempt_no": 2})
    assert r.status_code == 200
    assert c.get(f"/v1/tasks/{tid}").json()["state"] == "claimed"
