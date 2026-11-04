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


def _claim(c, tid, key, principal="coder-1"):
    return c.post(f"/v1/tasks/{tid}/assignments/claim",
                  json={"key": key, "principal": principal,
                        "attempt_no": 1, "ttl_s": 120})


def _row(c, tid):
    items = {t["id"]: t for t in c.get("/v1/tasks").json()["items"]}
    return items[tid]


def test_pipeline_bare_task_is_all_pending(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "bare", "intent": "y",
                                    "tenant": "acme", "project": "site"}).json()["id"]
    assert _row(c, tid)["pipeline"] == {
        "contract": "pending", "knowledge": "pending", "build": "pending",
        "review": "pending", "evidence": "pending"}


def test_pipeline_contract_knowledge_build_review_evidence(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "full", "intent": "y",
                                    "tenant": "acme", "project": "site"}).json()["id"]
    assert c.post(f"/v1/tasks/{tid}/contracts",
                  json={"acceptance_criteria": ["ships"],
                        "plan_slug": "site-plan", "category": "feature"}).status_code == 200
    assert _row(c, tid)["pipeline"] == {
        "contract": "done", "knowledge": "done", "build": "pending",
        "review": "pending", "evidence": "pending"}
    c.post(f"/v1/tasks/{tid}/evidence",
           json={"kind": "tests", "body": {"passed": 3, "total": 3}})
    assert _row(c, tid)["pipeline"] == {
        "contract": "done", "knowledge": "done", "build": "done",
        "review": "pending", "evidence": "done"}
    c.post(f"/v1/tasks/{tid}/evidence",
           json={"kind": "reviewer", "body": {"verdict": "approve"}})
    assert _row(c, tid)["pipeline"] == {
        "contract": "done", "knowledge": "done", "build": "done",
        "review": "done", "evidence": "done"}
    # Detail endpoint carries the same object, computed from full rows.
    assert c.get(f"/v1/tasks/{tid}").json()["pipeline"] == \
        _row(c, tid)["pipeline"]


def test_pipeline_negative_review_and_result_flag_attention(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "neg", "intent": "y",
                                    "tenant": "acme", "project": "site"}).json()["id"]
    c.post(f"/v1/tasks/{tid}/contracts", json={"acceptance_criteria": ["ships"]})
    c.post(f"/v1/tasks/{tid}/evidence",
           json={"kind": "reviewer",
                 "body": {"verdict": "request_changes", "reason": "flaky"}})
    assert _row(c, tid)["pipeline"]["review"] == "attention"
    # Contract without plan linkage stays knowledge-pending (honest).
    assert _row(c, tid)["pipeline"]["knowledge"] == "pending"
    assert _claim(c, tid, "claim-1").status_code == 200
    att = c.post(f"/v1/tasks/{tid}/attempts",
                 json={"key": "att-1", "attempt_no": 1,
                       "lease_epoch": 1}).json()["attempt"]
    assert c.post(f"/v1/tasks/{tid}/results",
                  json={"key": "res-1", "attempt_id": att["id"],
                        "lease_epoch": 1, "verdict": "fail",
                        "result": {}}).status_code == 200
    assert _row(c, tid)["pipeline"]["build"] == "attention"
