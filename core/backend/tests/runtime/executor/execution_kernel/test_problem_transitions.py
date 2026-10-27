"""EX2-S010: Problem REVIEW/VERIFIED ledger transitions."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService
from backend.tests.runtime.executor.execution_kernel.test_problem_loop import REPO_STATE, _seed_two_items


@pytest.mark.asyncio
async def test_accept_and_rework_problem_transitions() -> None:
    svc = KernelService()
    await _seed_two_items(svc)
    await svc.run_problem_step(
        tenant_id="t1",
        actor_id="a1",
        problem_id="pr_1",
        mode="tick_dry",
        repository_state=REPO_STATE,
        idempotency_key="a",
    )
    await svc.run_problem_step(
        tenant_id="t1",
        actor_id="a1",
        problem_id="pr_1",
        mode="tick_dry",
        repository_state=REPO_STATE,
        idempotency_key="b",
    )
    problem = await svc.get_projection(tenant_id="t1", aggregate_type="problem", aggregate_id="pr_1")
    assert problem is not None
    assert problem["status"] == "REVIEW"

    rework = await svc.run_problem_step(
        tenant_id="t1",
        actor_id="a1",
        problem_id="pr_1",
        mode="rework_problem",
        repository_state=REPO_STATE,
        idempotency_key="rework",
    )
    assert rework.status == "PROBLEM_EXECUTING"
    assert rework.problem_status == "EXECUTING"

    # Still ready for review (WIs still verified) — tick with no actionable re-enters REVIEW.
    again = await svc.run_problem_step(
        tenant_id="t1",
        actor_id="a1",
        problem_id="pr_1",
        mode="tick_dry",
        repository_state=REPO_STATE,
        idempotency_key="back",
    )
    assert again.status == "PROBLEM_IN_REVIEW"
    assert again.problem_status == "REVIEW"

    verified = await svc.run_problem_step(
        tenant_id="t1",
        actor_id="a1",
        problem_id="pr_1",
        mode="accept_problem",
        repository_state=REPO_STATE,
        idempotency_key="ok",
    )
    assert verified.status == "PROBLEM_VERIFIED"
    problem2 = await svc.get_projection(tenant_id="t1", aggregate_type="problem", aggregate_id="pr_1")
    assert problem2 is not None
    assert problem2["status"] == "VERIFIED"

    completed = await svc.run_problem_step(
        tenant_id="t1",
        actor_id="a1",
        problem_id="pr_1",
        mode="complete_problem",
        repository_state=REPO_STATE,
        idempotency_key="complete",
    )
    assert completed.status == "PROBLEM_COMPLETED"
    assert completed.problem_status == "COMPLETED"

    archived = await svc.run_problem_step(
        tenant_id="t1",
        actor_id="a1",
        problem_id="pr_1",
        mode="archive_problem",
        repository_state=REPO_STATE,
        idempotency_key="archive",
    )
    assert archived.status == "PROBLEM_ARCHIVED"
    problem3 = await svc.get_projection(tenant_id="t1", aggregate_type="problem", aggregate_id="pr_1")
    assert problem3 is not None
    assert problem3["status"] == "ARCHIVED"
