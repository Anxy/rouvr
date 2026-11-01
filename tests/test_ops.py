"""T3/T4/T5 acceptance tests: governed ops path, validated contract + judge, grants + schedule."""
import asyncio
import hashlib
import importlib
import json
import uuid

from rouvr.contracts import OpsInput

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

RUN_KEY = "morning-report:2026-10-30"
INSTRUCTION = "Compile yesterday's lane report and publish it."


@pytest.fixture
def ops_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_DEMO_OUTBOX", str(tmp_path / "outbox"))
    monkeypatch.setenv("ROUVR_LEDGER_DATABASE_URL", f"sqlite:///{tmp_path}/ops.db")
    monkeypatch.setenv("ROUVR_MODEL_PROVIDER", "stub")
    monkeypatch.delenv("ROUVR_LEDGER_URL", raising=False)
    monkeypatch.delenv("ROUVR_INJECT", raising=False)
    import demo.morning_report as m
    m = importlib.reload(m)
    c = m.ledger_client(None)  # S022: register the partition ops tasks run inside
    c.post("/v1/tenants", json={"tenant": "acme"}).raise_for_status()
    c.post("/v1/projects", json={"tenant": "acme", "project": "site"}).raise_for_status()
    return m, tmp_path


@pytest.fixture
def acts():
    from rouvr.activities import Activities
    from types import SimpleNamespace
    return Activities(SimpleNamespace())


def _run_key_in():
    from rouvr.contracts import OpsInput
    return OpsInput(RUN_KEY, INSTRUCTION, "acme", "site")


async def _full_flow(acts, inp):
    prep = await acts.ops_prepare(inp)
    await acts.ops_compile(inp, prep["task_id"])
    claimed = await acts.ops_claim(inp, prep["task_id"])
    sent = await acts.ops_deliver(inp, prep["task_id"], "primary", claimed["report"])
    v = await acts.ops_verify(inp, prep["task_id"], "primary", sent["message_id"])
    if v["verdict"] == "pass":
        return await acts.ops_close(inp, prep["task_id"], "pass", "primary",
                                    sent["message_id"], v["figures"])
    sent2 = await acts.ops_deliver(inp, prep["task_id"], "fallback", claimed["report"])
    v2 = await acts.ops_verify(inp, prep["task_id"], "fallback", sent2["message_id"])
    return await acts.ops_close(inp, prep["task_id"], v2["verdict"], "fallback",
                                sent2["message_id"], v2["figures"])


def _evidence_of(m, tid, kind):
    return [e for e in m.ledger_client(None).get(f"/v1/tasks/{tid}").json()["evidence"]
            if e["kind"] == kind]


def test_ops_happy_path_digest_and_receipt(ops_env, acts):
    m, tmp = ops_env
    r = asyncio.run(_full_flow(acts, _run_key_in()))
    assert (r.state, r.digest) == ("done", "Done. No action needed.")
    assert len(list((tmp / "outbox").iterdir())) == 1  # exactly 1 outbox message
    receipts = _evidence_of(m, r.task_id, "ops_receipt")
    assert len(receipts) == 1 and json.loads(receipts[0]["body"])["sink"] == "primary"
    judges = _evidence_of(m, r.task_id, "reviewer")  # T4: separate judge principal
    payload = json.loads(judges[0]["body"])
    assert payload["judge_principal"] == "ops-judge"
    assert payload["judge_principal"] != payload["executor"] == "ops-worker"
    assert payload["model"] == "stub" and payload["verdict"] == "pass"


def test_ops_double_deliver_sends_once(ops_env, acts):
    """Kill mid-deliver reruns the activity: file-exists check sends once."""
    m, tmp = ops_env
    inp = _run_key_in()
    prep = asyncio.run(acts.ops_prepare(inp))
    asyncio.run(acts.ops_claim(inp, prep["task_id"]))
    report = "Lane report for 2026-10-30\n" + json.dumps({"figures": {}})
    first = asyncio.run(acts.ops_deliver(inp, prep["task_id"], "primary", report))
    second = asyncio.run(acts.ops_deliver(inp, prep["task_id"], "primary", report))
    assert first["message_id"] == second["message_id"]
    assert len(list((tmp / "outbox").iterdir())) == 1


