"""EX4-R4-P12-S001: Enterprise Intelligence Platform — enterprise scenarios."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.aix_core.runtime.executor.execution_kernel_adapter.bootstrap import BootstrapSpec, BootstrapWorkItem
from backend.aix_core.runtime.executor.enterprise_intelligence import ENTERPRISE_SCENARIOS
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService
from backend.routers.execution_kernel_router import router as execution_kernel_router


@pytest.mark.asyncio
async def test_p12_e1_portfolio_multi_project_coordination() -> None:
    service = KernelService()
    await service.bootstrap_graph(
        tenant_id="t1",
        idempotency_key="p12-e1-a",
        spec=BootstrapSpec(
            vision_summary="Keys A",
            requirements_summary="Scope A",
            capability_name="KeysA",
            charter_summary="Ship A",
            work_items=[BootstrapWorkItem(title="Model A", work_item_id="wi_a")],
            project_id="proj_a",
            capability_id="cap_a",
            problem_id="pr_a",
        ),
    )
    await service.bootstrap_graph(
        tenant_id="t1",
        idempotency_key="p12-e1-b",
        spec=BootstrapSpec(
            vision_summary="Keys B",
            requirements_summary="Scope B",
            capability_name="KeysB",
            charter_summary="Ship B",
            work_items=[BootstrapWorkItem(title="Model B", work_item_id="wi_b")],
            project_id="proj_b",
            capability_id="cap_b",
            problem_id="pr_b",
        ),
    )
    content = "Prefer tenant-scoped API keys"
    for project_id, kid in (("proj_a", "k_a"), ("proj_b", "k_b")):
        await service.upsert_knowledge(
            tenant_id="t1",
            knowledge_entry_id=kid,
            knowledge_kind="lesson_learned",
            content=content,
            command_id=f"p12-{kid}",
            capability_id="cap_shared",
            project_id=project_id,
            confidence="high",
        )
    await service.declare_knowledge_conflict(
        tenant_id="t1",
        left_knowledge_id="k_a",
        right_knowledge_id="k_b",
        command_id="p12-conflict",
    )

    view = await service.portfolio_intelligence_view(tenant_id="t1")
    assert view["metrics"]["project_count"] == 2
    assert view["metrics"]["active_conflict_count"] == 1
    assert view["metrics"]["shared_knowledge_group_count"] >= 1
    assert view["ai_ceo_claim"] is False
    assert view["silent_org_promotion"] is False
    assert view["bypasses_hyeve_controls"] is False
    assert {s["id"] for s in ENTERPRISE_SCENARIOS} == {"E1", "E2", "E3"}


@pytest.mark.asyncio
async def test_p12_e2_governed_org_pattern_no_silent_promotion() -> None:
    service = KernelService()
    cycle = await service.start_enterprise_improvement_cycle(
        tenant_id="t1",
        summary="Adopt tenant-scoped keys org-wide",
        scope="organisation",
        actor_id="operator_1",
    )
    assert cycle["silent_org_promotion"] is False
    assert cycle["auto_promote_blocked"] is True
    assert cycle["blocked_low_level_activation"] is True
    assert cycle["learning"]["scope"] == "organisation"
    assert cycle["learning"]["required_proposal_level"] == 4
    assert cycle["learning"]["stage"] == "AWAITING_APPROVAL"
    audit = await service.list_enterprise_audit(tenant_id="t1")
    assert any(e["event_type"] == "IMPROVEMENT_CYCLE_STARTED" for e in audit)


@pytest.mark.asyncio
async def test_p12_e3_long_horizon_strategic_resume() -> None:
    service = KernelService()
    await service.bootstrap_graph(
        tenant_id="t1",
        idempotency_key="p12-e3",
        spec=BootstrapSpec(
            vision_summary="Strategic keys",
            requirements_summary="Long horizon",
            capability_name="Strategy",
            charter_summary="Resume later",
            work_items=[
                BootstrapWorkItem(title="Phase 1", work_item_id="wi_p1"),
                BootstrapWorkItem(title="Phase 2", work_item_id="wi_p2", depends_on=["wi_p1"]),
            ],
            project_id="proj_s",
            capability_id="cap_s",
            problem_id="pr_s",
        ),
    )
    await service.capture_checkpoint(
        tenant_id="t1",
        problem_id="pr_s",
        repository_state={
            "repository_id": "aix",
            "branch": "main",
            "base_commit_sha": "abc1234",
            "current_commit_sha": "abc1234",
            "working_tree_diff_reference": "diff:none",
            "untracked_artifact_references": [],
            "environment_manifest_reference": "env:local",
        },
    )
    await service.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_s",
        knowledge_kind="architecture",
        content="Strategic programmes must resume from checkpoint",
        command_id="p12-arch",
        capability_id="cap_s",
        confidence="high",
    )

    result = await service.strategic_programme_resume(
        tenant_id="t1", problem_id="pr_s", capability_id="cap_s"
    )
    assert result["ai_ceo_claim"] is False
    assert result["state_mutations_from_platform"] == []
    assert result["resume"]["checkpoint_id"]
    assert result["resume"]["source"] == "checkpoint"
    assert result["pre_planning_brief"]["state_mutations"] == []
    audit = await service.list_enterprise_audit(tenant_id="t1")
    assert any(e["event_type"] == "STRATEGIC_RESUME" for e in audit)


@pytest.mark.asyncio
async def test_p12_enterprise_http_surface() -> None:
    service = KernelService()
    await service.bootstrap_graph(
        tenant_id="t1",
        idempotency_key="p12-http",
        spec=BootstrapSpec(
            vision_summary="HTTP",
            requirements_summary="HTTP",
            capability_name="HTTP",
            charter_summary="HTTP",
            work_items=[BootstrapWorkItem(title="WI", work_item_id="wi_h")],
            project_id="proj_h",
            capability_id="cap_h",
            problem_id="pr_h",
        ),
    )
    app = FastAPI()
    app.state.execution_kernel_service = service
    app.state.execution_kernel_require_gateway = False
    app.include_router(execution_kernel_router)
    headers = {"X-Tenant-ID": "t1"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        scenarios = await client.get("/aix/execution-kernel/enterprise/scenarios", headers=headers)
        assert scenarios.status_code == 200
        assert len(scenarios.json()["scenarios"]) == 3
        portfolio = await client.get("/aix/execution-kernel/enterprise/portfolio", headers=headers)
        assert portfolio.status_code == 200
        assert portfolio.json()["portfolio"]["metrics"]["project_count"] >= 1
        cycle = await client.post(
            "/aix/execution-kernel/enterprise/improvement-cycles",
            headers=headers,
            json={"summary": "Org pattern under governance", "scope": "organisation"},
        )
        assert cycle.status_code == 200
        assert cycle.json()["silent_org_promotion"] is False
