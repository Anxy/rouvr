"""rouvr/starter.py — idempotent start used by the thin endpoint (rouvr/api.py, owner decision 2) and the
`rouvr start` operator CLI. `rouvr schedule` (recurring work) is OUT OF SCOPE (owner decision 1: coding only); kept, unused."""
from __future__ import annotations

import argparse
import asyncio
import os
from datetime import timedelta

from temporalio.client import (Client, Schedule, ScheduleActionStartWorkflow, ScheduleOverlapPolicy, SchedulePolicy,
                               ScheduleSpec)
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from rouvr.config import Settings
from rouvr.contracts import TaskWorkflowInput
from rouvr.ids import workflow_id
from rouvr.workflows import Q_ORCH, ScheduledRunWorkflow, TaskWorkflow
from rouvr.workhub.client import WorkHubClient


async def start_task_workflow(client: Client, wf: TaskWorkflowInput, *, report: bool = False):
    wid = workflow_id(wf.tenant_id, wf.task_id, wf.contract_rev)
    started = True
    try:
        await client.start_workflow(TaskWorkflow.run, wf, id=wid, task_queue=Q_ORCH,
                                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                                    execution_timeout=timedelta(hours=6))
    except WorkflowAlreadyStartedError:
        started = False  # same task+revision already running/finished: return the same id
    return (wid, started) if report else wid


async def create_schedule(client: Client, wf: TaskWorkflowInput, schedule_id: str, cron: str, tz: str) -> None:
    await client.create_schedule(schedule_id, Schedule(
        action=ScheduleActionStartWorkflow(ScheduledRunWorkflow.run, wf, id=f"rouvr-sched-{schedule_id}",
                                           task_queue=Q_ORCH),
        spec=ScheduleSpec(cron_expressions=[cron], time_zone_name=tz),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP)))


async def run_start(*, task_number: int, requested_by: str) -> str:
    """Operator start: read the task, idempotently start its workflow. Returns the workflow id."""
    wf, s = await _input_for(task_number, requested_by)
    client = await Client.connect(s.temporal_address, namespace=s.temporal_namespace)
    return await start_task_workflow(client, wf)


def schedule_scope(task_number: int) -> str:
    """Grant scope a schedule for this task requires (T5)."""
    return f"schedule:task:{task_number}"


async def run_schedule(*, task_number: int, requested_by: str, schedule_id: str,
                       cron: str, tz: str = "Asia/Kolkata") -> str:
    """Create the Temporal schedule. Callers must hold an active grant for schedule_scope()."""
    wf, s = await _input_for(task_number, requested_by)
    client = await Client.connect(s.temporal_address, namespace=s.temporal_namespace)
    await create_schedule(client, wf, schedule_id, cron, tz)
    return schedule_id


async def _input_for(task_number: int, requested_by: str) -> tuple[TaskWorkflowInput, Settings]:
    s = Settings.from_env()
    if s.workhub_backend == "ledger":
        from rouvr.workhub.ledger import LedgerWorkHub
        wh = LedgerWorkHub(s.workhub_base_url)
    else:
        jwt = os.environ.get("WORKHUB_JWT")
        if not jwt:
            raise RuntimeError("WORKHUB_JWT is required for `rouvr start`")
        wh = WorkHubClient(s.workhub_base_url, s.tenant_id, s.project_id, jwt)
    try:
        task = await wh.task_by_number(task_number)
    finally:
        await wh.aclose()
    spec_timeout_s = 1500
    try:
        from rouvr.execspec import LocalExecSpecProvider
        spec_timeout_s = (await LocalExecSpecProvider(s.spec_root).get(s.tenant_id, task)).timeout_s
    except Exception:
        pass  # missing/bad spec still fails in assign_task; keep the ExecSpec default here
    return TaskWorkflowInput(tenant_id=s.tenant_id, project_id=s.project_id, task_id=str(task["id"]),
                             contract_rev=str(task["contract_rev"]), requested_by=requested_by,
                             max_rework=s.max_rework, assign_start_to_close_s=s.assign_start_to_close_s,
                             spec_timeout_s=spec_timeout_s), s


async def _main(a: argparse.Namespace) -> None:
    if a.cmd == "start":
        print(await run_start(task_number=a.task_number, requested_by=a.requested_by))
    else:
        wf, s = await _input_for(a.task_number, a.requested_by)
        client = await Client.connect(s.temporal_address, namespace=s.temporal_namespace)
        await create_schedule(client, wf, a.schedule_id, a.cron, a.tz)
        print(a.schedule_id)


def main() -> None:
    p = argparse.ArgumentParser(prog="rouvr")
    sub = p.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("start"); st.add_argument("--task-number", type=int, required=True)
    st.add_argument("--requested-by", required=True)
    sc = sub.add_parser("schedule"); sc.add_argument("--task-number", type=int, required=True)
    sc.add_argument("--requested-by", required=True); sc.add_argument("--schedule-id", required=True)
    sc.add_argument("--cron", required=True); sc.add_argument("--tz", default="Asia/Kolkata")
    asyncio.run(_main(p.parse_args()))