def test_ops_silent_delivery_falls_back(ops_env, acts, monkeypatch):
    m, tmp = ops_env
    monkeypatch.setenv("ROUVR_INJECT", "silent_delivery")
    r = asyncio.run(_full_flow(acts, _run_key_in()))
    assert (r.state, r.sink) == ("done", "fallback")
    primary_id = hashlib.sha256(f"{RUN_KEY}:primary".encode()).hexdigest()[:16]
    assert r.message_id != primary_id  # receipt carries the fallback id, not the lied-about one
    assert (tmp / "outbox" / f"{r.message_id}.txt").exists()


def test_ops_revoked_key_blocked_preclaim(ops_env, acts, monkeypatch):
    from temporalio.exceptions import ApplicationError
    m, _ = ops_env
    monkeypatch.setenv("ROUVR_INJECT", "revoked_key")
    inp = _run_key_in()
    prep = asyncio.run(acts.ops_prepare(inp))
    with pytest.raises(ApplicationError):  # refused before any claim
        asyncio.run(acts.ops_compile(inp, prep["task_id"]))
    r = asyncio.run(acts.ops_block(inp, prep["task_id"], "E_AUTHZ"))
    assert (r.state, r.task_id) == ("blocked", prep["task_id"])
    task = m.ledger_client(None).get(f"/v1/tasks/{prep['task_id']}").json()
    assert task["state"] == "blocked"
    assert _evidence_of(m, prep["task_id"], "escalation")


def test_ops_contract_invalid_json_refused(monkeypatch):
    from backend.rouvr_models.providers import ModelProvider
    from rouvr.errors import Code, RouvrError
    from rouvr.ops import compile_ops_contract, validate_ops_contract

    class BadProvider(ModelProvider):
        def __init__(self, raw):
            super().__init__("meta", "http://x", "m")
            self._raw = raw

        def chat(self, messages, **kw):
            return self._raw

    monkeypatch.setattr("backend.rouvr_models.providers.get_provider",
                        lambda name=None, env=None: BadProvider("not json{{"))
    with pytest.raises(RouvrError) as e:
        compile_ops_contract("do it")
    assert e.value.code is Code.CONTRACT  # refused, never merged
    with pytest.raises(RouvrError):
        validate_ops_contract({"steps": []})  # schema violation refused


def test_ops_contract_valid_merge_passes(monkeypatch):
    from rouvr.ops import compile_ops_contract
    from backend.rouvr_models.providers import ModelProvider

    class GoodProvider(ModelProvider):
        def __init__(self):
            super().__init__("meta", "http://x", "m")

        def chat(self, messages, **kw):
            return json.dumps({"steps": ["a"], "success_criteria": ["b"],
                               "side_effects": [], "stakeholders": ["owner"],
                               "escalation": "owner", "expires_in_hours": 3})

    monkeypatch.setattr("backend.rouvr_models.providers.get_provider",
                        lambda name=None, env=None: GoodProvider())
    assert compile_ops_contract("do it")["expires_in_hours"] == 3


# ---- T5: grants + schedule ----

