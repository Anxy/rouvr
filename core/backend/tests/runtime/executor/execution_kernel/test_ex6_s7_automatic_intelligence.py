"""AIX-EXECUTION-006-S7: Automatic Intelligence Wiring.

A zero-trust audit found two real gaps in decompose_planning_objective:
1. capability_reuse_search existed (EX5-S10) but was never called from
   planning — only reachable as a separate, manually-invoked /reasoning/query
   a caller had to already know to make.
2. Omitting capability_id silently no-op'd the EX5-S6 learning loop with no
   signal to the caller — indistinguishable from "no relevant history".

Both are fixed in decompose_planning_objective (service.py and
postgres_service.py): capability_id is derived from problem_id's parent
capability when not supplied, every response reports an explicit
learning_status, and reuse search now runs as part of the same call.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter.postgres_service import PostgresKernelService
from backend.infrastructure.postgres.execution_kernel.repository import resolve_test_database_url
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService


async def _promote_to_active_knowledge(
    svc: KernelService, *, tenant_id: str, capability_id: str, summary: str
) -> str:
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
async def test_learning_status_is_never_silently_ambiguous() -> None:
    """Three distinct 'no learning applied' cases must be distinguishable
    from each other and from genuine success — never just absent."""
    svc = KernelService()
    objective = "Build a status dashboard"

    no_tenant = await svc.decompose_planning_objective(objective=objective)
    assert no_tenant["learning_status"] == "skipped_no_tenant_id"
    assert no_tenant["capability_id_used"] is None

    no_capability = await svc.decompose_planning_objective(objective=objective, tenant_id="t_s7")
    assert no_capability["learning_status"] == "skipped_no_capability_id"

    fresh_capability = await svc.decompose_planning_objective(
        objective=objective, tenant_id="t_s7", capability_id="cap_fresh_s7",
    )
    assert fresh_capability["learning_status"] == "no_relevant_history"
    assert fresh_capability["relevant_learning_count"] == 0


@pytest.mark.asyncio
async def test_capability_id_derived_from_problem_id_when_not_supplied() -> None:
    """The 'derive it safely' half: a caller who supplies problem_id but not
    capability_id must still get the capability_id resolved from the
    problem's own parent, not a silent skip."""
    svc = KernelService()
    tenant_id = f"t_derive_{uuid4().hex[:8]}"
    capability_id = f"cap_{uuid4().hex[:8]}"
    problem_id = f"pr_{uuid4().hex[:8]}"

    await svc.append_and_project(
        tenant_id=tenant_id, aggregate_type="capability", aggregate_id=capability_id,
        expected_sequence_number=1, event_type="CAPABILITY_CREATED", command_id="c1",
        payload={"status": "ACTIVE", "name": "Derived capability"},
    )
    await svc.append_and_project(
        tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id,
        expected_sequence_number=1, event_type="PROBLEM_CREATED", command_id="c2",
        payload={"status": "EXECUTING", "parent_id": capability_id},
    )

    result = await svc.decompose_planning_objective(
        objective="Build a status dashboard", tenant_id=tenant_id, problem_id=problem_id,
    )
    assert result["capability_id_used"] == capability_id
    assert result["capability_id_source"] == "derived_from_problem"
    assert result["learning_status"] == "no_relevant_history"


