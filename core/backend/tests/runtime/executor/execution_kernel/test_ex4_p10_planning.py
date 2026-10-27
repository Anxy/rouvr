"""EX4-R3-P10-S001: Autonomous Planning Engine — bounded core tests."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.aix_core.runtime.executor.execution_kernel_adapter.bootstrap import BootstrapSpec, BootstrapWorkItem
from backend.aix_core.runtime.executor.planning_engine import (
    MAX_WORK_ITEMS,
    bounded_autonomy_for_mutation,
    decompose_plan,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService
from backend.routers.execution_kernel_router import router as execution_kernel_router


def test_p10_decompose_caps_and_ambiguity() -> None:
    clear = decompose_plan(
        objective=(
            "Implement tenant-scoped API keys. Verify rotation works. "
            "Add audit logging for key use."
        )
    )
    assert clear["disposition"] == "CANDIDATE"
    assert 1 <= len(clear["work_items"]) <= MAX_WORK_ITEMS
    assert clear["capped"] is True

    vague = decompose_plan(objective="Do something somehow later etc")
    assert vague["ambiguous"] is True
    assert vague["disposition"] == "CLARIFICATION_REQUIRED"


def test_p10_autonomy_maps_to_proposal_levels() -> None:
    local = bounded_autonomy_for_mutation("local_title_tweak")
    assert local.required_proposal_level == 1
    assert local.auto_allowed is True

    replan = bounded_autonomy_for_mutation("replan_within_capability")
    assert replan.required_proposal_level == 2
    assert replan.auto_allowed is False

    charter = bounded_autonomy_for_mutation("new_capability_or_charter")
    assert charter.required_proposal_level == 4
    assert charter.auto_allowed is False

    cross = bounded_autonomy_for_mutation(
        "add_work_items_same_problem",
        problem_capability_id="cap_a",
        affected_capabilities=["cap_a", "cap_b"],
    )
    assert cross.required_proposal_level == 3


@pytest.mark.asyncio
async def test_p10_replan_triggers_and_proposal_rails() -> None:
    service = KernelService()
    graph = await service.bootstrap_graph(
        tenant_id="t1",
        idempotency_key="p10-boot",
        spec=BootstrapSpec(
            vision_summary="Secure keys",
            requirements_summary="Tenant scoped keys",
            capability_name="Key Mgmt",
            charter_summary="Ship key rotation",
            work_items=[
                BootstrapWorkItem(title="Add key model", work_item_id="wi_model"),
                BootstrapWorkItem(
                    title="Add rotation API",
                    work_item_id="wi_api",
                    depends_on=["wi_model"],
                ),
            ],
            project_id="proj_p10",
            capability_id="cap_p10",
            problem_id="pr_p10",
        ),
    )
    assert graph["problem_id"] == "pr_p10"

    await service.append_and_project(
        tenant_id="t1",
        aggregate_type="work_item",
        aggregate_id="wi_api",
        expected_sequence_number=2,
        event_type="WI_FAILED",
        command_id="p10-fail",
        payload={"status": "FAILED", "parent_id": "pr_p10", "title": "Add rotation API"},
    )

    triggers = await service.detect_planning_replan_triggers(
        tenant_id="t1",
        problem_id="pr_p10",
        required_knowledge_kinds=["architecture"],
        objective="Implement tenant-scoped API keys somehow later",
    )
    codes = {t["code"] for t in triggers["triggers"]}
    assert "WORK_ITEM_FAILED" in codes
    assert "MISSING_KNOWLEDGE" in codes
    assert "AMBIGUOUS_OBJECTIVE" in codes or "PLAN_QUALITY_BELOW_THRESHOLD" in codes

    proposed = await service.propose_plan_mutation(
        tenant_id="t1",
        problem_id="pr_p10",
        mutation_kind="replan_within_capability",
        reason="Failed rotation API needs replan",
        command_id="p10-propose-replan",
        work_item_id="wi_api",
        objective="Implement tenant-scoped API keys. Verify rotation. Add audits.",
    )
    assert proposed["silent_graph_mutation"] is False
    assert proposed["autonomy"]["required_proposal_level"] == 2
    assert proposed["proposal"]["level"] == 2
    assert proposed["proposal"]["status"] in {"CLASSIFIED", "ROUTED", "RAISED"}

    # Level-1 title tweak may auto-resolve via Proposal rails.
    local = await service.propose_plan_mutation(
        tenant_id="t1",
        problem_id="pr_p10",
        mutation_kind="local_title_tweak",
        reason="Clarify WI title",
        command_id="p10-propose-local",
        work_item_id="wi_model",
    )
    assert local["autonomy"]["auto_allowed"] is True
    assert local["proposal"]["level"] == 1
    assert local["proposal"]["status"] == "RESOLVED"


@pytest.mark.asyncio
async def test_p10_planning_http_surface() -> None:
    service = KernelService()
    await service.bootstrap_graph(
        tenant_id="t1",
        idempotency_key="p10-http-boot",
        spec=BootstrapSpec(
            vision_summary="Keys",
            requirements_summary="Scope keys",
            capability_name="Keys",
            charter_summary="Ship keys",
            work_items=[BootstrapWorkItem(title="Model keys", work_item_id="wi_h")],
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
        decomposed = await client.post(
            "/aix/execution-kernel/planning/decompose",
            headers=headers,
            json={
                "objective": "Must implement key rotation. Must verify audit trails.",
                "max_work_items": 12,
            },
        )
        assert decomposed.status_code == 200
        assert decomposed.json()["silent_graph_mutation"] is False
        assert decomposed.json()["plan"]["work_items"]

        autonomy = await client.post(
            "/aix/execution-kernel/planning/autonomy",
            headers=headers,
            json={"mutation_kind": "new_capability_or_charter"},
        )
        assert autonomy.status_code == 200
        assert autonomy.json()["autonomy"]["required_proposal_level"] == 4

        propose = await client.post(
            "/aix/execution-kernel/planning/problems/pr_h/propose-mutation",
            headers=headers,
            json={
                "mutation_kind": "add_work_items_same_problem",
                "reason": "Add verification WI",
                "command_id": "p10-http-propose",
            },
        )
        assert propose.status_code == 200
        assert propose.json()["silent_graph_mutation"] is False
