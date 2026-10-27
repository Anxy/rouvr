from fastapi.testclient import TestClient

from ledger.stub_workhub.app import app


def test_roundtrip():
    c = TestClient(app)
    assert c.get("/health").json()["stub"] is True
    t = c.post("/v1/tasks", json={"title": "demo", "status": "ready"}).json()
    assert c.patch(f"/v1/tasks/{t['id']}", json={"status": "done"}).json()["status"] == "done"
    assert len(c.get("/v1/tasks").json()["items"]) == 1
