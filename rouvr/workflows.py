"""rouvr/workflows.py — TaskWorkflow (build→test→verify→close with bounded rework) + ScheduledRunWorkflow.

R4 port of hyeve-lane TaskLoop @ d2e39d8: workflow id = task id, four fixed role
queues. Lane-ops dispatch-queue ownership (lanes/temporal/queues.py #351/#359)
is intentionally not ported; phase 2 reintroduces wake/queueing."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from rouvr.activities import Activities
    from rouvr.contracts import AssignInput, TaskWorkflowInput
    from rouvr.errors import retry_policy_for
    from rouvr.ids import workflow_id

Q_ORCH, Q_BUILD, Q_TEST, Q_VERIFY = "rouvr-orchestrator", "rouvr-builder", "rouvr-test", "rouvr-verifier"


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
            notes, resume = "", None
            while self.attempt <= wf.max_rework:
                if self.cancel_requested:
                    return await self._block(wf, "E_CANCELLED", "")
                self.attempt += 1
                self.phase = "build"
                built = await workflow.execute_activity_method(
                    Activities.assign_task,
                    AssignInput(wf=wf, binding=b, attempt_no=self.attempt, rework_notes=notes,
                                resume_session_id=resume),
                    task_queue=Q_BUILD, start_to_close_timeout=timedelta(seconds=1800),
                    heartbeat_timeout=timedelta(seconds=90),
                    retry_policy=RetryPolicy(initial_interval=timedelta(seconds=10), backoff_coefficient=2.0,
                                             maximum_attempts=3, non_retryable_error_types=wh.non_retryable_error_types))
                self.phase = "test"
                tests = await workflow.execute_activity_method(Activities.run_tests, args=[wf, built],
                                                               task_queue=Q_TEST,
                                                               start_to_close_timeout=timedelta(seconds=1800),
                                                               retry_policy=retry_policy_for("test"))
                if not tests.passed:
                    notes = f"Tests failed:\n{tests.summary}"
                    resume = built.muse_session_id
                    continue
                self.phase = "verify"
                v = await workflow.execute_activity_method(Activities.verify, args=[wf, b, built, tests],
                                                           task_queue=Q_VERIFY,
                                                           start_to_close_timeout=timedelta(seconds=1200),
                                                           heartbeat_timeout=None,
                                                           retry_policy=retry_policy_for("verify"))
                if v.verdict == "approve":
                    self.phase = "close"
                    closed = await workflow.execute_activity_method(Activities.close_task,
                                                                    args=[wf, b, built.attempt_id], task_queue=Q_ORCH,
                                                                    start_to_close_timeout=timedelta(seconds=120),
                                                                    retry_policy=wh)
                    receipt = await workflow.execute_activity_method(Activities.sign_receipt,
                                                                     args=[wf, built, tests, v], task_queue=Q_ORCH,
                                                                     start_to_close_timeout=timedelta(seconds=60),
                                                                     retry_policy=wh)
                    self.phase = "done"
                    return {"state": closed.final_state, "pr_url": built.pr_url, "receipt": receipt,
                            "attempts": self.attempt}
                if v.verdict == "reject":
                    return await self._block(wf, "rejected_by_verifier", built.attempt_id)
                notes, resume = v.notes, built.muse_session_id
            return await self._block(wf, "rework_exhausted", f"attempt-{self.attempt}")
        except ActivityError as e:
            return await self._block(wf, _code(e), f"attempt-{self.attempt}")

    async def _block(self, wf: TaskWorkflowInput, reason: str, attempt_id: str) -> dict:
        self.phase = "blocked"
        wh = retry_policy_for("workhub")
        await workflow.execute_activity_method(Activities.mark_blocked, args=[wf, reason], task_queue=Q_ORCH,
                                               start_to_close_timeout=timedelta(seconds=120), retry_policy=wh)
        await workflow.execute_activity_method(Activities.escalate, args=[wf, reason, attempt_id], task_queue=Q_ORCH,
                                               start_to_close_timeout=timedelta(seconds=60), retry_policy=wh)
        return {"state": "blocked", "reason": reason, "attempts": self.attempt}


@workflow.defn(name="ScheduledRunWorkflow")
class ScheduledRunWorkflow:
    """Started by a Temporal Schedule. Fires one TaskWorkflow child per tick for an approved template revision.
    Child id is deterministic per (task, revision, tick) so overlapping ticks cannot double-run."""

    @workflow.run
    async def run(self, wf: TaskWorkflowInput) -> dict:
        tick = workflow.now().strftime("%Y%m%dT%H%M")
        child_id = workflow_id(wf.tenant_id, wf.task_id, f"{wf.contract_rev}@{tick}")
        result = await workflow.execute_child_workflow(TaskWorkflow.run, wf, id=child_id, task_queue=Q_ORCH,
                                                       id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE)
        return {"tick": tick, "child": child_id, "result": result}
