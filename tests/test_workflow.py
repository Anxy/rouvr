"""Integration test: real TaskWorkflow on Temporal's time-skipping test server with fake activities."""
import asyncio
import uuid

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from rouvr.contracts import (AssignInput, AssignResult, Binding, CloseResult, DeliveryCursor,
                             TaskWorkflowInput, TestResult, VerifyResult)
from rouvr.workflows import Q_BUILD, Q_ORCH, Q_TEST, Q_VERIFY, TaskWorkflow, run_tests_timeout_s, verify_timeout_s

calls: list[str] = []
verdicts: list[str] = []
seen: list[str] = []
gates: dict[str, asyncio.Event | None] = {"assign": None, "test": None, "verify": None}
# In-memory cursor store keyed by task_id for fake load/save/clear activities.
_cursors: dict[str, DeliveryCursor] = {}
_test_results: list[TestResult] = []
_test_errors: list[Exception | None] = []


async def _gate(stage: str) -> None:
    seen.append(stage)
    if gates[stage] is not None:
        await gates[stage].wait()


@activity.defn(name="verify_binding")
async def verify_binding(wf: TaskWorkflowInput) -> Binding:
    return Binding(wf.task_id, "b", "v", wf.contract_rev, 1)


@activity.defn(name="assign_task")
async def assign_task(i: AssignInput) -> AssignResult:
    calls.append(f"assign:{i.attempt_no}:{i.resume_session_id}")
    await _gate("assign")
    return AssignResult(f"att{i.attempt_no}", 1, f"sess{i.attempt_no}", f"rouvr/x/a{i.attempt_no}", "https://pr", "sha")


@activity.defn(name="run_tests")
async def run_tests(wf: TaskWorkflowInput, b: AssignResult) -> TestResult:
    await _gate("test")
    calls.append(f"test:{b.attempt_id}")
    if _test_errors:
        err = _test_errors.pop(0)
        if err is not None:
            raise err
    if _test_results:
        return _test_results.pop(0)
    return TestResult("tr", True, "ok", "ev")


@activity.defn(name="verify")
async def verify(wf, b, built, t) -> VerifyResult:
    await _gate("verify")
    return VerifyResult(verdicts.pop(0), "fix x", "svc-v", "ev")


@activity.defn(name="close_task")
async def close_task(wf, b, attempt_id) -> CloseResult:
    calls.append("close")
    return CloseResult("done", 2)


@activity.defn(name="mark_blocked")
async def mark_blocked(wf, reason) -> CloseResult:
    calls.append(f"blocked:{reason}")
    return CloseResult("blocked", 2)


@activity.defn(name="escalate")
async def escalate(wf, reason, attempt_id) -> bool:
    return True


@activity.defn(name="sign_receipt")
async def sign_receipt(wf, built, tests, v) -> dict:
    return {"digest": "d" * 64, "signature": "c2ln", "key_id": "rouvr-receipt-1"}


@activity.defn(name="load_delivery_cursor")
async def load_delivery_cursor(wf: TaskWorkflowInput) -> DeliveryCursor:
    return _cursors.get(wf.task_id, DeliveryCursor())


@activity.defn(name="save_delivery_cursor")
async def save_delivery_cursor(wf: TaskWorkflowInput, cursor: DeliveryCursor) -> str:
    _cursors[wf.task_id] = cursor
    calls.append(f"cursor:{cursor.phase or 'cleared'}")
    return "ev-cursor"


@activity.defn(name="clear_delivery_cursor")
async def clear_delivery_cursor(wf: TaskWorkflowInput) -> str:
    _cursors[wf.task_id] = DeliveryCursor()
    calls.append("cursor:cleared")
    return "ev-cleared"


_ORCH_ACTS = [verify_binding, close_task, mark_blocked, escalate, sign_receipt,
              load_delivery_cursor, save_delivery_cursor, clear_delivery_cursor]


def _workers(env: WorkflowEnvironment) -> list[Worker]:
    return [Worker(env.client, task_queue=Q_ORCH, workflows=[TaskWorkflow],
                   activities=_ORCH_ACTS),
            Worker(env.client, task_queue=Q_BUILD, activities=[assign_task]),
            Worker(env.client, task_queue=Q_TEST, activities=[run_tests]),
            Worker(env.client, task_queue=Q_VERIFY, activities=[verify])]


async def _run(env: WorkflowEnvironment, max_rework: int = 2,
               *, task_id: str = "task1", restart: bool = False) -> dict:
    workers = _workers(env)
    for w in workers:
        await w.__aenter__()
    try:
        return await env.client.execute_workflow(
            TaskWorkflow.run,
            TaskWorkflowInput("t", "p", task_id, "rev1", "dev", max_rework,
                              restart_from_build=restart),
            id=str(uuid.uuid4()), task_queue=Q_ORCH)
    finally:
        for w in workers:
            await w.__aexit__(None, None, None)


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as e:
        yield e


@pytest.fixture(autouse=True)
def _reset_fakes():
    calls.clear()
    seen.clear()
    verdicts.clear()
    _cursors.clear()
    _test_results.clear()
    _test_errors.clear()
    for k in gates:
        gates[k] = None


async def test_rework_then_approve(env):
    verdicts[:] = ["request_changes", "approve"]
    r = await _run(env)
    assert r["state"] == "done" and r["attempts"] == 2
    assert "assign:1:None" in calls and "assign:2:sess1" in calls and "close" in calls
    assert "cursor:cleared" in calls  # product rework clears cursor