@pytest.fixture(scope="module")
def ledger_url(tmp_path_factory):
    import threading
    import httpx
    import uvicorn
    from ledger.app import make_app
    db = tmp_path_factory.mktemp("opsledger") / "l.db"
    config = uvicorn.Config(make_app(f"sqlite:///{db}"), host="127.0.0.1", port=18222,
                            log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            if httpx.get("http://127.0.0.1:18222/health", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            pass
        import time; time.sleep(0.1)
    yield "http://127.0.0.1:18222"
    server.should_exit = True


def _grant(c, scope, expiry="2999-01-01T00:00:00+00:00", rev=""):
    return c.post("/v1/grants", json={"scope": scope, "contract_rev": rev,
                                      "expires_at": expiry, "approved_by": "owner"})


def test_grants_crud_and_expiry(ledger_url):
    import httpx
    c = httpx.Client(base_url=ledger_url, timeout=10)
    assert c.get("/v1/grants/active", params={"scope": "s:1"}).json() == {
        "active": False, "grant": None}
    _grant(c, "s:1").raise_for_status()
    assert c.get("/v1/grants/active", params={"scope": "s:1"}).json()["active"] is True
    _grant(c, "s:2", expiry="2000-01-01T00:00:00+00:00").raise_for_status()
    assert c.get("/v1/grants/active", params={"scope": "s:2"}).json()["active"] is False
    r = c.post("/v1/grants", json={"scope": "s:3"})
    assert r.status_code == 422  # missing expiry/approver refused


async def _schedule(monkeypatch, ledger_url, grant=True):
    import httpx
    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    import importlib
    import rouvr.mcp_server as ms
    importlib.reload(ms)
    if grant:
        _grant(httpx.Client(base_url=ledger_url, timeout=10),
               "schedule:task:7").raise_for_status()
    return ms


async def test_schedule_refused_without_grant(monkeypatch, ledger_url):
    import pytest as _pytest
    ms = await _schedule(monkeypatch, ledger_url, grant=False)
    with _pytest.raises(RuntimeError, match="no active grant"):
        await ms.rouvr_schedule(7, "* * * * *")


async def test_schedule_with_grant_creates(monkeypatch, ledger_url):
    ms = await _schedule(monkeypatch, ledger_url, grant=True)
    seen = {}

    async def fake_run_schedule(**kw):
        seen.update(kw)
        return kw["schedule_id"]

    monkeypatch.setattr("rouvr.starter.run_schedule", fake_run_schedule)
    out = await ms.rouvr_schedule(7, "* * * * *")
    assert out["schedule_id"] == "task-7" and seen["cron"] == "* * * * *"


# ---- Temporal end-to-end (test server, real ops activities) ----

@activity.defn(name="ops_prepare")
async def _op_prepare(inp: OpsInput) -> dict:
    from rouvr.activities import Activities
    from types import SimpleNamespace
    return await Activities(SimpleNamespace()).ops_prepare(inp)


@activity.defn(name="ops_compile")
async def _op_compile(inp: OpsInput, task_id: str) -> dict:
    from rouvr.activities import Activities
    from types import SimpleNamespace
    return await Activities(SimpleNamespace()).ops_compile(inp, task_id)


@activity.defn(name="ops_claim")
async def _op_claim(inp: OpsInput, task_id: str) -> dict:
    from rouvr.activities import Activities
    from types import SimpleNamespace
    return await Activities(SimpleNamespace()).ops_claim(inp, task_id)


@activity.defn(name="ops_deliver")
async def _op_deliver(inp: OpsInput, task_id: str, sink: str, report: str) -> dict:
    from rouvr.activities import Activities
    from types import SimpleNamespace
    return await Activities(SimpleNamespace()).ops_deliver(inp, task_id, sink, report)


@activity.defn(name="ops_verify")
async def _op_verify(inp: OpsInput, task_id: str, sink: str, msg_id: str) -> dict:
    from rouvr.activities import Activities
    from types import SimpleNamespace
    return await Activities(SimpleNamespace()).ops_verify(inp, task_id, sink, msg_id)


@activity.defn(name="ops_close")
async def _op_close(inp: OpsInput, task_id: str, verdict: str, sink: str, msg_id: str,
                    figures: dict):
    from rouvr.activities import Activities
    from types import SimpleNamespace
    return await Activities(SimpleNamespace()).ops_close(inp, task_id, verdict, sink, msg_id, figures)


@activity.defn(name="ops_block")
async def _op_block(inp: OpsInput, task_id: str, reason: str):
    from rouvr.activities import Activities
    from types import SimpleNamespace
    return await Activities(SimpleNamespace()).ops_block(inp, task_id, reason)


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as e:
        yield e


async def test_ops_workflow_done_digest(env, ops_env):
    from rouvr.workflows import Q_ORCH, OpsTaskWorkflow
    worker = Worker(env.client, task_queue=Q_ORCH, workflows=[OpsTaskWorkflow],
                    activities=[_op_prepare, _op_compile, _op_claim, _op_deliver,
                                _op_verify, _op_close, _op_block])
    async with worker:
        r = await env.client.execute_workflow(
            OpsTaskWorkflow.run, OpsInput(RUN_KEY, INSTRUCTION, "acme", "site"),
            id=str(uuid.uuid4()), task_queue=Q_ORCH)
    assert r.state == "done" and r.digest == "Done. No action needed."


async def _ops_schedule_ms(monkeypatch, ledger_url, grant=True, expired=False):
    import httpx
    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    import importlib
    import rouvr.mcp_server as ms
    importlib.reload(ms)
    if grant or expired:
        expiry = "2000-01-01T00:00:00+00:00" if expired else "2999-01-01T00:00:00+00:00"
        _grant(httpx.Client(base_url=ledger_url, timeout=10),
               "ops:morning-report", expiry=expiry).raise_for_status()
    return ms


async def test_ops_schedule_refused_without_grant(monkeypatch, ledger_url):
    import pytest as _pytest
    ms = await _ops_schedule_ms(monkeypatch, ledger_url, grant=False)
    with _pytest.raises(RuntimeError, match="no active grant"):
        await ms.rouvr_schedule_ops("morning-report:2026-10-30", INSTRUCTION,
                                    "acme", "site", "* * * * *")


async def test_ops_schedule_refused_when_grant_expired(monkeypatch, ledger_url):
    import pytest as _pytest
    ms = await _ops_schedule_ms(monkeypatch, ledger_url, expired=True)
    with _pytest.raises(RuntimeError, match="no active grant"):
        await ms.rouvr_schedule_ops("morning-report:2026-10-30", INSTRUCTION,
                                    "acme", "site", "* * * * *")


async def test_ops_schedule_with_grant_creates(monkeypatch, ledger_url):
    ms = await _ops_schedule_ms(monkeypatch, ledger_url, grant=True)
    seen = {}

    async def fake_run_schedule_ops(**kw):
        seen.update(kw)
        return kw["schedule_id"]

    monkeypatch.setattr("rouvr.starter.run_schedule_ops", fake_run_schedule_ops)
    out = await ms.rouvr_schedule_ops("morning-report:2026-10-30", INSTRUCTION,
                                      "acme", "site", "* * * * *")
    assert out["schedule_id"] == "ops-morning-report-2026-10-30"
    assert out["scope"] == "ops:morning-report" and seen["cron"] == "* * * * *"


async def test_ops_prepare_blocks_without_active_grant(ops_env):
    """Per-tick grant gate: an expired/absent grant parks the tick with an
    escalation record instead of running the pack."""
    import httpx
    from rouvr.activities import Activities
    from rouvr.contracts import OpsInput
    from types import SimpleNamespace

    acts = Activities(SimpleNamespace(
        workhub_for=lambda persona: (_ for _ in ()).throw(AssertionError("no ledger reads expected")),
        settings=SimpleNamespace(workspace_root=None)))
    inp = OpsInput(RUN_KEY, INSTRUCTION, "acme", "site", grant_scope="ops:morning-report")
    out = await acts.ops_prepare(inp)
    assert out["state"] == "blocked"
    assert "ops:morning-report" in out.get("reason", "")


async def test_ops_prepare_proceeds_with_active_grant(ops_env, ledger_url, monkeypatch):
    import httpx
    from rouvr.activities import Activities
    from rouvr.contracts import OpsInput
    from types import SimpleNamespace

    monkeypatch.setenv("ROUVR_LEDGER_URL", ledger_url)
    c = httpx.Client(base_url=ledger_url, timeout=10)
    c.post("/v1/tenants", json={"tenant": "acme"})
    c.post("/v1/projects", json={"tenant": "acme", "project": "site"})
    c.post("/v1/grants", json={"scope": "ops:morning-report", "contract_rev": "",
                               "expires_at": "2999-01-01T00:00:00+00:00",
                               "approved_by": "owner"}).raise_for_status()
    acts = Activities(SimpleNamespace(
        workhub_for=lambda persona: (_ for _ in ()).throw(AssertionError("no ledger reads expected")),
        settings=SimpleNamespace(workspace_root=None)))
    out = await acts.ops_prepare(
        OpsInput(RUN_KEY, INSTRUCTION, "acme", "site", grant_scope="ops:morning-report"))
    assert out["state"] in ("open", "done", "claimed")


async def test_ops_workflow_blocked_tick_leaves_escalation_record(ops_env, ledger_url, monkeypatch):
    """Full tick through live Temporal: expired grant -> blocked + escalation evidence."""
    import httpx
    import uuid
    from temporalio.worker import Worker
    from rouvr.activities import Activities
    from rouvr.contracts import OpsInput
    from rouvr.workflows import Q_ORCH, OpsTaskWorkflow
    from types import SimpleNamespace

    monkeypatch.setenv("ROUVR_LEDGER_URL", ledger_url)
    c = httpx.Client(base_url=ledger_url, timeout=10)
    c.post("/v1/tenants", json={"tenant": "acme"})
    c.post("/v1/projects", json={"tenant": "acme", "project": "site"})
    c.post("/v1/grants", json={"scope": "ops:morning-report", "contract_rev": "",
                               "expires_at": "2000-01-01T00:00:00+00:00",
                               "approved_by": "owner"}).raise_for_status()
    acts = Activities(SimpleNamespace(
        workhub_for=lambda persona: (_ for _ in ()).throw(AssertionError("no ledger reads expected")),
        settings=SimpleNamespace(workspace_root=None)))

    async def _env_client():
        async with await WorkflowEnvironment.start_time_skipping() as e:
            yield e

    async for e in _env_client():
        worker = Worker(e.client, task_queue=Q_ORCH, workflows=[OpsTaskWorkflow],
                        activities=[acts.ops_prepare, acts.ops_block])
        async with worker:
            r = await e.client.execute_workflow(
                OpsTaskWorkflow.run,
                OpsInput(RUN_KEY, INSTRUCTION, "acme", "site",
                         grant_scope="ops:morning-report"),
                id=str(uuid.uuid4()), task_queue=Q_ORCH)
        c2 = httpx.Client(base_url=ledger_url, timeout=10)
        tasks = c2.get("/v1/tasks").json()["items"]
    assert r.state == "blocked"
    tid = str(r.task_id)
    assert any(str(t["id"]) == tid and t["state"] == "blocked" for t in tasks)
    with httpx.Client(base_url=ledger_url, timeout=10) as c3:
        detail = c3.get(f"/v1/tasks/{tid}").json()
    assert any(x.get("kind") == "escalation" for x in detail.get("evidence", []))


async def test_create_ops_schedule_registers_ops_action(env):
    """The test Temporal server has no CreateSchedule RPC, so the schedule
    payload is asserted on a recording client: it must fire OpsTaskWorkflow
    (never a coding child) with the pack input, SKIP overlap, and the cron."""
    from rouvr.contracts import OpsInput
    from rouvr.starter import create_ops_schedule
    from rouvr.workflows import OpsTaskWorkflow
    from temporalio.client import (ScheduleActionStartWorkflow, ScheduleOverlapPolicy)

    seen = {}

    class RecordingClient:
        async def create_schedule(self, schedule_id, schedule, **_kw):
            seen["id"] = schedule_id
            seen["schedule"] = schedule

    inp = OpsInput(RUN_KEY, INSTRUCTION, "acme", "site")
    await create_ops_schedule(RecordingClient(), inp, "ops-morning-report-test",
                              "7 6 * * *", "Asia/Kolkata")
    assert seen["id"] == "ops-morning-report-test"
    sched = seen["schedule"]
    assert isinstance(sched.action, ScheduleActionStartWorkflow)
    assert sched.action.workflow == "OpsTaskWorkflow"
    assert list(sched.action.args) == [inp]
    assert sched.action.id == "rouvr-ops-ops-morning-report-test"
    assert sched.spec.cron_expressions == ["7 6 * * *"]
    assert sched.spec.time_zone_name == "Asia/Kolkata"
    assert sched.policy.overlap == ScheduleOverlapPolicy.SKIP


async def test_scheduled_duplicate_tick_deduped(env):
    """T5: a duplicate tick creates no second child (REJECT_DUPLICATE + swallow)."""
    from rouvr.contracts import (AssignInput, AssignResult, Binding, CloseResult, TaskWorkflowInput,
                                 TestResult, VerifyResult)
    from rouvr.workflows import Q_BUILD, Q_ORCH, Q_TEST, Q_VERIFY, ScheduledRunWorkflow, TaskWorkflow

    @activity.defn(name="verify_binding")
    async def vb(wf: TaskWorkflowInput) -> Binding:
        return Binding(wf.task_id, "b", "v", wf.contract_rev, 1)

    @activity.defn(name="assign_task")
    async def at(i: AssignInput) -> AssignResult:
        return AssignResult("att1", 1, "s1", "rouvr/x/a1", "https://pr", "sha")

    @activity.defn(name="run_tests")
    async def rt(wf: TaskWorkflowInput, b: AssignResult) -> TestResult:
        return TestResult("tr", True, "ok", "ev")

    @activity.defn(name="verify")
    async def vf(wf, b, built, t) -> VerifyResult:
        return VerifyResult("approve", "ok", "svc-v", "ev")

    @activity.defn(name="close_task")
    async def ct(wf, b, attempt_id) -> CloseResult:
        return CloseResult("done", 2)

    @activity.defn(name="mark_blocked")
    async def mb(wf, reason) -> CloseResult:
        return CloseResult("blocked", 2)

    @activity.defn(name="escalate")
    async def es(wf, reason, attempt_id) -> bool:
        return True

    @activity.defn(name="sign_receipt")
    async def sr(wf, built, tests, v) -> dict:
        return {"digest": "d" * 64, "signature": "c2ln", "key_id": "rouvr-receipt-1"}

    workers = [Worker(env.client, task_queue=Q_ORCH, workflows=[TaskWorkflow, ScheduledRunWorkflow],
                      activities=[vb, ct, mb, es, sr]),
               Worker(env.client, task_queue=Q_BUILD, activities=[at]),
               Worker(env.client, task_queue=Q_TEST, activities=[rt]),
               Worker(env.client, task_queue=Q_VERIFY, activities=[vf])]
    import asyncio
    for w in workers:
        await w.__aenter__()
    try:
        wf = TaskWorkflowInput("t", "p", "task9", "rev1", "dev")
        # Concurrent starts share one virtual minute, so both compute the same
        # tick: the loser hits REJECT_DUPLICATE and reports deduped.
        first, second = await asyncio.gather(
            env.client.execute_workflow(
                ScheduledRunWorkflow.run, wf, id=f"sched-{uuid.uuid4()}", task_queue=Q_ORCH),
            env.client.execute_workflow(
                ScheduledRunWorkflow.run, wf, id=f"sched-{uuid.uuid4()}", task_queue=Q_ORCH))
    finally:
        for w in workers:
            await w.__aexit__(None, None, None)
    by_child = sorted([first, second], key=lambda r: (r.get("deduped") is True, r["child"]))
    assert by_child[0]["result"]["state"] == "done" and not by_child[0].get("deduped")
    assert by_child[1]["child"] == by_child[0]["child"] and by_child[1].get("deduped") is True


FIGURES = {"done": 5, "blocked": 0, "failed": 0}
GOOD_REPORT = 'Lane report for x\n{"blocked": 0, "done": 5, "failed": 0}'
BAD_REPORT = 'Lane report for x\n{"blocked": 0, "done": 4, "failed": 1}'


def _chat(returning):
    def chat(messages):
        assert messages[0]["role"] == "system"
        return returning
    return chat


def test_judge_passes_matching_report():
    from rouvr.ops import judge_ops_report
    r = judge_ops_report(GOOD_REPORT, FIGURES,
                         chat=_chat('{"verdict": "pass", "reason": "all figures stated"}'))
    assert r["verdict"] == "pass" and r["judge_principal"] == "ops-judge"


def test_judge_rejects_wrong_report():
    from rouvr.ops import judge_ops_report
    r = judge_ops_report(BAD_REPORT, FIGURES,
                         chat=_chat('{"verdict": "fail", "reason": "done mismatch"}'))
    assert r["verdict"] == "fail"


def test_judge_refuses_unparsable_output():
    from rouvr.errors import Code, RouvrError
    from rouvr.ops import judge_ops_report
    for bad in ("not json", "[1, 2]", '{"verdict": "maybe"}', '{"reason": "no verdict"}'):
        try:
            judge_ops_report(GOOD_REPORT, FIGURES, chat=_chat(bad))
        except RouvrError as e:
            assert e.code == Code.CONTRACT
        else:
            raise AssertionError(f"judge accepted {bad!r}")


def test_verify_with_judge_skips_on_stub():
    from rouvr.ops import verify_with_judge
    assert verify_with_judge(deterministic_pass=True, report=GOOD_REPORT,
                             figures=FIGURES, provider_name="stub")["judge"]["verdict"] == "skipped"
    assert verify_with_judge(deterministic_pass=False, report=BAD_REPORT,
                             figures=FIGURES, provider_name="stub")["verdict"] == "fail"


def test_verify_with_judge_live_fail_overrides_deterministic_pass():
    from rouvr.ops import verify_with_judge
    r = verify_with_judge(deterministic_pass=True, report=BAD_REPORT, figures=FIGURES,
                          provider_name="meta",
                          chat=_chat('{"verdict": "fail", "reason": "done mismatch"}'))
    assert r["verdict"] == "fail" and r["judge"]["verdict"] == "fail"


def test_verify_with_judge_live_pass_passes():
    from rouvr.ops import verify_with_judge
    r = verify_with_judge(deterministic_pass=True, report=GOOD_REPORT, figures=FIGURES,
                          provider_name="meta",
                          chat=_chat('{"verdict": "pass", "reason": "all figures stated"}'))
    assert r["verdict"] == "pass"
