"""rouvr/worker.py — run the Rouvr Temporal workers (R10).

Usage: ORCHESTRATOR_TOKEN=... .venv/bin/python -m rouvr.worker [all|orch|build|test|verify]
R10 runs one process per queue so the rehearsal can kill -9 the coder only.
"""
from __future__ import annotations

import asyncio
import os
import sys

from temporalio.client import Client
from temporalio.worker import Worker

from rouvr.activities import Activities
from rouvr.config import Settings
from rouvr.host import build_host
from rouvr.workflows import Q_BUILD, Q_ORCH, Q_TEST, Q_VERIFY, ScheduledRunWorkflow, TaskWorkflow


async def run_workers(s: Settings, which: str) -> None:
    client = await Client.connect(s.temporal_address, namespace=s.temporal_namespace)
    acts = Activities(build_host(s, orchestrator_token=os.environ.get("ORCHESTRATOR_TOKEN")))
    all_workers = {
        "orch": Worker(client, task_queue=Q_ORCH, workflows=[TaskWorkflow, ScheduledRunWorkflow],
                       activities=[acts.verify_binding, acts.close_task, acts.mark_blocked, acts.escalate,
                                   acts.sign_receipt]),
        "build": Worker(client, task_queue=Q_BUILD, activities=[acts.assign_task],
                        max_concurrent_activities=s.coder_concurrency),
        "test": Worker(client, task_queue=Q_TEST, activities=[acts.run_tests]),
        "verify": Worker(client, task_queue=Q_VERIFY, activities=[acts.verify]),
    }
    if which == "all":
        await asyncio.gather(*(w.run() for w in all_workers.values()))
    else:
        await all_workers[which].run()


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which not in {"all", "orch", "build", "test", "verify"}:
        raise SystemExit(f"usage: python -m rouvr.worker [all|orch|build|test|verify]")
    asyncio.run(run_workers(Settings.from_env(), which))


if __name__ == "__main__":
    main()
