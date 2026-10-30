"""R5: claim -> attempt -> fenced work through rouvr/lease.py against the ledger arm."""
import asyncio

import httpx
import pytest

from ledger.app import make_app
from rouvr.errors import Code, RouvrError
from rouvr.lease import (LeaseKeeper, claim_assignment_and_lease, open_or_reuse_attempt, resume_or_claim,
                         run_fenced)
from rouvr.workhub.ledger import LedgerWorkHub


async def test_claim_attempt_fenced_flow(tmp_path):
    app = make_app(f"sqlite:///{tmp_path}/ledger.db")
    wh = LedgerWorkHub("http://ledger", transport=httpx.ASGITransport(app=app))
    task = await wh.create_task("r5", "ownership")
    tid = str(task["id"])
    lease, asg = await claim_assignment_and_lease(wh, tid, "coder-1", 1, 120)
    assert lease.lease_epoch == 1 and asg
    lease = await open_or_reuse_attempt(wh, lease)
    assert lease.attempt_id
    keeper = LeaseKeeper(wh, lease, asg, ttl_s=120, every_s=30)
    assert await run_fenced(keeper, asyncio.sleep(0.01, result="did-work")) == "did-work"
    await wh.aclose()


class FakePort:
    def __init__(self, binding, renew=None):
        self.binding, self._renew, self.calls = binding, renew, []

    async def get_binding_by_number(self, number):
        return self.binding

    async def renew_lease(self, *a):
        self.calls.append("renew")
        if isinstance(self._renew, Exception):
            raise self._renew
        return self._renew

    async def reclaim_dead(self, *a):
        self.calls.append("reclaim")
        return {"reclaimed": True}

    async def claim(self, task_id, principal, attempt_no, ttl_s):
        self.calls.append("claim")
        return {"assignment_id": "a2", "lease_epoch": 2, "expires_at": "e", "holder": principal}


async def test_resume_renews_live_lease():
    wh = FakePort({"assignment_id": "a1", "lease_epoch": 1, "holder": "b"},
                  {"lease_epoch": 1, "expires_at": "e"})
    lease, asg = await resume_or_claim(wh, "t", 7, "b", 1, 120)
    assert (asg, lease.lease_epoch) == ("a1", 1) and wh.calls == ["renew"]


async def test_resume_falls_back_to_reclaim_claim():
    wh = FakePort({"assignment_id": "a1", "lease_epoch": 1, "holder": "b"},
                  RouvrError(Code.MOVED, "expired"))
    lease, asg = await resume_or_claim(wh, "t", 7, "b", 1, 120)
    assert (asg, lease.lease_epoch) == ("a2", 2) and wh.calls == ["renew", "reclaim", "claim"]


async def test_resume_claims_when_unheld():
    wh = FakePort({})
    lease, asg = await resume_or_claim(wh, "t", 7, "b", 1, 120)
    assert (asg, lease.lease_epoch) == ("a2", 2) and wh.calls == ["reclaim", "claim"]


async def test_resume_propagates_non_moved_renew_errors():
    wh = FakePort({"assignment_id": "a1", "lease_epoch": 1, "holder": "b"},
                  RouvrError(Code.AUTHZ, "no"))
    with pytest.raises(RouvrError) as e:
        await resume_or_claim(wh, "t", 7, "b", 1, 120)
    assert e.value.code is Code.AUTHZ and wh.calls == ["renew"]