async def test_rework_exhausted_blocks(env):
    verdicts[:] = ["request_changes"] * 3
    r = await _run(env, max_rework=1)
    assert r == {"state": "blocked", "reason": "rework_exhausted", "attempts": 2}


async def test_escalate_failure_still_blocks(env):
    """T1: a failing escalate must not turn the block path into a failed workflow."""

    @activity.defn(name="escalate")
    async def boom_escalate(wf, reason, attempt_id) -> bool:
        raise ApplicationError("escalation down", type="E_DOWNSTREAM")

    verdicts[:] = ["request_changes"] * 3
    orch = [verify_binding, close_task, mark_blocked, boom_escalate, sign_receipt,
            load_delivery_cursor, save_delivery_cursor, clear_delivery_cursor]
    workers = [Worker(env.client, task_queue=Q_ORCH, workflows=[TaskWorkflow], activities=orch),
               Worker(env.client, task_queue=Q_BUILD, activities=[assign_task]),
               Worker(env.client, task_queue=Q_TEST, activities=[run_tests]),
               Worker(env.client, task_queue=Q_VERIFY, activities=[verify])]
    for w in workers:
        await w.__aenter__()
    try:
        r = await env.client.execute_workflow(
            TaskWorkflow.run, TaskWorkflowInput("t", "p", "task1", "rev1", "dev", 1),
            id=str(uuid.uuid4()), task_queue=Q_ORCH)
    finally:
        for w in workers:
            await w.__aexit__(None, None, None)
    assert r == {"state": "blocked", "reason": "rework_exhausted", "attempts": 2}


def test_activity_timeout_covers_two_spec_runs():
    assert run_tests_timeout_s(1500) == 3300
    assert run_tests_timeout_s(10) == 320
    assert verify_timeout_s(1500) == 1800
    assert verify_timeout_s(10) == 310
    wf = TaskWorkflowInput("t", "p", "task1", "rev1", "dev", assign_start_to_close_s=99, spec_timeout_s=10)
    assert (wf.assign_start_to_close_s, wf.spec_timeout_s) == (99, 10)


@pytest.mark.parametrize("stage", ["assign", "test", "verify"])
async def test_cancel_during_stage_blocks_without_close(env, stage):
    verdicts[:] = ["approve"]
    gates[stage] = asyncio.Event()
    workers = _workers(env)
    for w in workers:
        await w.__aenter__()
    try:
        handle = await env.client.start_workflow(
            TaskWorkflow.run, TaskWorkflowInput("t", "p", "task1", "rev1", "dev"),
            id=str(uuid.uuid4()), task_queue=Q_ORCH)
        for _ in range(1000):
            if stage in seen:
                break
            await asyncio.sleep(0.01)
        assert stage in seen
        await handle.signal(TaskWorkflow.request_cancel)
        gates[stage].set()
        r = await handle.result()
    finally:
        gates[stage] = None
        for w in workers:
            await w.__aexit__(None, None, None)
    assert r == {"state": "blocked", "reason": "E_CANCELLED", "attempts": 1}
    assert "close" not in calls and "blocked:E_CANCELLED" in calls


async def test_resume_at_test_skips_build(env):
    """Infra block left build_done cursor → Retry re-enters at test, no new assign."""
    tid = "resume-test"
    _cursors[tid] = DeliveryCursor(
        phase="build_done", attempt_no=1, attempt_id="att1", lease_epoch=1,
        muse_session_id="sess1", branch="rouvr/x/a1", pr_url="https://pr", head_sha="sha")
    verdicts[:] = ["approve"]
    r = await _run(env, task_id=tid)
    assert r["state"] == "done"
    assert not any(c.startswith("assign:") for c in calls)
    assert any(c.startswith("test:att1") for c in calls)
    assert "close" in calls


async def test_resume_at_close_skips_build_test_verify(env):
    tid = "resume-close"
    _cursors[tid] = DeliveryCursor(
        phase="verify_approved", attempt_no=1, attempt_id="att1", lease_epoch=1,
        muse_session_id="sess1", branch="rouvr/x/a1", pr_url="https://pr", head_sha="sha",
        test_run_id="tr", test_summary="ok", test_evidence_id="ev",
        verify_verdict="approve", verify_notes="lgtm", verify_reviewer="svc-v",
        verify_evidence_id="vev")
    r = await _run(env, task_id=tid)
    assert r["state"] == "done"
    assert not any(c.startswith("assign:") for c in calls)
    assert not any(c.startswith("test:") for c in calls)
    assert "close" in calls


async def test_restart_clears_cursor_and_rebuilds(env):
    tid = "restart-build"
    _cursors[tid] = DeliveryCursor(
        phase="build_done", attempt_no=1, attempt_id="att1", lease_epoch=1,
        muse_session_id="sess1", branch="rouvr/x/a1", pr_url="https://pr", head_sha="sha")
    verdicts[:] = ["approve"]
    r = await _run(env, task_id=tid, restart=True)
    assert r["state"] == "done"
    assert "cursor:cleared" in calls
    assert any(c.startswith("assign:1:") for c in calls)


async def test_infra_test_failure_leaves_build_cursor(env):
    """E_EVIDENCE during tests blocks without clearing the build_done cursor."""
    tid = "infra-test"
    _test_errors.append(ApplicationError("mount broken", type="E_EVIDENCE", non_retryable=True))
    r = await _run(env, task_id=tid)
    assert r["state"] == "blocked" and r["reason"] == "E_EVIDENCE"
    assert _cursors[tid].phase == "build_done"
    assert any(c.startswith("assign:1:") for c in calls)
