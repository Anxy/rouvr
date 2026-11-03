"""G1: a blocked task ends cleanly as blocked WITH an escalation record.

Proven live: real TaskWorkflow on Temporal's test server, real
mark_blocked + escalate activities, real lite ledger over HTTP.
Only the upstream failure is doubled (verify_binding refusing) — the
block path, the transition, and the escalation evidence are all real.
"""
import asyncio
import uuid

import httpx
import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from types import SimpleNamespace

from rouvr.activities import Activities
from rouvr.contracts import TaskWorkflowInput
from rouvr.workflows import Q_ORCH, TaskWorkflow
from rouvr.workhub.ledger import LedgerWorkHub


@pytest.fixture(scope="module")
def live_ledger(tmp_path_factory):
    import threading

    import uvicorn

    from env_scope import scoped_env

    with scoped_env(ROUVR_INSECURE_DEV="1", ROUVR_LEDGER_TOKEN=None):
        from ledger.app import make_app

        db = tmp_path_factory.mktemp("g1ledger") / "g1.db"
        config = uvicorn.Config(make_app(f"sqlite:///{db}"), host="127.0.0.1",
                                port=18223, log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            try:
                if httpx.get("http://127.0.0.1:18223/health", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            import time; time.sleep(0.1)
        yield "http://127.0.0.1:18223"
        server.should_exit = True


@activity.defn(name="verify_binding")
async def refusing_binding(wf: TaskWorkflowInput):
    raise ApplicationError("binding refused upstream", type="E_AUTHZ")


async def test_blocked_task_carries_live_escalation_record(live_ledger):
    c = httpx.Client(base_url=live_ledger, timeout=10)
    c.post("/v1/tenants", json={"tenant": "acme"}).raise_for_status()
    c.post("/v1/projects", json={"tenant": "acme", "project": "site"}).raise_for_status()
    task = c.post("/v1/tasks", json={"title": "g1-block", "intent": "prove block",
                                     "tenant": "acme", "project": "site"}).json()
    tid = str(task["id"])

    host = SimpleNamespace(
        workhub_for=lambda persona: LedgerWorkHub(live_ledger),
        settings=SimpleNamespace(workspace_root=None))
    acts = Activities(host)  # real mark_blocked + escalate, no stubs
    wf = TaskWorkflowInput("acme", "site", tid, "rev1", "owner", max_rework=0)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        worker = Worker(env.client, task_queue=Q_ORCH, workflows=[TaskWorkflow],
                        activities=[refusing_binding, acts.mark_blocked, acts.escalate])
        async with worker:
            result = await env.client.execute_workflow(
                TaskWorkflow.run, wf, id=str(uuid.uuid4()), task_queue=Q_ORCH)

    assert result == {"state": "blocked", "reason": "E_AUTHZ", "attempts": 0}
    state = c.get(f"/v1/tasks/{tid}").json()
    assert state["state"] == "blocked"
    kinds = [e["kind"] for e in state["evidence"]]
    assert "escalation" in kinds
    esc = next(e for e in state["evidence"] if e["kind"] == "escalation")
    import json as _json
    body = _json.loads(esc["body"]) if isinstance(esc["body"], str) else esc["body"]
    assert body.get("payload", {}).get("reason") == "E_AUTHZ"
    await asyncio.sleep(0)
