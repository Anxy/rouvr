"""rouvr/lease.py — lease + fencing. The epoch returned by claim is the fencing token; every write
after claim passes it, and WorkHub rejects stale epochs with 409 (=> E_MOVED, non-retryable)."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from rouvr.contracts import Lease
from rouvr.errors import Code, RouvrError
from rouvr.workhub.port import WorkHubPort


async def claim_assignment_and_lease(wh: WorkHubPort, task_id: str, principal: str,
                                     attempt_no: int, ttl_s: int) -> tuple[Lease, str]:
    body = await wh.claim(task_id, principal, attempt_no, ttl_s)
    if body.get("holder") not in (None, principal):
        raise RouvrError(Code.MOVED, f"lease held by {body.get('holder')}")
    lease = Lease(task_id=task_id, holder=principal, lease_epoch=int(body["lease_epoch"]),
                  attempt_no=attempt_no, attempt_id="", expires_at=str(body["expires_at"]),
                  assignment_id=str(body["assignment_id"]))
    return lease, lease.assignment_id


async def open_or_reuse_attempt(wh: WorkHubPort, lease: Lease) -> Lease:
    body = await wh.open_attempt(lease.task_id, lease.attempt_no, lease.lease_epoch,  # idempotent by key
                                 lease.assignment_id)
    lease.attempt_id = str(body["attempt_id"])
    return lease


async def resume_or_claim(wh: WorkHubPort, task_id: str, task_number: int, principal: str,
                          attempt_no: int, ttl_s: int) -> tuple[Lease, str]:
    """Activity-retry entry (R5/R10): the holder renews its live lease (fast path,
    same epoch); otherwise reclaim the dead lease and claim fresh on the same attempt."""
    b = await wh.get_binding_by_number(task_number)
    if b and b.get("holder") == principal and b.get("lease_epoch") is not None:
        try:
            r = await wh.renew_lease(task_id, str(b["assignment_id"]), int(b["lease_epoch"]), ttl_s, 0)
            lease = Lease(task_id=task_id, holder=principal, lease_epoch=int(r["lease_epoch"]),
                          attempt_no=attempt_no, attempt_id="", expires_at=str(r["expires_at"]),
                          assignment_id=str(b["assignment_id"]))
            return lease, lease.assignment_id
        except RouvrError as e:
            if e.code is not Code.MOVED:
                raise
    await wh.reclaim_dead(task_id, attempt_no, principal)
    return await claim_assignment_and_lease(wh, task_id, principal, attempt_no, ttl_s)


@dataclass
class LeaseKeeper:
    """Background renewer. Runs inside the assign_task activity alongside Temporal heartbeats.
    If renewal ever fails with E_MOVED, `lost` is set and the caller must stop the Muse process."""
    wh: WorkHubPort
    lease: Lease
    assignment_id: str
    ttl_s: int
    every_s: int
    on_beat: Callable[[int], None] = lambda n: None
    lost: asyncio.Event | None = None
    _task: asyncio.Task | None = None

    def start(self) -> None:
        self.lost = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        beat = 0
        assert self.lost is not None
        while True:
            await asyncio.sleep(self.every_s)
            beat += 1
            try:
                await self.wh.renew_lease(self.lease.task_id, self.assignment_id, self.lease.lease_epoch,
                                          self.ttl_s, beat)
                self.on_beat(beat)
            except RouvrError as e:
                if e.code in (Code.MOVED, Code.AUTHZ):
                    self.lost.set()
                    return
                # transient: try again next tick; lease TTL (>=3 beats) absorbs one miss
            except Exception:
                pass  # transient: unknown renew errors must not kill fencing silently

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass  # never mask the work's result with the keeper's failure
            cur = asyncio.current_task()
            if cur is not None and cur.cancelling():
                raise asyncio.CancelledError()  # caller was cancelled while awaiting us: don't swallow it


async def run_fenced(keeper: LeaseKeeper, work: Awaitable[object]) -> object:
    """Run `work` but abort as soon as the lease is lost."""
    keeper.start()
    assert keeper.lost is not None
    w = asyncio.ensure_future(work)
    lost = asyncio.ensure_future(keeper.lost.wait())
    try:
        done, _ = await asyncio.wait({w, lost}, return_when=asyncio.FIRST_COMPLETED)
        if lost in done:
            w.cancel()
            raise RouvrError(Code.MOVED, "lease lost during work")
        return w.result()
    finally:
        lost.cancel()
        await keeper.stop()
