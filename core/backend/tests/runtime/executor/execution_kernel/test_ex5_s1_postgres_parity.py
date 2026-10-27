"""EX5-S1: P8–P12 API parity — PostgresKernelService mirrors KernelService."""

from __future__ import annotations

import inspect
from uuid import uuid4

import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter.postgres_service import PostgresKernelService
from backend.infrastructure.postgres.execution_kernel.repository import resolve_test_database_url
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService

P8_P12_METHODS = (
    "observe_learning",
    "list_learning",
    "advance_learning",
    "classify_learning_scope",
    "set_learning_confidence",
    "approve_learning",
    "reject_learning",
    "semantic_search",
    "semantic_technology_decision",
    "interpret_planning_objective",
    "decompose_planning_objective",
    "evaluate_planning_autonomy",
    "detect_planning_replan_triggers",
    "propose_plan_mutation",
    "reason_organizationally",
    "reasoning_golden_scenarios",
    "enterprise_scenarios",
    "portfolio_intelligence_view",
    "start_enterprise_improvement_cycle",
    "strategic_programme_resume",
    "list_enterprise_audit",
)


def test_postgres_kernel_declares_p8_p12_parity_methods() -> None:
    """Static parity: every P8–P12 KernelService method exists on PostgresKernelService."""
    for name in P8_P12_METHODS:
        assert hasattr(KernelService, name), name
        assert hasattr(PostgresKernelService, name), f"Postgres missing {name}"
        assert inspect.iscoroutinefunction(getattr(PostgresKernelService, name)), name


@pytest.mark.asyncio
async def test_postgres_p8_p12_roundtrip_when_url_configured() -> None:
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    service = PostgresKernelService(database_url=url)
    await service.ensure_schema()
    # AIX-EXECUTION-006-S6: randomized so this file is safely re-runnable
    # against a persistent database.
    run_suffix = uuid4().hex[:10]
    tenant = f"t_ex5s1_{run_suffix}"
    suffix = f"ex5s1_{run_suffix}"

    # Bootstrap minimal graph for planning / portfolio / resume
    await service.append_and_project(
        tenant_id=tenant,
        aggregate_type="project",
        aggregate_id=f"proj_{suffix}",
        expected_sequence_number=1,
        event_type="PROJECT_CREATED",
        command_id=f"cmd_p_{suffix}",
        payload={"status": "ACTIVE", "vision_summary": "V", "requirements_summary": "R"},
    )
    await service.append_and_project(
        tenant_id=tenant,
        aggregate_type="capability",
        aggregate_id=f"cap_{suffix}",
        expected_sequence_number=1,
        event_type="CAPABILITY_CREATED",
        command_id=f"cmd_c_{suffix}",
        payload={"status": "ACTIVE", "name": "C", "parent_id": f"proj_{suffix}"},
    )
    await service.append_and_project(
        tenant_id=tenant,
        aggregate_type="problem",
        aggregate_id=f"pr_{suffix}",
        expected_sequence_number=1,
        event_type="PROBLEM_CREATED",
        command_id=f"cmd_pr_{suffix}",
        payload={"status": "EXECUTING", "parent_id": f"cap_{suffix}", "charter_summary": "X"},
    )
    await service.append_and_project(
        tenant_id=tenant,
        aggregate_type="work_item",
        aggregate_id=f"wi_{suffix}",
        expected_sequence_number=1,
        event_type="WI_CREATED",
        command_id=f"cmd_w_{suffix}",
        payload={"status": "PENDING", "parent_id": f"pr_{suffix}", "title": "T"},
    )

    # P8 learning
    observed = await service.observe_learning(
        tenant_id=tenant, summary="device enrollment before barcodes"
    )
    learning_id = observed["learning_id"]
    for stage in (
        "CANDIDATE_LESSON",
        "VALIDATION",
        "SCOPE_CLASSIFICATION",
        "CONFIDENCE",
        "AWAITING_APPROVAL",
    ):
        await service.advance_learning(
            tenant_id=tenant, learning_id=learning_id, to_stage=stage
        )
    await service.classify_learning_scope(
        tenant_id=tenant, learning_id=learning_id, scope="local"
    )
    await service.set_learning_confidence(
        tenant_id=tenant, learning_id=learning_id, confidence="high"
    )
    approved = await service.approve_learning(
        tenant_id=tenant,
        learning_id=learning_id,
        actor_id="tester",
        proposal_level=1,
        promote_to_knowledge=True,
        command_id=f"cmd_lrn_{suffix}",
    )
    assert approved["stage"] == "ACTIVE_KNOWLEDGE"
    listed = await service.list_learning(tenant_id=tenant, active_only=True)
    assert any(r["learning_id"] == learning_id for r in listed)

    # Durable reconnect
    service2 = PostgresKernelService(database_url=url)
    listed2 = await service2.list_learning(tenant_id=tenant, active_only=True)
    assert any(r["learning_id"] == learning_id for r in listed2)

    # P9 semantic
    tech = await service.semantic_technology_decision()
    assert tech["preferred_durable"] == "postgres_pgvector"
    hits = await service.semantic_search(
        tenant_id=tenant, query="enroll devices before barcodes", limit=5
    )
    assert isinstance(hits, list)

    # P10 planning
    plan = await service.decompose_planning_objective(
        objective="Ship enrollment before barcode attachment"
    )
    assert "work_items" in plan
    autonomy = await service.evaluate_planning_autonomy(
        mutation_kind="replan_within_capability",
        tenant_id=tenant,
        problem_id=f"pr_{suffix}",
    )
    assert "required_proposal_level" in autonomy
    triggers = await service.detect_planning_replan_triggers(
        tenant_id=tenant, problem_id=f"pr_{suffix}"
    )
    assert triggers["work_item_count"] >= 1

    # P11 reasoning
    golden = await service.reasoning_golden_scenarios()
    assert len(golden) >= 1
    reasoned = await service.reason_organizationally(
        tenant_id=tenant,
        mode="pre_planning_brief",
        capability_id=f"cap_{suffix}",
        question="What do we know?",
    )
    assert reasoned.get("mode") == "pre_planning_brief" or "answer" in reasoned or "provenance" in reasoned

    # P12 enterprise
    scenarios = await service.enterprise_scenarios()
    assert any(s["id"] == "E1" for s in scenarios)
    portfolio = await service.portfolio_intelligence_view(tenant_id=tenant)
    assert portfolio["metrics"]["project_count"] >= 1
    cycle = await service.start_enterprise_improvement_cycle(
        tenant_id=tenant, summary="Adopt enrollment pattern org-wide"
    )
    assert cycle["silent_org_promotion"] is False
    assert cycle["blocked_low_level_activation"] is True
    await service.capture_checkpoint(
        tenant_id=tenant,
        problem_id=f"pr_{suffix}",
        repository_state={
            "repository_id": "r",
            "branch": "main",
            "base_commit_sha": "a",
            "current_commit_sha": "b",
            "working_tree_diff_reference": "d",
            "untracked_artifact_references": [],
            "environment_manifest_reference": "e",
        },
    )
    resume = await service.strategic_programme_resume(
        tenant_id=tenant, problem_id=f"pr_{suffix}", capability_id=f"cap_{suffix}"
    )
    assert resume["scenario"] == "long_horizon_strategic_resume"
    audit = await service.list_enterprise_audit(tenant_id=tenant)
    assert len(audit) >= 1
