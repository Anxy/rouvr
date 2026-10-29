"""Integration test: real TaskWorkflow on Temporal's time-skipping test server with fake activities."""
import uuid

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from rouvr.contracts import (AssignInput, AssignResult, Binding, CloseResult, TaskWorkflowInput, TestResult,
                             VerifyResult)
from rouvr.workflows import Q_BUILD, Q_ORCH, Q_TEST, Q_VERIFY, TaskWorkflow

calls: list[str] = []
verdicts: list[str] = []


@activity.defn(name="verify_binding")
async def verify_binding(wf: TaskWorkflowInput) -> Binding:
    return Binding(wf.task_id, "b", "v", wf.contract_rev, 1)


@activity.defn(name="assign_task")
async def assign_task(i: AssignInput) -> AssignResult:
    calls.append(f"assign:{i.attempt_no}:{i.resume_session_id}")
    return AssignResult(f"att{i.attempt_no}", 1, f"sess{i.attempt_no}", f"rouvr/x/a{i.attempt_no}", "https://pr", "sha")


@activity.defn(name="run_tests")
async def run_tests(wf: TaskWorkflowInput, b: AssignResult) -> TestResult:
    return TestResult("tr", True, "ok", "ev")


@activity.defn(name="verify")
async def verify(wf, b, built, t) -> VerifyResult:
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


async def _run(env: WorkflowEnvironment, max_rework: int = 2) -> dict:
    workers = [Worker(env.client, task_queue=Q_ORCH, workflows=[TaskWorkflow],
                      activities=[verify_binding, close_task, mark_blocked, escalate, sign_receipt]),
               Worker(env.client, task_queue=Q_BUILD, activities=[assign_task]),
               Worker(env.client, task_queue=Q_TEST, activities=[run_tests]),
               Worker(env.client, task_queue=Q_VERIFY, activities=[verify])]
    for w in workers:
        await w.__aenter__()
    try:
        return await env.client.execute_workflow(
            TaskWorkflow.run, TaskWorkflowInput("t", "p", "task1", "rev1", "dev", max_rework),
            id=str(uuid.uuid4()), task_queue=Q_ORCH)
    finally:
        for w in workers:
            await w.__aexit__(None, None, None)


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as e:
        yield e


async def test_rework_then_approve(env):
    calls.clear(); verdicts[:] = ["request_changes", "approve"]
    r = await _run(env)
    assert r["state"] == "done" and r["attempts"] == 2
    assert calls == ["assign:1:None", "assign:2:sess1", "close"]


async def test_rework_exhausted_blocks(env):
    calls.clear(); verdicts[:] = ["request_changes"] * 3
    r = await _run(env, max_rework=1)
    assert r == {"state": "blocked", "reason": "rework_exhausted", "attempts": 2}
