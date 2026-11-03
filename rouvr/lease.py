"""rouvr/lease.py — lease + fencing. The epoch returned by claim is the fencing token; every write
after claim passes it, and WorkHub rejects stale epochs with 409 (=> E_MOVED, non-retryable)."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from rouvr.contracts import Lease
from rouvr.errors import Code, RouvrError
from rouvr.workhub.port import WorkHubPort


def attempt_base(task: dict) -> int:
    """Highest attempt_no already receipted on this task (0 when none).

    A reopened task starts its new run at base + workflow-attempt so the
    ledger's per-(task, attempt_no) uniqueness (assignments, attempts) and
    per-attempt results never collide with previous runs' rows — which
    otherwise replay stale leases (E_MOVED) or 409 as REPLAY_CONFLICT.
    Receipt bodies may be JSON strings or dicts; anything else is skipped.
    """
    top = 0
    for r in task.get("receipts") or []:
        if not isinstance(r, dict) or r.get("action") != "attempt":
            continue
        body = r.get("body") or {}
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                continue
        if not isinstance(body, dict):
            continue
        try:
            top = max(top, int(body.get("attempt_no") or 0))
        except (TypeError, ValueError):
            continue
    return top


async def claim_assignment_and_lease(wh: WorkHubPort, task_id: str, principal: str,
                                     attempt_no: int, ttl_s: int, run_id: str = "") -> tuple[Lease, str]:
    body = await wh.claim(task_id, principal, attempt_no, ttl_s, run_id=run_id)
    if body.get("holder") not in (None, principal):
        raise RouvrError(Code.MOVED, f"lease held by {body.get('holder')}")
    lease = Lease(task_id=task_id, holder=principal, lease_epoch=int(body["lease_epoch"]),
                  attempt_no=attempt_no, attempt_id="", expires_at=str(body["expires_at"]),
                  assignment_id=str(body["assignment_id"]))
    return lease, lease.assignment_id


async def open_or_reuse_attempt(wh: WorkHubPort, lease: Lease, run_id: str = "") -> Lease:
    body = await wh.open_attempt(lease.task_id, lease.attempt_no, lease.lease_epoch,  # idempotent by key
                                 lease.assignment_id, run_id=run_id)
    lease.attempt_id = str(body["attempt_id"])
    return lease


async def fresh_claim(wh: WorkHubPort, task_id: str, principal: str,
                      attempt_no: int, ttl_s: int, run_id: str = "") -> tuple[Lease, str]:
    """First claim of an attempt: claim, releasing any leftover assignment
    first only when the claim 409s on it. A live lease refuses (E_MOVED):
    never steal another run's work. Reclaim-first would refuse a same-run
    rework whose previous attempt just finished (lease still within TTL).
    """
    try:
        return await claim_assignment_and_lease(wh, task_id, principal, attempt_no, ttl_s, run_id=run_id)
    except RouvrError as e:
        if e.code is not Code.MOVED:
            raise
    # Occupied by a dead leftover: release it, then claim for real. A live
    # lease 409s here again, which correctly refuses the steal.
    await wh.reclaim_dead(task_id, attempt_no, principal)
    return await claim_assignment_and_lease(wh, task_id, principal, attempt_no, ttl_s, run_id=run_id)


async def resume_or_claim(wh: WorkHubPort, task_id: str, task_number: int, principal: str,
                          attempt_no: int, ttl_s: int, run_id: str = "") -> tuple[Lease, str]:
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
    return await claim_assignment_and_lease(wh, task_id, principal, attempt_no, ttl_s, run_id=run_id)


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
