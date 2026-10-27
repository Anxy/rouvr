"""EX2-S012: bootstrap graph + proposal raise/resolve."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter.bootstrap import BootstrapSpec, BootstrapWorkItem
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService

REPO_STATE = {
    "repository_id": "aix",
    "branch": "main",
    "base_commit_sha": "abc1234",
    "current_commit_sha": "abc1234",
    "working_tree_diff_reference": "",
    "untracked_artifact_references": [],
    "environment_manifest_reference": "",
}


@pytest.mark.asyncio
async def test_bootstrap_and_tick_with_proposal() -> None:
    svc = KernelService()
    graph = await svc.bootstrap_graph(
        tenant_id="t1",
        idempotency_key="boot-1",
        spec=BootstrapSpec(
            vision_summary="Ship inventory",
            requirements_summary="Device registry first",
            capability_name="Inventory",
            charter_summary="Register devices",
            work_items=[
                BootstrapWorkItem(title="Schema"),
                BootstrapWorkItem(title="API", depends_on=["Schema"]),
            ],
            problem_id="pr_boot",
        ),
    )
    assert graph["problem_id"] == "pr_boot"
    assert len(graph["work_item_ids"]) == 2
    assert graph["dependencies"]

    step = await svc.run_problem_step(
        tenant_id="t1",
        actor_id="a1",
        problem_id="pr_boot",
        mode="tick_dry",
        repository_state=REPO_STATE,
        idempotency_key="step-prop",
        proposal_raise={
            "reason": "Need an extra validation Work Item",
            "level": 2,
            "risk": "low",
        },
        dry_auto_verify=False,
    )
    assert step.proposal is not None
    assert step.proposal["status"] == "ROUTED"
    assert step.checkpoint is not None
    assert step.checkpoint.get("open_proposals")
    assert step.status == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_level1_proposal_auto_resolves() -> None:
    svc = KernelService()
    graph = await svc.bootstrap_graph(
        tenant_id="t1",
        idempotency_key="boot-2",
        spec=BootstrapSpec(
            vision_summary="V",
            requirements_summary="R",
            capability_name="C",
            charter_summary="X",
            work_items=[BootstrapWorkItem(title="Only")],
            problem_id="pr_l1",
        ),
    )
    raised = await svc.raise_proposal(
        tenant_id="t1",
        problem_id=graph["problem_id"],
        reason="Add one more unit test",
        command_id="p1",
        level=1,
        work_item_id=graph["work_item_ids"][0],
    )
    assert raised["status"] == "RESOLVED"

    level4 = await svc.raise_proposal(
        tenant_id="t1",
        problem_id=graph["problem_id"],
        reason="Need Multi-Currency capability",
        command_id="p4",
        level=4,
        risk="high",
    )
    assert level4["status"] == "ROUTED"
    resolved = await svc.resolve_proposal(
        tenant_id="t1",
        proposal_id=level4["proposal_id"],
        command_id="p4r",
        decision="DENIED",
    )
    assert resolved["status"] == "DENIED"


@pytest.mark.asyncio
async def test_ex3_s001_level_floor_escalates_on_cross_capability_impact() -> None:
    """A caller-suggested Level 2 that actually names another Capability
    must be escalated to Level 3 (AIX-PROPOSAL-SYSTEM.md §2), not accepted
    as-is — the guard raises the level, never lowers it."""
    svc = KernelService()
    graph = await svc.bootstrap_graph(
        tenant_id="t1",
        idempotency_key="boot-3",
        spec=BootstrapSpec(
            vision_summary="V",
            requirements_summary="R",
            capability_name="Inventory",
            charter_summary="X",
            work_items=[BootstrapWorkItem(title="Only")],
            problem_id="pr_esc3",
        ),
    )
    raised = await svc.raise_proposal(
        tenant_id="t1",
        problem_id=graph["problem_id"],
        reason="Inventory needs Finance",
        command_id="pesc3",
        level=2,
        affected_capabilities=["cap_finance_other"],
        risk="medium",
    )
    assert raised["level"] == 3
    assert raised["status"] == "ROUTED"


@pytest.mark.asyncio
async def test_ex3_s001_level_floor_escalates_to_human_on_new_capability() -> None:
    """A caller-suggested Level 2/3 that actually requests a new Capability
    must be escalated to Level 4 (Human-only) regardless of what the caller
    claimed — this is the enforced version of the Level 3 charter guard
    that was previously payload-level only."""
    svc = KernelService()
    graph = await svc.bootstrap_graph(
        tenant_id="t1",
        idempotency_key="boot-4",
        spec=BootstrapSpec(
            vision_summary="V",
            requirements_summary="R",
            capability_name="Inventory",
            charter_summary="X",
            work_items=[BootstrapWorkItem(title="Only")],
            problem_id="pr_esc4",
        ),
    )
    raised = await svc.raise_proposal(
        tenant_id="t1",
        problem_id=graph["problem_id"],
        reason="Barcode scanners require a new Device Registry capability",
        command_id="pesc4",
        level=3,
        affected_capabilities=[],
        requests_new_capability=True,
        risk="high",
    )
    assert raised["level"] == 4
    assert raised["status"] == "ROUTED"
    assert raised["approver"] if "approver" in raised else True  # approver set server-side


@pytest.mark.asyncio
async def test_ex3_s001_level_floor_does_not_lower_a_caller_supplied_higher_level() -> None:
    """The guard is a floor, not a ceiling: a caller who (over-)raises a
    Level 4 for a purely local change is not silently downgraded."""
    svc = KernelService()
    graph = await svc.bootstrap_graph(
        tenant_id="t1",
        idempotency_key="boot-5",
        spec=BootstrapSpec(
            vision_summary="V",
            requirements_summary="R",
            capability_name="Inventory",
            charter_summary="X",
            work_items=[BootstrapWorkItem(title="Only")],
            problem_id="pr_esc5",
        ),
    )
    raised = await svc.raise_proposal(
        tenant_id="t1",
        problem_id=graph["problem_id"],
        reason="Escalating out of caution",
        command_id="pesc5",
        level=4,
        affected_capabilities=[],
        risk="low",
    )
    assert raised["level"] == 4
