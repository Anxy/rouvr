"""EX4-R4-P11-S001: Organizational Reasoning Engine — golden scenarios."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.aix_core.runtime.executor.organizational_reasoning import GOLDEN_SCENARIOS
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService
from backend.routers.execution_kernel_router import router as execution_kernel_router


@pytest.mark.asyncio
async def test_p11_golden_g1_precedent_search_tenant_isolated() -> None:
    service = KernelService()
    await service.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_keys",
        knowledge_kind="lesson_learned",
        content="Prefer tenant-scoped API keys before shared secrets",
        command_id="p11-k1",
        capability_id="cap_1",
        project_id="proj_a",
        confidence="high",
    )
    await service.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_keys_b",
        knowledge_kind="lesson_learned",
        content="Prefer tenant-scoped API keys before shared secrets",
        command_id="p11-k2",
        capability_id="cap_1",
        project_id="proj_b",
        confidence="high",
    )
    await service.upsert_knowledge(
        tenant_id="t2",
        knowledge_entry_id="k_secret",
        knowledge_kind="lesson_learned",
        content="Prefer tenant-scoped API keys before shared secrets",
        command_id="p11-other",
        capability_id="cap_1",
        confidence="high",
    )

    result = await service.reason_organizationally(
        tenant_id="t1",
        mode="precedent_search",
        question="What precedents exist for tenant-scoped keys?",
    )
    assert result["state_mutations"] == []
    assert result["cross_tenant"] is False
    assert result["treats_inference_as_fact"] is False
    assert any(a["kind"] == "fact" and "tenant-scoped" in a["statement"] for a in result["answers"])
    assert any(a["kind"] == "inference" for a in result["answers"])
    # No t2 leakage in provenance knowledge ids
    for answer in result["answers"]:
        for prov in answer["provenance"]:
            assert prov.get("knowledge_entry_id") != "k_secret"
            ids = prov.get("knowledge_entry_ids") or []
            assert "k_secret" not in ids

    with pytest.raises(ExecutionKernelRepositoryError, match="tenant_id"):
        await service.reason_organizationally(tenant_id="", mode="precedent_search", question="x")


@pytest.mark.asyncio
async def test_p11_golden_g2_conflict_report() -> None:
    service = KernelService()
    for kid, content in (("k_old", "Use global keys"), ("k_new", "Use tenant keys")):
        await service.upsert_knowledge(
            tenant_id="t1",
            knowledge_entry_id=kid,
            knowledge_kind="architecture",
            content=content,
            command_id=f"p11-c-{kid}",
            capability_id="cap_1",
            confidence="high",
        )
    await service.declare_knowledge_conflict(
        tenant_id="t1",
        left_knowledge_id="k_old",
        right_knowledge_id="k_new",
        command_id="p11-conflict",
    )
    result = await service.reason_organizationally(
        tenant_id="t1", mode="conflict_report", question="Which conflicts are active?"
    )
    assert result["state_mutations"] == []
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["edge_type"] == "CONFLICTS_WITH"
    assert all(a.get("is_fact") is True for a in result["answers"] if a["kind"] == "fact")
    assert any(o["action"] == "resolve_knowledge_conflict" for o in result["options"])


@pytest.mark.asyncio
async def test_p11_golden_g3_pre_planning_brief_no_mutation() -> None:
    service = KernelService()
    await service.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_arch",
        knowledge_kind="architecture",
        content="Keys must be tenant-scoped",
        command_id="p11-arch",
        capability_id="cap_plan",
        confidence="high",
    )
    result = await service.reason_organizationally(
        tenant_id="t1",
        mode="pre_planning_brief",
        capability_id="cap_plan",
        question="What do we already know before planning?",
        objective="Must implement key rotation. Must verify audits.",
        required_knowledge_kinds=["architecture", "pattern"],
    )
    assert result["state_mutations"] == []
    assert result["silent_policy_change"] is False
    assert any(a["kind"] == "fact" for a in result["answers"])
    assert any(a["kind"] == "inference" and a["is_fact"] is False for a in result["answers"])
    assert any(a["kind"] == "recommendation" for a in result["answers"])
    assert any(o["action"] == "propose_plan_mutation" for o in result["options"])
    # EX5-S10 added G4 (capability_reuse_search) — must be a superset of
    # the original P11 scenarios, not equal to them.
    assert {"G1", "G2", "G3"} <= {s["id"] for s in GOLDEN_SCENARIOS}


@pytest.mark.asyncio
async def test_p11_reasoning_http_surface() -> None:
    service = KernelService()
    await service.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_http",
        knowledge_kind="lesson_learned",
        content="Prefer idempotent device registration before barcodes",
        command_id="p11-http",
        capability_id="cap_1",
        confidence="medium",
    )
    app = FastAPI()
    app.state.execution_kernel_service = service
    app.state.execution_kernel_require_gateway = False
    app.include_router(execution_kernel_router)
    headers = {"X-Tenant-ID": "t1"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        scenarios = await client.get("/aix/execution-kernel/reasoning/scenarios", headers=headers)
        assert scenarios.status_code == 200
        # EX5-S10 added G4 (capability_reuse_search) on top of P11's original 3.
        assert len(scenarios.json()["scenarios"]) == 4
        queried = await client.post(
            "/aix/execution-kernel/reasoning/query",
            headers=headers,
            json={
                "mode": "precedent_search",
                "question": "device registration before barcodes",
            },
        )
        assert queried.status_code == 200
        body = queried.json()["result"]
        assert body["state_mutations"] == []
        assert body["answers"]
