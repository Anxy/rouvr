from fastapi.testclient import TestClient
from ledger.app import make_app


def _client(tmp_path):
    import os
    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)
    c = TestClient(make_app(f"sqlite:///{tmp_path}/l.db"))
    c.post("/v1/tenants", json={"tenant": "acme"})
    c.post("/v1/projects", json={"tenant": "acme", "project": "site"})
    return c


def test_list_tasks_attention_reason(tmp_path):
    c = _client(tmp_path)
    open_tid = c.post("/v1/tasks", json={"title": "o", "intent": "y",
                                         "tenant": "acme", "project": "site"}).json()["id"]
    blocked_tid = c.post("/v1/tasks", json={"title": "b", "intent": "y",
                                            "tenant": "acme", "project": "site"}).json()["id"]
    assert c.post(f"/v1/tasks/{blocked_tid}/transition",
                  json={"to": "blocked", "idempotency_key": "b1"}).status_code == 200
    c.post(f"/v1/tasks/{blocked_tid}/evidence",
           json={"kind": "escalation", "body": {"reason": "waiting on owner approval"}})
    review_tid = c.post("/v1/tasks", json={"title": "r", "intent": "y",
                                           "tenant": "acme", "project": "site"}).json()["id"]
    assert c.post(f"/v1/tasks/{review_tid}/transition",
                  json={"to": "claimed", "idempotency_key": "r1"}).status_code == 200
    assert c.post(f"/v1/tasks/{review_tid}/transition",
                  json={"to": "needs_review", "idempotency_key": "r2"}).status_code == 200
    c.post(f"/v1/tasks/{review_tid}/evidence",
           json={"kind": "reviewer", "body": {"verdict": "request_changes", "reason": "fix the flaky test"}})
    long_tid = c.post("/v1/tasks", json={"title": "l", "intent": "y",
                                         "tenant": "acme", "project": "site"}).json()["id"]
    assert c.post(f"/v1/tasks/{long_tid}/transition",
                  json={"to": "blocked", "idempotency_key": "l1"}).status_code == 200
    c.post(f"/v1/tasks/{long_tid}/evidence",
           json={"kind": "escalation", "body": {"reason": "x" * 500}})
    nested_tid = c.post("/v1/tasks", json={"title": "n", "intent": "y",
                                            "tenant": "acme", "project": "site"}).json()["id"]
    assert c.post(f"/v1/tasks/{nested_tid}/transition",
                  json={"to": "blocked", "idempotency_key": "n1"}).status_code == 200
    c.post(f"/v1/tasks/{nested_tid}/evidence",
           json={"kind": "escalation",
                 "body": {"fence": "escalate:attempt-3:rework_exhausted",
                          "payload": {"attempt_id": "attempt-3",
                                      "reason": "rework_exhausted"}}})
    fence_tid = c.post("/v1/tasks", json={"title": "f", "intent": "y",
                                          "tenant": "acme", "project": "site"}).json()["id"]
    assert c.post(f"/v1/tasks/{fence_tid}/transition",
                  json={"to": "claimed", "idempotency_key": "f0"}).status_code == 200
    assert c.post(f"/v1/tasks/{fence_tid}/transition",
                  json={"to": "failed", "idempotency_key": "f1"}).status_code == 200
    c.post(f"/v1/tasks/{fence_tid}/evidence",
           json={"kind": "escalation",
                 "body": {"fence": "escalate:attempt-1:E_MOVED", "payload": {}}})
    items = {t["id"]: t for t in c.get("/v1/tasks").json()["items"]}
    assert items[blocked_tid]["attention"] == "waiting on owner approval"
    assert items[review_tid]["attention"] == "fix the flaky test"
    assert items[open_tid]["attention"] is None
    assert len(items[long_tid]["attention"]) == 200
    assert items[nested_tid]["attention"] == "rework exhausted (attempt 3)"
    assert items[fence_tid]["attention"] == "E MOVED (attempt 1)"


def test_evidence_resend_returns_same_row(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "e", "intent": "y",
                                    "tenant": "acme", "project": "site"}).json()["id"]
    first = c.post(f"/v1/tasks/{tid}/evidence",
                   json={"kind": "note", "body": {"text": "same body"}}).json()
    second = c.post(f"/v1/tasks/{tid}/evidence",
                    json={"kind": "note", "body": {"text": "same body"}}).json()
    assert second["id"] == first["id"]
    assert second.get("replayed") is True
    rows = c.get(f"/v1/tasks/{tid}").json()["evidence"]
    assert [r["id"] for r in rows] == [first["id"]]


def test_task_detail_carries_attention(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "b", "intent": "y",
                                    "tenant": "acme", "project": "site"}).json()["id"]
    assert c.post(f"/v1/tasks/{tid}/transition",
                  json={"to": "blocked", "idempotency_key": "b1"}).status_code == 200
    c.post(f"/v1/tasks/{tid}/evidence",
           json={"kind": "escalation", "body": {"reason": "waiting on owner"}})
    detail = c.get(f"/v1/tasks/{tid}").json()
    assert detail["attention"] == "waiting on owner"
    assert "pipeline" in detail