@pytest.mark.asyncio
async def test_project_2_plans_better_via_derived_capability_not_just_explicit_id() -> None:
    """The measurable-improvement claim, proven through the SAME path a real
    caller who only knows problem_id (not capability_id) would use — not
    just the synthetic explicit-capability_id unit test EX5-S6 already has."""
    svc = KernelService()
    tenant_id = f"t_p1p2_{uuid4().hex[:8]}"
    capability_id = f"cap_{uuid4().hex[:8]}"
    problem_id = f"pr_{uuid4().hex[:8]}"
    objective = "Implement device registration. Verify barcode binding. Add audit logging."

    await svc.append_and_project(
        tenant_id=tenant_id, aggregate_type="capability", aggregate_id=capability_id,
        expected_sequence_number=1, event_type="CAPABILITY_CREATED", command_id="c1",
        payload={"status": "ACTIVE", "name": "Device registration"},
    )
    await svc.append_and_project(
        tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id,
        expected_sequence_number=1, event_type="PROBLEM_CREATED", command_id="c2",
        payload={"status": "EXECUTING", "parent_id": capability_id},
    )

    # "Project 1": no history yet, caller only has problem_id.
    project_1 = await svc.decompose_planning_objective(
        objective=objective, tenant_id=tenant_id, problem_id=problem_id,
    )
    assert project_1["capability_id_source"] == "derived_from_problem"
    assert "known_failure_pattern_applies" not in project_1["quality"]["reasons"]

    # Project 1 fails; governed-approved into ACTIVE_KNOWLEDGE for that capability.
    await _promote_to_active_knowledge(
        svc, tenant_id=tenant_id, capability_id=capability_id,
        summary="Device registration must precede barcode binding — Project 1 failed skipping this order",
    )

    # "Project 2": identical objective, same problem — caller STILL only
    # supplies problem_id, never learns or passes capability_id explicitly.
    project_2 = await svc.decompose_planning_objective(
        objective=objective, tenant_id=tenant_id, problem_id=problem_id,
    )
    assert project_2["learning_status"] == "applied"
    assert "known_failure_pattern_applies" in project_2["quality"]["reasons"]
    assert project_2["quality"]["score"] < project_1["quality"]["score"], (
        "Project 2 must plan measurably better (lower risk score, explained) than "
        "Project 1 — reached through problem_id-only derivation, the realistic caller path"
    )


@pytest.mark.asyncio
async def test_reuse_recommendation_is_returned_automatically_during_planning() -> None:
    """Capability reuse search must run AS PART of planning, not require the
    caller to separately know to call /reasoning/query afterward."""
    svc = KernelService()
    tenant_id = f"t_reuse_{uuid4().hex[:8]}"

    await svc.register_capability(
        tenant_id=tenant_id,
        capability_key="status-dashboard-widget",
        version="1",
        display_name="Status Dashboard Widget",
        description="Reusable status dashboard widget with live metrics",
    )

    result = await svc.decompose_planning_objective(
        objective="Build a status dashboard widget for the ops team", tenant_id=tenant_id,
    )
    assert result["reuse_recommendation"] is not None
    answers = result["reuse_recommendation"].get("answers", [])
    assert any("status-dashboard-widget" in (a.get("statement") or "") for a in answers), (
        f"expected the registered capability to surface as a reuse recommendation, got {answers}"
    )


@pytest.mark.asyncio
async def test_postgres_backend_has_the_same_derivation_and_reuse_behavior() -> None:
    """Parity check: the Postgres-backed twin must behave identically, not
    just the in-memory KernelService the other tests in this file exercise."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    svc = PostgresKernelService(database_url=url)
    await svc.ensure_schema()
    tenant_id = f"t_pg_s7_{uuid4().hex[:8]}"
    capability_id = f"cap_{uuid4().hex[:8]}"
    problem_id = f"pr_{uuid4().hex[:8]}"

    await svc.append_and_project(
        tenant_id=tenant_id, aggregate_type="capability", aggregate_id=capability_id,
        expected_sequence_number=1, event_type="CAPABILITY_CREATED", command_id="c1",
        payload={"status": "ACTIVE", "name": "Derived capability"},
    )
    await svc.append_and_project(
        tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id,
        expected_sequence_number=1, event_type="PROBLEM_CREATED", command_id="c2",
        payload={"status": "EXECUTING", "parent_id": capability_id},
    )
    await svc.register_capability(
        tenant_id=tenant_id, capability_key="reusable-widget", version="1",
        display_name="Reusable Widget", description="A reusable widget for dashboards",
    )

    result = await svc.decompose_planning_objective(
        objective="Build a widget for a dashboard", tenant_id=tenant_id, problem_id=problem_id,
    )
    assert result["capability_id_used"] == capability_id
    assert result["capability_id_source"] == "derived_from_problem"
    assert result["learning_status"] == "no_relevant_history"
    assert result["reuse_recommendation"] is not None
    answers = result["reuse_recommendation"].get("answers", [])
    assert any("reusable-widget" in (a.get("statement") or "") for a in answers)
