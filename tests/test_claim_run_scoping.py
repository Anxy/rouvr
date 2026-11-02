"""Claim/attempt idempotency keys must be scoped per workflow run.

A reopened task starts a new workflow run at attempt 1 again. If the claim
key is only (task, attempt, principal), the fresh run replays the previous
run's dead lease and every write 409s (E_MOVED) with no new evidence --
the run blocks in under a minute. Each run must mint fresh keys.
"""
from rouvr.ids import idempotency_key
from rouvr.lease import claim_assignment_and_lease, open_or_reuse_attempt


class ReplayClaimWH:
    """Mimics the ledger claim/attempt endpoints: a seen idempotency key
    replays the stored row without changing state; only a fresh key mints
    a new assignment/lease epoch (the fencing token)."""

    def __init__(self):
        self.used = {}
        self.epoch = 0
        self.attempts = 0

    async def claim(self, task_id, principal, attempt_no, ttl_s, run_id=""):
        key = idempotency_key("claim", task_id, attempt_no, principal, run_id)
        if key in self.used:
            return dict(self.used[key])
        self.epoch += 1
        row = {"assignment_id": "a%d" % self.epoch, "lease_epoch": self.epoch,
               "expires_at": "2999-01-01T00:00:00+00:00", "holder": principal}
        self.used[key] = row
        return dict(row)

    async def open_attempt(self, task_id, attempt_no, epoch, assignment_id="", run_id=""):
        key = idempotency_key("attempt", task_id, attempt_no, run_id)
        if key in self.used:
            return dict(self.used[key])
        self.attempts += 1
        row = {"attempt_id": "att%d" % self.attempts, "attempt_no": attempt_no,
               "lease_epoch": epoch}
        self.used[key] = row
        return dict(row)


async def test_claim_across_runs_mints_fresh_lease():
    wh = ReplayClaimWH()
    first, _ = await claim_assignment_and_lease(wh, "t", "coder-1", 1, 120, run_id="run-A")
    second, _ = await claim_assignment_and_lease(wh, "t", "coder-1", 1, 120, run_id="run-B")
    assert first.lease_epoch == 1
    assert second.lease_epoch == 2  # not run-A's dead lease replayed
    assert second.assignment_id != first.assignment_id


async def test_open_attempt_across_runs_mints_fresh_attempt():
    wh = ReplayClaimWH()
    first, _ = await claim_assignment_and_lease(wh, "t", "coder-1", 1, 120, run_id="run-A")
    first = await open_or_reuse_attempt(wh, first, run_id="run-A")
    second, _ = await claim_assignment_and_lease(wh, "t", "coder-1", 1, 120, run_id="run-B")
    second = await open_or_reuse_attempt(wh, second, run_id="run-B")
    assert first.attempt_id != second.attempt_id  # not run-A's attempt replayed
