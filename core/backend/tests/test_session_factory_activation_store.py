from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
import pytest
from backend.session_factory.activation_store import (
    ActivationStoreCorrupt,
    PostgresActivationStore,
)


class Tx:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        await self.c.lock.acquire()
        return self

    async def __aexit__(self, t, v, b):
        self.c.rollbacks += int(t is not None)
        self.c.lock.release()


class FakeConn:
    def __init__(self, state):
        self.state = state
        self.lock = state.setdefault("lock", asyncio.Lock())
        self.rollbacks = 0

    def transaction(self):
        return Tx(self)

    async def close(self):
        pass

    async def execute(self, q, *a):
        if q.startswith("INSERT INTO aix_session_releases"):
            self.state.setdefault("releases", {}).setdefault(
                a[0],
                {
                    "record": a[1],
                    "state": "retryable",
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "result": None,
                },
            )
            return "INSERT 1"
        if "SET state='claimed'" in q:
            r = self.state["releases"][a[0]]
            r.update(
                state="claimed",
                lease_owner=a[1],
                lease_token=a[2],
                lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=a[3]),
            )
            return "UPDATE 1"
        if "SET state='retryable'" in q:
            r = self.state["releases"][a[0]]
            if r["lease_owner"] == a[1] and r["lease_token"] == a[2]:
                r.update(
                    state="retryable",
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                )
            return "UPDATE 1"
        return "UPDATE 1"

    async def fetchrow(self, q, *a):
        if self.state.get("fail_read"):
            raise RuntimeError("injected read failure")
        if "FROM aix_session_releases" in q:
            return self.state.get("releases", {}).get(a[0])
        if "FROM aix_session_activations" in q:
            return self.state.get("activation")
        raise RuntimeError("injected read failure")

    async def fetchval(self, q, *a):
        if q.startswith("INSERT INTO aix_session_activations"):
            return False
        if q.startswith("UPDATE aix_session_activations SET status="):
            row = self.state.get("activation")
            if row is not None and row.get("status") == "STARTING":
                row["status"] = a[1]
                return True
            return None
        if "SET state='completed'" in q:
            r = self.state["releases"][a[0]]
            if (
                r["state"] == "claimed"
                and r["lease_owner"] == a[1]
                and r["lease_token"] == a[2]
            ):
                r.update(
                    state="completed",
                    result=a[3],
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                )
                return True
            return None
        return None


class Factory:
    def __init__(self, state):
        self.state = state
        self.connections = []

    async def __call__(self):
        c = FakeConn(self.state)
        self.connections.append(c)
        return c


def store(state, owner):
    f = Factory(state)
    return PostgresActivationStore(
        "postgresql://test", lease_owner=owner, lease_seconds=10, connect=f
    ), f


