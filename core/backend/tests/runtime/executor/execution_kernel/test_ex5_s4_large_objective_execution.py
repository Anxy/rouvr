"""EX5-S4: Large Objective Execution — correctness at 1000+ Work Items.

Charter goal: "1000+ work items without context loss." This proves the
graph/projection/checkpoint machinery stays *correct* at that scale (does
not drop items, does not misreport actionable status, resume still works)
across two topology shapes — a long dependency chain (worst case for
active_dependencies' current implementation) and a wide independent set
(best case) — and records real, measured timing rather than asserting an
SLA (documented performance targets are EX5-S9's job, not this sprint's).
"""

from __future__ import annotations

import time

import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService

N = 1000


async def _build_chain(svc: KernelService, *, tenant_id: str, problem_id: str) -> None:
    await svc.append_and_project(
        tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id,
        expected_sequence_number=1, event_type="PROBLEM_CREATED", command_id=f"problem_created_{problem_id}",
        payload={"status": "EXECUTING"},
    )
    for i in range(N):
        await svc.append_and_project(
            tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=f"wi_{i}",
            expected_sequence_number=1, event_type="WI_CREATED", command_id=f"wi_created_{i}",
            payload={"status": "PENDING", "parent_id": problem_id},
        )
        if i > 0:
            await svc.add_dependency(
                tenant_id=tenant_id, work_item_id=f"wi_{i}",
                depends_on_work_item_id=f"wi_{i - 1}", command_id=f"dep_added_{i}",
            )


@pytest.mark.asyncio
async def test_1000_item_dependency_chain_progresses_correctly() -> None:
    """The hardest correctness case: a linear chain of 1000 dependent Work
    Items. Only the unblocked head must ever be actionable — never more,
    never fewer — and unlocking the head must correctly reveal exactly the
    next item, not the whole remaining chain or nothing at all."""
    svc = KernelService()
    problem_id = "pr_chain"
    t0 = time.perf_counter()
    await _build_chain(svc, tenant_id="t1", problem_id=problem_id)
    build_seconds = time.perf_counter() - t0

    children = svc.projection.list_children(tenant_id="t1", aggregate_type="work_item", parent_id=problem_id)
    assert len(children) == N, "no work item may be silently lost while building at this scale"

    t1 = time.perf_counter()
    actionable = await svc.next_actionable(tenant_id="t1", problem_id=problem_id)
    query_seconds = time.perf_counter() - t1
    assert actionable == ["wi_0"], "only the unblocked chain head may be actionable"

    await svc.append_and_project(
        tenant_id="t1", aggregate_type="work_item", aggregate_id="wi_0",
        expected_sequence_number=2, event_type="WI_VERIFIED", command_id="wi_verify_0",
        payload={"status": "VERIFIED", "parent_id": problem_id},
    )
    actionable_after = await svc.next_actionable(tenant_id="t1", problem_id=problem_id)
    assert actionable_after == ["wi_1"], "unlocking the head must reveal exactly the next chain link"

    # Recorded, not asserted as a hard SLA (that's EX5-S9's job) — but a
    # regression-guard bound so a future change doesn't silently make this
    # unusably slow without anyone noticing. Chain topology is O(N^2) in
    # today's InMemoryDependencyGraph.active_dependencies() implementation
    # (every candidate re-scans the full dependency-event list) — this is
    # a known, documented gap, not fixed in this sprint.
    assert build_seconds < 5.0, f"build took {build_seconds:.2f}s — investigate before shipping"
    assert query_seconds < 5.0, f"next_actionable took {query_seconds:.2f}s — investigate before shipping"


@pytest.mark.asyncio
async def test_1000_independent_work_items_all_actionable_at_once() -> None:
    """The easy correctness case: 1000 Work Items with no dependencies at
    all must all be reported actionable, and quickly — this is the
    topology InMemoryDependencyGraph already handles efficiently, proving
    the chain case's cost above is genuinely about dependency-chain depth,
    not about scale in general."""
    svc = KernelService()
    problem_id = "pr_star"
    await svc.append_and_project(
        tenant_id="t1", aggregate_type="problem", aggregate_id=problem_id,
        expected_sequence_number=1, event_type="PROBLEM_CREATED", command_id=f"problem_created_{problem_id}",
        payload={"status": "EXECUTING"},
    )
    for i in range(N):
        await svc.append_and_project(
            tenant_id="t1", aggregate_type="work_item", aggregate_id=f"star_wi_{i}",
            expected_sequence_number=1, event_type="WI_CREATED", command_id=f"star_wi_created_{i}",
            payload={"status": "PENDING", "parent_id": problem_id},
        )

    t0 = time.perf_counter()
    actionable = await svc.next_actionable(tenant_id="t1", problem_id=problem_id)
    query_seconds = time.perf_counter() - t0

    assert len(actionable) == N, "every independent item must be actionable — none dropped"
    assert query_seconds < 1.0, (
        f"independent-topology next_actionable took {query_seconds:.2f}s — "
        "this shape should stay fast; a regression here would suggest a "
        "broader complexity problem, not just the known chain-specific one"
    )


@pytest.mark.asyncio
async def test_checkpoint_and_resume_correct_at_1000_item_scale() -> None:
    """Checkpoint/resume must not drop or corrupt items when the Problem
    is large — this is the actual 'without context loss' claim for the
    checkpoint path specifically, at the scale the charter names."""
    svc = KernelService()
    problem_id = "pr_checkpoint_scale"
    await _build_chain(svc, tenant_id="t1", problem_id=problem_id)

    checkpoint = await svc.capture_checkpoint(
        tenant_id="t1", problem_id=problem_id,
        repository_state={
            "repository_id": "aix", "branch": "main", "base_commit_sha": "abc1234",
            "current_commit_sha": "abc1234", "working_tree_diff_reference": "",
            "untracked_artifact_references": [], "environment_manifest_reference": "",
        },
    )
    assert checkpoint["status"] == "CAPTURED"
    assert len(checkpoint["work_items"]) == N, "the checkpoint must capture every Work Item, not a truncated subset"

    resumed = await svc.resume(tenant_id="t1", problem_id=problem_id)
    assert resumed.source == "checkpoint"
    assert len(resumed.work_items) == N, "resume must reconstruct every Work Item, not a truncated subset"
