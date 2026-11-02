"""Fresh workflow runs must not collide with previous runs' attempt rows.

The ledger enforces one active assignment and one result per
(task, attempt_no), keyed receipts replay same-key retries, and a reopened
task restarts numbering at workflow attempt 1. So the run numbers past
previous runs (max receipted attempt_no + 1, see attempt_base): reusing
attempt 1 would 409 on the claim or replay a stale result row as
REPLAY_CONFLICT. Same-key retries still replay the stored row.
"""
import json
import os

import httpx
import pytest

os.environ["ROUVR_INSECURE_DEV"] = "1"
os.environ.pop("ROUVR_LEDGER_TOKEN", None)

from fastapi.testclient import TestClient

from ledger.app import make_app
from rouvr.errors import Code, RouvrError
from rouvr.lease import attempt_base, fresh_claim
from rouvr.workhub.ledger import LedgerWorkHub

pytestmark = pytest.mark.asyncio


def _client(tmp_path):
    c = TestClient(make_app(f"sqlite:///{tmp_path}/l.db"))
    assert c.post("/v1/tenants", json={"tenant": "acme"}).status_code == 200
    assert c.post("/v1/projects", json={"tenant": "acme", "project": "site"}).status_code == 200
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y",
                                    "tenant": "acme", "project": "site"}).json()["id"]
    return c, tid


def _claim(c, tid, key, attempt_no=1, ttl_s=120):
    r = c.post(f"/v1/tasks/{tid}/assignments/claim",
               json={"key": key, "principal": "coder-1", "attempt_no": attempt_no, "ttl_s": ttl_s})
    assert r.status_code == 200, r.text
    return r.json()


def _attempt(c, tid, key, attempt_no, epoch):
    r = c.post(f"/v1/tasks/{tid}/attempts",
               json={"key": key, "attempt_no": attempt_no, "lease_epoch": epoch})
    assert r.status_code == 200, r.text
    return r.json()["attempt"]


def _result(c, tid, key, attempt_id, epoch, verdict):
    return c.post(f"/v1/tasks/{tid}/results",
                  json={"key": key, "attempt_id": attempt_id, "lease_epoch": epoch,
                        "verdict": verdict, "result": {"ok": verdict == "pass"}})


def _reclaim(c, tid, key):
    r = c.post(f"/v1/tasks/{tid}/assignments/reclaim", json={"key": key})
    assert r.status_code == 200, r.text
    return r.json()


def test_UNUSED_attempt_no_mints_new_row(tmp_path):
    # Guard: an unused attempt number still creates a fresh row.
    c, tid = _client(tmp_path)
    lease = _claim(c, tid, "claim-A")["lease_epoch"]
    first = _attempt(c, tid, "att-A", 1, lease)
    assert _result(c, tid, "res-A", first["id"], lease, "pass").status_code == 200


def test_same_key_replays_same_row(tmp_path):
    # Guard: retry safety — the same idempotency key returns the stored row.
    c, tid = _client(tmp_path)
    lease = _claim(c, tid, "claim-A")["lease_epoch"]
    first = _attempt(c, tid, "att-A", 1, lease)
    again = c.post(f"/v1/tasks/{tid}/attempts",
                   json={"key": "att-A", "attempt_no": 1, "lease_epoch": lease}).json()
    assert again["attempt"]["id"] == first["id"]


def test_replay_conflict_still_fires_on_same_attempt(tmp_path):
    # Guard: two different verdicts on the same attempt row still 409 —
    # the fencing the offset numbering preserves.
    c, tid = _client(tmp_path)
    lease = _claim(c, tid, "claim-A")["lease_epoch"]
    first = _attempt(c, tid, "att-A", 1, lease)
    assert _result(c, tid, "res-A", first["id"], lease, "pass").status_code == 200
    r = _result(c, tid, "res-B", first["id"], lease, "fail")
    assert r.status_code == 409


def _task_with(receipt_rows):
    return {"id": "9", "receipts": receipt_rows}


def _att_row(no):
    return {"id": no, "action": "attempt", "body": json.dumps({"id": no, "attempt_no": no})}


def test_attempt_base_empty():
    assert attempt_base({}) == 0
    assert attempt_base({"id": "9"}) == 0
    assert attempt_base(_task_with([{"id": 1, "action": "claim", "body": "{}"}])) == 0


def test_attempt_base_max_of_rows():
    rows = [_att_row(1), _att_row(3), _att_row(2),
            {"id": 9, "action": "pr_record", "body": "{}"}]
    assert attempt_base(_task_with(rows)) == 3


def test_attempt_base_ignores_malformed():
    rows = [_att_row(2),
            {"id": 3, "action": "attempt", "body": "not-json"},
            {"id": 4, "action": "attempt", "body": json.dumps({"attempt_no": "x"})},
            {"id": 5, "action": "attempt"},
            {"id": 6, "action": "attempt", "body": {"attempt_no": 7}},
            "junk"]
    assert attempt_base(_task_with(rows)) == 7


async def _wh(tmp_path):
    app = make_app(f"sqlite:///{tmp_path}/ledger.db")
    wh = LedgerWorkHub("http://ledger", transport=httpx.ASGITransport(app=app))
    c = TestClient(app)
    assert c.post("/v1/tenants", json={"tenant": "acme"}).status_code == 200
    assert c.post("/v1/projects", json={"tenant": "acme", "project": "site"}).status_code == 200
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y",
                                    "tenant": "acme", "project": "site"}).json()["id"]
    return wh, tid


async def test_fresh_claim_releases_then_claims(tmp_path):
    import asyncio
    wh, tid = await _wh(tmp_path)
    first, _ = await fresh_claim(wh, tid, "coder-1", 1, 2, run_id="run-A")
    # Stale leftover from the previous run must not block the next run.
    await asyncio.sleep(2.5)  # let the previous run's lease lapse first
    second, _ = await fresh_claim(wh, tid, "coder-1", 1, 120, run_id="run-B")
    assert second.lease_epoch == first.lease_epoch + 1
    await wh.aclose()


async def test_fresh_claim_refuses_live_lease(tmp_path):
    wh, tid = await _wh(tmp_path)
    await fresh_claim(wh, tid, "coder-1", 1, 120, run_id="run-A")
    try:
        await fresh_claim(wh, tid, "coder-1", 1, 120, run_id="run-B")
    except RouvrError as e:
        assert e.code is Code.MOVED
    else:
        raise AssertionError("live lease was stolen")
    await wh.aclose()