@pytest.mark.asyncio
async def test_live_lease_blocks_other_owner_and_expired_lease_is_taken_over():
    state = {
        "releases": {
            "r": {
                "record": '{"x": 1}',
                "state": "claimed",
                "lease_owner": "one",
                "lease_token": "old-token",
                "lease_expires_at": datetime.now(timezone.utc) + timedelta(seconds=30),
                "result": None,
            }
        }
    }
    two, _ = store(state, "two")
    assert (await two.claim_release("r", {"x": 1}))[0] == "busy"
    state["releases"]["r"]["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    assert (await two.claim_release("r", {"x": 1}))[0] == "acquired"
    assert state["releases"]["r"]["lease_owner"] == "two"


@pytest.mark.asyncio
async def test_process_restart_same_owner_reclaims_and_completed_result_reads_back():
    state = {}
    first, _ = store(state, "worker")
    status, _, _ = await first.claim_release("r", {"x": 1})
    assert status == "acquired"
    restarted, _ = store(state, "worker")
    state["releases"]["r"]["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    status, _, token = await restarted.claim_release("r", {"x": 1})
    assert status == "acquired" and token is not None
    await restarted.mark_release_completed("r", token, {"receipt": "ok"})
    status, row, _ = await first.claim_release("r", {"x": 1})
    assert status == "completed" and row["release_result"]["receipt"] == "ok"


@pytest.mark.asyncio
async def test_concurrent_duplicate_delivery_has_one_owner_and_binding_conflict_refuses():
    state = {}
    one, _ = store(state, "one")
    two, _ = store(state, "two")
    results = await asyncio.gather(
        one.claim_release("r", {"x": 1}), two.claim_release("r", {"x": 1})
    )
    assert sorted(x[0] for x in results) == ["acquired", "busy"]
    with pytest.raises(ActivationStoreCorrupt, match="REPLAY_CONFLICT"):
        await one.claim_release("r", {"x": 2})


@pytest.mark.asyncio
async def test_corrupt_activation_row_fails_and_transaction_rolls_back():
    state = {"activation": {"binding": "[]", "status": "STARTING", "result": None}}
    subject, factory = store(state, "one")
    with pytest.raises(ActivationStoreCorrupt, match="ROW_CORRUPT"):
        await subject.claim_activation("a", {"max_repairs": 1})
    assert (
        factory.connections[-1].rollbacks == 0
    )  # corruption is detected after committed read
    broken_state = {"fail_read": True}
    broken, factory2 = store(broken_state, "one")
    with pytest.raises(RuntimeError, match="injected read failure"):
        await broken.claim_activation("a", {"max_repairs": 1})
    assert factory2.connections[-1].rollbacks == 1


@pytest.mark.asyncio
async def test_same_owner_cross_store_is_busy_and_stale_token_cannot_complete():
    state = {}
    one, _ = store(state, "shared-owner")
    two, _ = store(state, "shared-owner")
    status, _, first_token = await one.claim_release("r", {"x": 1})
    assert status == "acquired" and first_token is not None
    first_token = state["releases"]["r"]["lease_token"]
    assert (await two.claim_release("r", {"x": 1}))[0] == "busy"
    state["releases"]["r"]["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    status, _, second_token = await two.claim_release("r", {"x": 1})
    assert status == "acquired" and second_token is not None
    assert second_token != first_token
    with pytest.raises(ActivationStoreCorrupt, match="LEASE_LOST"):
        await one.mark_release_completed("r", first_token, {"stale": True})
    await one.abandon_release("r", first_token)
    assert state["releases"]["r"]["state"] == "claimed"
    await two.mark_release_completed("r", second_token, {"fresh": True})
    assert state["releases"]["r"]["state"] == "completed"


@pytest.mark.asyncio
async def test_corrupt_temporal_result_is_never_merged_into_binding():
    state = {
        "activation": {
            "binding": '{"activation_id":"a","status":"STARTING"}',
            "status": "STARTED",
            "result": "[]",
        }
    }
    subject, _ = store(state, "one")
    with pytest.raises(ActivationStoreCorrupt, match="ROW_CORRUPT"):
        await subject.get_activation("a")


@pytest.mark.asyncio
async def test_same_store_stale_invocation_cannot_complete_or_abandon_takeover():
    state = {}
    subject, _ = store(state, "one")
    _, _, stale_token = await subject.claim_release("r", {"x": 1})
    state["releases"]["r"]["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    _, _, current_token = await subject.claim_release("r", {"x": 1})
    assert stale_token and current_token and stale_token != current_token
    with pytest.raises(ActivationStoreCorrupt, match="LEASE_LOST"):
        await subject.mark_release_completed("r", stale_token, {"stale": True})
    await subject.abandon_release("r", stale_token)
    assert state["releases"]["r"]["state"] == "claimed"
    await subject.mark_release_completed("r", current_token, {"fresh": True})


@pytest.mark.asyncio
async def test_result_marks_activation_terminal_exactly_once():
    # Lane-ops #23 R5: the recorded result transitions the activation
    # itself so supervision observes terminality. The first terminal
    # status wins; a same-status replay is a no-op; a conflicting
    # terminal status refuses without mutating.
    state = {"activation": {"status": "STARTING"}}
    subject, _ = store(state, "one")
    await subject.mark_activation_result("a", "SUCCEEDED")
    assert state["activation"]["status"] == "SUCCEEDED"
    await subject.mark_activation_result("a", "SUCCEEDED")
    assert state["activation"]["status"] == "SUCCEEDED"
    with pytest.raises(ValueError, match="ACTIVATION_RESULT_REPLAY_CONFLICT"):
        await subject.mark_activation_result("a", "FAILED")
    assert state["activation"]["status"] == "SUCCEEDED"
    with pytest.raises(ValueError, match="ACTIVATION_RESULT_STATUS_INVALID"):
        await subject.mark_activation_result("a", "STARTED")


@pytest.mark.asyncio
async def test_concurrent_expired_takeover_on_same_store_has_one_token():
    state = {}
    subject, _ = store(state, "one")
    await subject.claim_release("r", {"x": 1})
    state["releases"]["r"]["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    results = await asyncio.gather(
        subject.claim_release("r", {"x": 1}),
        subject.claim_release("r", {"x": 1}),
    )
    assert sorted(result[0] for result in results) == ["acquired", "busy"]
    tokens = [result[2] for result in results if result[0] == "acquired"]
    assert len(tokens) == 1 and tokens[0]
