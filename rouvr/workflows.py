"""rouvr/workflows.py — TaskWorkflow (build→test→verify→close with bounded rework) + ScheduledRunWorkflow.

R4 port of hyeve-lane TaskLoop @ d2e39d8: workflow id = task id, four fixed role
queues. Lane-ops dispatch-queue ownership (lanes/temporal/queues.py #351/#359)
is intentionally not ported; phase 2 reintroduces wake/queueing.

Cross-run resume: a durable delivery_cursor lets a new workflow enter at
test/verify/close after infra blocks, without rebuilding. Product rework still
clears the cursor and re-enters at build.
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, ApplicationError, WorkflowAlreadyStartedError

with workflow.unsafe.imports_passed_through():
    from rouvr.activities import Activities
    from rouvr.contracts import AssignInput, AssignResult, OpsInput, OpsResult, TaskWorkflowInput, TestResult
    from rouvr.errors import retry_policy_for
    from rouvr.ids import scheduled_child_id, workflow_id
    from rouvr.ops import OPS_DIGEST_DONE
    from rouvr.phase_cursor import (assign_from_cursor, cursor_after_build, cursor_after_test,
                                    cursor_after_verify, hydrate_tests, resume_at,
                                    verify_from_cursor)

Q_ORCH, Q_BUILD, Q_TEST, Q_VERIFY = "rouvr-orchestrator", "rouvr-coder", "rouvr-test", "rouvr-reviewer"


def run_tests_timeout_s(spec_timeout_s: int) -> int:
    return 2 * spec_timeout_s + 300  # two container runs at spec.timeout_s each, plus clone/diff/evidence overhead


def verify_timeout_s(spec_timeout_s: int) -> int:
    return spec_timeout_s + 300  # reviewer turn runs spec.timeout_s, plus overhead


def _code(err: BaseException) -> str:
    c = err.cause if isinstance(err, ActivityError) else err
    return c.type if isinstance(c, ApplicationError) and c.type else "E_DOWNSTREAM"


@workflow.defn(name="TaskWorkflow")
class TaskWorkflow:
    def __init__(self) -> None:
        self.phase = "init"
        self.attempt = 0
        self.cancel_requested = False

    @workflow.query
    def status(self) -> dict:
        return {"phase": self.phase, "attempt": self.attempt}

    @workflow.signal
    def request_cancel(self) -> None:
        self.cancel_requested = True

    @workflow.run
    async def run(self, wf: TaskWorkflowInput) -> dict:
        wh = retry_policy_for("workhub")
        try:
            self.phase = "binding"
            b = await workflow.execute_activity_method(Activities.verify_binding, wf, task_queue=Q_ORCH,
                                                       start_to_close_timeout=timedelta(seconds=60), retry_policy=wh)
            if wf.restart_from_build:
                await workflow.execute_activity_method(
                    Activities.clear_delivery_cursor, wf, task_queue=Q_ORCH,
                    start_to_close_timeout=timedelta(seconds=60), retry_policy=wh)
                cursor = None
            else:
                cursor = await workflow.execute_activity_method(
                    Activities.load_delivery_cursor, wf, task_queue=Q_ORCH,
                    start_to_close_timeout=timedelta(seconds=60), retry_policy=wh)

            entry = resume_at(cursor)
            notes, resume = "", None
            built: AssignResult | None = None
            tests: TestResult | None = None

            if entry == "close" and cursor is not None:
                built = assign_from_cursor(cursor)
                tests = hydrate_tests(cursor)
                v = verify_from_cursor(cursor)
                if built and tests and v:
                    self.attempt = cursor.attempt_no or 1
                    return await self._finish(wf, b, built, tests, v, wh)
                # Corrupt close cursor: fall back to a full build.
                built, tests = None, None
                self.attempt = 0

            if entry in ("test", "verify") and cursor is not None:
                built = assign_from_cursor(cursor)
                self.attempt = cursor.attempt_no or 1
                if entry == "verify":
                    tests = hydrate_tests(cursor)
                if built is None or (entry == "verify" and tests is None):
                    # Corrupt cursor: fall back to a full build.
                    built, tests = None, None
                    self.attempt = 0

            while True:
                if self.cancel_requested:
                    return await self._block(wf, "E_CANCELLED",
                                             built.attempt_id if built else "")

                if built is None:
                    # Same budget as before: attempts 1..(max_rework+1).
                    if not (self.attempt <= wf.max_rework):
                        return await self._block(wf, "rework_exhausted",
                                                 f"attempt-{self.attempt}")
                    self.attempt += 1
                    self.phase = "build"
                    built = await workflow.execute_activity_method(
                        Activities.assign_task,
                        AssignInput(wf=wf, binding=b, attempt_no=self.attempt, rework_notes=notes,
                                    resume_session_id=resume),
                        task_queue=Q_BUILD, start_to_close_timeout=timedelta(seconds=wf.assign_start_to_close_s),
                        heartbeat_timeout=timedelta(seconds=90),
                        retry_policy=RetryPolicy(initial_interval=timedelta(seconds=10), backoff_coefficient=2.0,
                                                 maximum_attempts=3, non_retryable_error_types=wh.non_retryable_error_types))
                    if self.cancel_requested:
                        return await self._block(wf, "E_CANCELLED", built.attempt_id)
                    await workflow.execute_activity_method(
                        Activities.save_delivery_cursor,
                        args=[wf, cursor_after_build(self.attempt, built)],
                        task_queue=Q_ORCH, start_to_close_timeout=timedelta(seconds=60), retry_policy=wh)
                    tests = None

                if tests is None:
                    self.phase = "test"
                    tests = await workflow.execute_activity_method(
                        Activities.run_tests, args=[wf, built], task_queue=Q_TEST,
                        start_to_close_timeout=timedelta(seconds=run_tests_timeout_s(wf.spec_timeout_s)),
                        retry_policy=retry_policy_for("test"))
                    if self.cancel_requested:
                        return await self._block(wf, "E_CANCELLED", built.attempt_id)
                    if not tests.passed:
                        # Product failure: clear cursor so Retry rebuilds; burn rework.
                        notes = f"Tests failed:\n{tests.summary}\n{tests.failed_output[:4000]}"
                        resume = built.muse_session_id
                        await workflow.execute_activity_method(
                            Activities.clear_delivery_cursor, wf, task_queue=Q_ORCH,
                            start_to_close_timeout=timedelta(seconds=60), retry_policy=wh)
                        built, tests = None, None
                        continue

                await workflow.execute_activity_method(
                    Activities.save_delivery_cursor,
                    args=[wf, cursor_after_test(self.attempt, built, tests)],
                    task_queue=Q_ORCH, start_to_close_timeout=timedelta(seconds=60), retry_policy=wh)

                self.phase = "verify"
                v = await workflow.execute_activity_method(
                    Activities.verify, args=[wf, b, built, tests],
                    task_queue=Q_VERIFY,
                    start_to_close_timeout=timedelta(seconds=verify_timeout_s(wf.spec_timeout_s)),
                    heartbeat_timeout=None,
                    retry_policy=retry_policy_for("verify"))
                if v.verdict == "approve":
                    if self.cancel_requested:
                        return await self._block(wf, "E_CANCELLED", built.attempt_id)
                    await workflow.execute_activity_method(
                        Activities.save_delivery_cursor,
                        args=[wf, cursor_after_verify(self.attempt, built, tests, v)],
                        task_queue=Q_ORCH, start_to_close_timeout=timedelta(seconds=60), retry_policy=wh)
                    return await self._finish(wf, b, built, tests, v, wh)
                if v.verdict == "reject":
                    return await self._block(wf, "rejected_by_reviewer", built.attempt_id)
                # request_changes: product rework — clear cursor, rebuild.
                notes, resume = v.notes, built.muse_session_id
                await workflow.execute_activity_method(
                    Activities.clear_delivery_cursor, wf, task_queue=Q_ORCH,
                    start_to_close_timeout=timedelta(seconds=60), retry_policy=wh)
                built, tests = None, None
        except ActivityError as e:
            return await self._block(wf, _code(e), f"attempt-{self.attempt}")

    async def _finish(self, wf, b, built, tests, v, wh) -> dict:
        self.phase = "close"
        closed = await workflow.execute_activity_method(
            Activities.close_task, args=[wf, b, built.attempt_id], task_queue=Q_ORCH,
            start_to_close_timeout=timedelta(seconds=120), retry_policy=wh)
        receipt = await workflow.execute_activity_method(
            Activities.sign_receipt, args=[wf, built, tests, v], task_queue=Q_ORCH,
            start_to_close_timeout=timedelta(seconds=60), retry_policy=wh)
        # Mark closed so a later Retry does not re-close; Restart clears explicitly.
        closed_cursor = cursor_after_verify(self.attempt, built, tests, v)
        closed_cursor.phase = "closed"
        await workflow.execute_activity_method(
            Activities.save_delivery_cursor, args=[wf, closed_cursor], task_queue=Q_ORCH,
            start_to_close_timeout=timedelta(seconds=60), retry_policy=wh)
        self.phase = "done"
        return {"state": closed.final_state, "pr_url": built.pr_url, "receipt": receipt,
                "attempts": self.attempt}

    async def _block(self, wf: TaskWorkflowInput, reason: str, attempt_id: str) -> dict:
        self.phase = "blocked"
        wh = retry_policy_for("workhub")
        await workflow.execute_activity_method(Activities.mark_blocked, args=[wf, reason], task_queue=Q_ORCH,
                                               start_to_close_timeout=timedelta(seconds=120), retry_policy=wh)
        # Single-attempt escalate: the escalation receipt must not retry the
        # block path into a failed workflow. Swallow-after-evidence: a failed
        # escalate still returns a clean blocked result (mark_blocked above
        # already parked the task).
        escalate_once = RetryPolicy(initial_interval=timedelta(seconds=2),
                                    maximum_interval=timedelta(seconds=10),
                                    maximum_attempts=1)
        try:
            await workflow.execute_activity_method(Activities.escalate, args=[wf, reason, attempt_id],
                                                   task_queue=Q_ORCH,
                                                   start_to_close_timeout=timedelta(seconds=60),
                                                   retry_policy=escalate_once)
        except ActivityError:
            pass
        return {"state": "blocked", "reason": reason, "attempts": self.attempt}


@workflow.defn(name="OpsTaskWorkflow")
class OpsTaskWorkflow:
    """Governed ops task (T3): morning_report as activities with run_key idempotency.

    Delivery readback is the verifier step; silent_delivery falls through to the
    fallback sink. A kill mid-step resumes without double-send (file-exists
    delivery, replayed transitions). Revoked/invalid contract parks pre-claim.
    """

    @workflow.run
    async def run(self, inp: OpsInput) -> OpsResult:
        wh = retry_policy_for("workhub")
        ops = timedelta(seconds=60)
        hb = timedelta(seconds=20)  # kill detection: healthy ops steps finish in seconds
        prep = await workflow.execute_activity_method(
            Activities.ops_prepare, inp, task_queue=Q_ORCH,
            start_to_close_timeout=ops, heartbeat_timeout=hb, retry_policy=wh)
        if prep["state"] == "done":  # rerun after completion: hand back the receipt
            return OpsResult("done", OPS_DIGEST_DONE, prep["task_id"],
                             prep["message_id"], prep["sink"])
        if prep["state"] == "blocked":  # expired grant: park with escalation record
            return await self._park(inp, prep["task_id"], prep.get("reason", "blocked"),
                                    None, ops)
        try:
            await workflow.execute_activity_method(
                Activities.ops_compile, args=[inp, prep["task_id"]], task_queue=Q_ORCH,
                start_to_close_timeout=ops, heartbeat_timeout=hb, retry_policy=wh)
            claimed = await workflow.execute_activity_method(
                Activities.ops_claim, args=[inp, prep["task_id"]], task_queue=Q_ORCH,
                start_to_close_timeout=ops, heartbeat_timeout=hb, retry_policy=wh)
            for sink in ("primary", "fallback"):
                sent = await workflow.execute_activity_method(
                    Activities.ops_deliver, args=[inp, prep["task_id"], sink, claimed["report"]],
                    task_queue=Q_ORCH, start_to_close_timeout=ops, heartbeat_timeout=hb,
                    retry_policy=wh)
                v = await workflow.execute_activity_method(
                    Activities.ops_verify,
                    args=[inp, prep["task_id"], sink, sent["message_id"]],
                    task_queue=Q_ORCH, start_to_close_timeout=ops, heartbeat_timeout=hb,
                    retry_policy=wh)
                if v["verdict"] == "pass":
                    return await workflow.execute_activity_method(
                        Activities.ops_close,
                        args=[inp, prep["task_id"], "pass", sink, sent["message_id"], v["figures"]],
                        task_queue=Q_ORCH, start_to_close_timeout=ops, heartbeat_timeout=hb,
                        retry_policy=wh)
            return await workflow.execute_activity_method(
                Activities.ops_close,
                args=[inp, prep["task_id"], "fail", "fallback", sent["message_id"], v["figures"]],
                task_queue=Q_ORCH, start_to_close_timeout=ops, heartbeat_timeout=hb,
                retry_policy=wh)
        except ActivityError as e:
            return await self._park(inp, prep["task_id"], _code(e), wh, ops)

    async def _park(self, inp: OpsInput, task_id: str, reason: str, wh, ops) -> OpsResult:
        try:
            return await workflow.execute_activity_method(
                Activities.ops_block, args=[inp, task_id, reason], task_queue=Q_ORCH,
                start_to_close_timeout=ops, heartbeat_timeout=timedelta(seconds=20),
                retry_policy=wh)
        except ActivityError:
            return OpsResult("blocked", f"ESCALATED: {reason}", task_id)


@workflow.defn(name="ScheduledRunWorkflow")
class ScheduledRunWorkflow:
    """Started by a Temporal Schedule. Fires one TaskWorkflow child per tick for an approved template revision.
    Child id is deterministic per (task, revision, tick) via scheduled_child_id so overlapping ticks cannot
    double-run. It never reuses workflow_id (the one-task-one-workflow id ignores the revision)."""

    @workflow.run
    async def run(self, wf: TaskWorkflowInput) -> dict:
        tick = workflow.now().strftime("%Y%m%dT%H%M")
        child_id = scheduled_child_id(wf.tenant_id, wf.task_id, wf.contract_rev, tick)
        try:
            result = await workflow.execute_child_workflow(TaskWorkflow.run, wf, id=child_id,
                                                           task_queue=Q_ORCH,
                                                           id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE)
        except WorkflowAlreadyStartedError:
            return {"tick": tick, "child": child_id, "result": None, "deduped": True}  # dup tick: no 2nd child
        return {"tick": tick, "child": child_id, "result": result}
