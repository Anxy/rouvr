"""EX5-S6: Closed Learning Loop.

Charter success metric: "Project 2 plans measurably better than Project 1."
Before this sprint, nothing connected P8's governed learning lifecycle to
P10's planning quality evaluation — Active Knowledge sat in the Knowledge
Store, retrievable by ranking, but nothing in planning ever consulted it.

This proves the actual, concrete mechanism: a capability with a recorded,
governance-approved failure produces a measurably different (lower,
explained) plan-quality score on the next decomposition for that same
capability — versus an identical objective decomposed for a capability
with no history, which scores the same as before this sprint existed.

Governance discipline (deliberately not exceeded): this only implements
Level 2 of the governance ladder (knowledge -> recommendation influencing
a planner). It never rewrites decompose_plan's own output and never
auto-updates a planning heuristic (Level 3+, still gated behind explicit
policy per REQUIRES_EXPLICIT_POLICY) — the plan produced is identical
either way; only its quality *evaluation* differs.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.executor.planning_engine import (
    decompose_plan,
    evaluate_plan_quality,
    evaluate_plan_quality_with_learning,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService


async def _promote_to_active_knowledge(
    svc: KernelService, *, tenant_id: str, capability_id: str, summary: str
) -> str:
    """Drive a learning record through the exact governed lifecycle already
    proven in test_ex4_r3_p8_s001_learning_lifecycle_governed_activation,
    scoped to 'capability' (Proposal Level 2) rather than 'organisation'
    (Level 4) — simpler, still real governance, not a shortcut."""
    obs = await svc.observe_learning(tenant_id=tenant_id, summary=summary, capability_id=capability_id)
    lid = obs["learning_id"]
    for stage in ("CANDIDATE_LESSON", "VALIDATION", "SCOPE_CLASSIFICATION", "CONFIDENCE", "AWAITING_APPROVAL"):
        await svc.advance_learning(tenant_id=tenant_id, learning_id=lid, to_stage=stage)
    await svc.classify_learning_scope(tenant_id=tenant_id, learning_id=lid, scope="capability")
    approved = await svc.approve_learning(
        tenant_id=tenant_id, learning_id=lid, actor_id="actor_1",
        proposal_level=2, proposal_id=f"prop_{lid}", promote_to_knowledge=True,
        command_id=f"lrn-approve-{lid}",
    )
    assert approved["stage"] == "ACTIVE_KNOWLEDGE"
    return lid


@pytest.mark.asyncio
async def test_project_2_plan_quality_reflects_project_1_failure_the_same_objective_does_not_get_it_for_free() -> None:
    svc = KernelService()
    objective = "Implement device registration. Verify barcode binding. Add audit logging."

    # "Project 1": decompose for a capability with NO history yet.
    plan1 = decompose_plan(objective=objective)
    quality1_before_any_learning = evaluate_plan_quality(plan1)

    # Project 1 fails; the failure is distilled and governed-approved into
    # ACTIVE_KNOWLEDGE scoped to this specific capability.
    await _promote_to_active_knowledge(
        svc, tenant_id="t1", capability_id="cap_device_reg",
        summary="Device registration must precede barcode binding — Project 1 failed skipping this order",
    )

    # "Project 2": identical objective, SAME capability — must now surface
    # the known failure pattern.
    relevant = [
        r for r in await svc.list_learning(tenant_id="t1", active_only=True)
        if r.get("capability_id") == "cap_device_reg"
    ]
    plan2 = decompose_plan(objective=objective)
    quality2_with_learning = evaluate_plan_quality_with_learning(plan2, relevant_learning=relevant)

    assert quality2_with_learning["score"] < quality1_before_any_learning["score"], (
        "Project 2's plan quality must be measurably different (lower, with an "
        "explanation) than Project 1's, which had no relevant history to draw on"
    )
    assert "known_failure_pattern_applies" in quality2_with_learning["reasons"]
    assert quality2_with_learning["relevant_learning"][0]["summary"].startswith("Device registration")

    # A DIFFERENT capability with no history must NOT get penalized — the
    # loop must not leak knowledge across unrelated capabilities.
    unrelated_capability_relevant = [
        r for r in await svc.list_learning(tenant_id="t1", active_only=True)
        if r.get("capability_id") == "cap_totally_unrelated"
    ]
    plan3 = decompose_plan(objective=objective)
    quality3_unrelated = evaluate_plan_quality_with_learning(plan3, relevant_learning=unrelated_capability_relevant)
    assert quality3_unrelated == quality1_before_any_learning, (
        "a capability with no relevant history must score identically to "
        "before this sprint existed — the loop must not apply globally"
    )


@pytest.mark.asyncio
async def test_decompose_planning_objective_service_method_wires_learning_automatically() -> None:
    """The actual caller-facing surface: KernelService.decompose_planning_objective
    fetches relevant learning itself when tenant_id + capability_id are
    given — a caller doesn't have to manually wire the loop together."""
    svc = KernelService()
    objective = "Implement device registration. Verify barcode binding."

    without_history = await svc.decompose_planning_objective(
        objective=objective, tenant_id="t1", capability_id="cap_fresh",
    )
    assert "known_failure_pattern_applies" not in without_history["quality"]["reasons"]

    await _promote_to_active_knowledge(
        svc, tenant_id="t1", capability_id="cap_with_history",
        summary="This objective shape previously failed on missing audit logging",
    )
    with_history = await svc.decompose_planning_objective(
        objective=objective, tenant_id="t1", capability_id="cap_with_history",
    )
    assert "known_failure_pattern_applies" in with_history["quality"]["reasons"]
    assert with_history["quality"]["score"] < without_history["quality"]["score"]

    # Backward compatibility: omitting tenant_id/capability_id must behave
    # exactly as before this sprint (no crash, no forced learning lookup).
    no_scope = await svc.decompose_planning_objective(objective=objective)
    assert "known_failure_pattern_applies" not in no_scope["quality"]["reasons"]
