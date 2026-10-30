"""R3 contract suite: identical assertions, two backends.

Ledger arm runs everywhere (in-process ASGI app, tmp sqlite file).
Stage arm runs only with ROUVR_CONTRACT_LIVE=1 + WORKHUB_URL + WORKHUB_JWT +
SCRATCH_TASK_ID; its response-key mapping is ASSUMED until A1-live confirms it.
"""
import os
from pathlib import Path

import httpx
import pytest

from rouvr.errors import Code, RouvrError
from rouvr.workhub.client import WorkHubClient
from rouvr.workhub.ledger import LedgerWorkHub

pytestmark = pytest.mark.asyncio

LIVE = os.environ.get("ROUVR_CONTRACT_LIVE") == "1"


@pytest.fixture(params=["ledger"] + (["stage"] if LIVE else []))
async def backend(request, tmp_path: Path):
    if request.param == "ledger":
        from ledger.app import make_app
        app = make_app(f"sqlite:///{tmp_path}/ledger.db")
        wh = LedgerWorkHub("http://ledger", transport=httpx.ASGITransport(app=app))
        task = await wh.create_task("contract", "suite task")
        yield wh, str(task["id"]), task["id"]
        await wh.aclose()
    else:
        wh = WorkHubClient(os.environ["WORKHUB_URL"], os.environ.get("TENANT", "t"),
                           os.environ.get("PROJECT", "p"), os.environ["WORKHUB_JWT"])
        yield wh, os.environ["SCRATCH_TASK_ID"], os.environ.get("SCRATCH_TASK_NUMBER")


def norm_claim(r: dict) -> dict:
    # Stage keys ASSUMED — A1-live confirms or fixes this mapping, not the tests.
    return {"assignment_id": str(r.get("assignment_id") or r["assignment"]["id"]),
            "lease_epoch": int(r.get("lease_epoch") or r["assignment"]["lease_epoch"])}


def norm_renew(r: dict) -> dict:
    return {"lease_epoch": int(r.get("lease_epoch") or r["lease"]["lease_epoch"]),
            "expires_at": str(r.get("expires_at") or r["lease"]["expires_at"])}


def norm_attempt(r: dict) -> dict:
    return {"attempt_id": str(r.get("attempt_id") or r["attempt"]["id"]),
            "attempt_no": int(r.get("attempt_no") or r["attempt"]["attempt_no"]),
            "lease_epoch": int(r.get("lease_epoch") or r["attempt"]["lease_epoch"])}


async def test_claim_attempt_renew_result_flow(backend):
    wh, tid, _number = backend
    c = norm_claim(await wh.claim(tid, "coder-1", 1, 120))
    assert c["lease_epoch"] >= 1
    a = norm_attempt(await wh.open_attempt(tid, 1, c["lease_epoch"]))
    assert a["attempt_no"] == 1
    r1 = norm_renew(await wh.renew_lease(tid, c["assignment_id"], c["lease_epoch"], 120, 1))
    r2 = norm_renew(await wh.renew_lease(tid, c["assignment_id"], c["lease_epoch"], 300, 2))
    assert r2["expires_at"] >= r1["expires_at"]
    eid = await wh.attach_evidence_exact(tid, "pr_record", "temporal-handoff-1-e1", {"url": "u"})
    assert eid
    res = await wh.record_result(tid, a["attempt_id"], c["lease_epoch"], {"verdict": "approved"})
    assert res.get("result_id") or res.get("result", {}).get("id")


async def test_replays_return_same_row(backend):
    wh, tid, _number = backend
    c1 = norm_claim(await wh.claim(tid, "coder-1", 1, 120))
    a1 = norm_attempt(await wh.open_attempt(tid, 1, c1["lease_epoch"]))
    a2 = norm_attempt(await wh.open_attempt(tid, 1, c1["lease_epoch"]))
    assert a1["attempt_id"] == a2["attempt_id"]
    e1 = await wh.attach_evidence_exact(tid, "pr_record", "fence-dup", {"url": "u"})
    e2 = await wh.attach_evidence_exact(tid, "pr_record", "fence-dup", {"url": "u"})
    assert e1 == e2


async def test_stale_renew_and_live_reclaim_refused(backend):
    wh, tid, _number = backend
    c = norm_claim(await wh.claim(tid, "coder-1", 1, 120))
    with pytest.raises(RouvrError) as ei:
        await wh.renew_lease(tid, c["assignment_id"], c["lease_epoch"] + 99, 120, 9)
    assert ei.value.code is Code.MOVED
    with pytest.raises(RouvrError) as ei:
        await wh.reclaim_dead(tid, 1)
    assert ei.value.code is Code.MOVED


async def test_expired_lease_reclaims(backend):
    wh, tid, _number = backend
    await wh.claim(tid, "coder-1", 1, 0)  # ttl 0: already expired
    assert (await wh.reclaim_dead(tid, 1))["reclaimed"] is True


async def test_result_core_change_conflicts(backend):
    wh, tid, _number = backend
    c = norm_claim(await wh.claim(tid, "coder-1", 1, 120))
    a = norm_attempt(await wh.open_attempt(tid, 1, c["lease_epoch"]))
    await wh.record_result(tid, a["attempt_id"], c["lease_epoch"], {"verdict": "approved"})
    with pytest.raises(RouvrError) as ei:
        await wh.record_result(tid, a["attempt_id"], c["lease_epoch"], {"verdict": "rework"})
    assert ei.value.code is Code.MOVED


async def test_claim_replay_and_reclaim_reclaim_same_attempt(backend):
    wh, tid, _number = backend
    c1 = norm_claim(await wh.claim(tid, "coder-1", 1, 0))  # ttl 0: lease already expired
    c2 = norm_claim(await wh.claim(tid, "coder-1", 1, 0))  # deterministic key: replay, same row
    assert c1["assignment_id"] == c2["assignment_id"]
    assert (await wh.reclaim_dead(tid, 1))["reclaimed"] is True
    c4 = norm_claim(await wh.claim(tid, "coder-1", 1, 120))  # released row reactivated, no UNIQUE clash
    assert c4["assignment_id"] == c1["assignment_id"]


async def test_claim_by_other_principal_conflicts(backend):
    wh, tid, _number = backend
    await wh.claim(tid, "coder-1", 1, 120)
    with pytest.raises(RouvrError) as ei:  # same attempt, different principal: not a replay
        await wh.claim(tid, "coder-2", 1, 120)
    assert ei.value.code is Code.MOVED


@pytest.mark.parametrize("status,content,ctype", [
    (502, b"<html>bad gateway</html>", "text/html"),
    (500, b'["oops"]', "application/json"),
])
async def test_ledger_non_dict_error_bodies_become_rouvr_errors(status, content, ctype):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=content, headers={"content-type": ctype})

    wh = LedgerWorkHub("http://ledger", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RouvrError) as ei:
            await wh.create_task("t", "i")
        assert ei.value.code is Code.DOWNSTREAM
    finally:
        await wh.aclose()


async def test_binding_empty_before_claim(backend):
    wh, _tid, number = backend
    if number is None:
        pytest.skip("SCRATCH_TASK_NUMBER not set")
    assert await wh.get_binding_by_number(int(number)) == {}
