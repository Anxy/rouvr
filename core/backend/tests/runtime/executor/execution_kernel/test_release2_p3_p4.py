"""Release 2 charter: bounded P3 organizational knowledge and P4 gateway integration."""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter.model_gateway import resolve_kernel_model_routes
from backend.aix_core.runtime.executor.execution_kernel_adapter.postgres_service import PostgresKernelService
from backend.infrastructure.postgres.execution_kernel.repository import (
    ExecutionKernelRepositoryError,
    resolve_test_database_url,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService


def _routing_context(*, executor_health: str = "unhealthy") -> dict:
    return {
        "task_type": "code_change",
        "verifier_task_type": "verification",
        "risk_level": "medium",
        "model_policy": "allow_escalate",
        "retry_count": 0,
        "policies": [
            {
                "policy_key": "executor-policy",
                "task_type": "code_change",
                "risk_level": "medium",
                "model_policy": "allow_escalate",
                "default_provider": "local_exec",
                "escalation_provider": "cloud_exec",
                "max_retry_before_escalate": 0,
                "status": "active",
            },
            {
                "policy_key": "verifier-policy",
                "task_type": "verification",
                "risk_level": "medium",
                "model_policy": "allow_escalate",
                "default_provider": "cloud_verify",
                "escalation_provider": "local_verify",
                "max_retry_before_escalate": 0,
                "status": "active",
            },
        ],
        "bindings": [
            {
                "binding_key": "executor-primary",
                "provider": "local_exec",
                "model_id": "exec-primary",
                "model_name": "Executor Primary",
                "route_class": "primary",
                "is_enabled": True,
            },
            {
                "binding_key": "executor-fallback",
                "provider": "cloud_exec",
                "model_id": "exec-fallback",
                "model_name": "Executor Fallback",
                "route_class": "escalation",
                "is_enabled": True,
            },
            {
                "binding_key": "verifier-primary",
                "provider": "cloud_verify",
                "model_id": "verify-primary",
                "model_name": "Verifier Primary",
                "route_class": "primary",
                "is_enabled": True,
            },
        ],
        "health_rows": [
            {
                "provider": "local_exec",
                "model_id": "exec-primary",
                "health_status": executor_health,
            },
            {
                "provider": "cloud_exec",
                "model_id": "exec-fallback",
                "health_status": "healthy",
            },
            {
                "provider": "cloud_verify",
                "model_id": "verify-primary",
                "health_status": "healthy",
            },
        ],
    }


@pytest.mark.asyncio
async def test_p3_current_temporal_confidence_and_provenance_query() -> None:
    service = KernelService()
    await service.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_current",
        knowledge_kind="technical_knowledge",
        content="Use tenant-scoped keys",
        command_id="p3-current",
        capability_id="cap_1",
        project_id="proj_1",
        confidence="high",
    )
    await service.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_stale",
        knowledge_kind="technical_knowledge",
        content="Use global keys",
        command_id="p3-stale",
        capability_id="cap_1",
        project_id="proj_1",
        confidence="high",
        valid_until="2020-01-01T00:00:00Z",
    )
    await service.upsert_knowledge(
        tenant_id="t2",
        knowledge_entry_id="k_other_tenant",
        knowledge_kind="technical_knowledge",
        content="Tenant secret",
        command_id="p3-other",
        capability_id="cap_1",
        confidence="high",
    )
    await service.add_knowledge_graph_edge(
        tenant_id="t1",
        edge_type="DERIVED_FROM",
        from_type="knowledge",
        from_id="k_current",
        to_type="evidence",
        to_id="ev_1",
        command_id="p3-provenance",
    )

    results = await service.query_organizational_knowledge(
        tenant_id="t1", capability_id="cap_1", minimum_confidence="medium"
    )
    assert [result["knowledge_entry_id"] for result in results] == ["k_current"]
    assert results[0]["provenance_edges"][0]["to_id"] == "ev_1"


@pytest.mark.asyncio
async def test_p3_missing_knowledge_and_cross_project_reconciliation() -> None:
    service = KernelService()
    missing = await service.detect_missing_knowledge(
        tenant_id="t1",
        capability_id="cap_missing",
        required_kinds=["architecture", "pattern"],
    )
    assert missing["missing"] is True
    assert missing["missing_kinds"] == ["architecture", "pattern"]

    for project_id, knowledge_id in (("proj_a", "k_a"), ("proj_b", "k_b")):
        await service.upsert_knowledge(
            tenant_id="t1",
            knowledge_entry_id=knowledge_id,
            knowledge_kind="lesson_learned",
            content="Prefer idempotent registration",
            command_id=f"p3-reconcile-{knowledge_id}",
            capability_id="cap_1",
            project_id=project_id,
            confidence="medium",
        )
    groups = await service.reconcile_cross_project_knowledge(tenant_id="t1")
    assert len(groups) == 1
    assert groups[0]["project_ids"] == ["proj_a", "proj_b"]


@pytest.mark.asyncio
async def test_p3_conflict_resolution_retains_edge_and_supersedes_loser() -> None:
    service = KernelService()
    for knowledge_id, content in (("k_old", "Use global keys"), ("k_new", "Use tenant keys")):
        await service.upsert_knowledge(
            tenant_id="t1",
            knowledge_entry_id=knowledge_id,
            knowledge_kind="architecture",
            content=content,
            command_id=f"p3-conflict-{knowledge_id}",
            capability_id="cap_1",
            confidence="high",
        )
    conflict = await service.declare_knowledge_conflict(
        tenant_id="t1",
        left_knowledge_id="k_old",
        right_knowledge_id="k_new",
        command_id="p3-conflict-edge",
        authority_context="architecture-review",
    )
    resolution = await service.resolve_knowledge_conflict(
        tenant_id="t1",
        conflict_edge_id=conflict["edge_id"],
        authoritative_knowledge_id="k_new",
        superseded_knowledge_id="k_old",
        command_id="p3-resolution",
    )
    assert resolution["conflict"]["status"] == "SUPERSEDED"
    retained = await service.get_knowledge_graph_edges(tenant_id="t1", active_only=False)
    assert any(
        edge["edge_id"] == conflict["edge_id"] and edge["status"] == "SUPERSEDED"
        for edge in retained
    )
    current = await service.query_organizational_knowledge(
        tenant_id="t1", capability_id="cap_1"
    )
    assert [entry["knowledge_entry_id"] for entry in current] == ["k_new"]


@pytest.mark.asyncio
async def test_p3_postgres_freshness_provenance_and_conflict_when_url_configured() -> None:
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")
    service = PostgresKernelService(database_url=url)
    await service.ensure_schema()
    suffix = uuid4().hex[:10]
    current_id = f"k_current_{suffix}"
    old_id = f"k_old_{suffix}"
    for knowledge_id, content in (
        (current_id, "Use tenant-scoped keys"),
        (old_id, "Use global keys"),
    ):
        await service.upsert_knowledge(
            tenant_id="t_pg_p3",
            knowledge_entry_id=knowledge_id,
            knowledge_kind="architecture",
            content=content,
            command_id=f"p3-pg-create-{knowledge_id}",
            capability_id="cap_pg_p3",
            project_id="proj_pg_p3",
            confidence="high",
        )
    await service.add_knowledge_graph_edge(
        tenant_id="t_pg_p3",
        edge_type="DERIVED_FROM",
        from_type="knowledge",
        from_id=current_id,
        to_type="evidence",
        to_id=f"ev_{suffix}",
        command_id=f"p3-pg-provenance-{suffix}",
    )
    conflict = await service.declare_knowledge_conflict(
        tenant_id="t_pg_p3",
        left_knowledge_id=old_id,
        right_knowledge_id=current_id,
        command_id=f"p3-pg-conflict-{suffix}",
    )
    await service.resolve_knowledge_conflict(
        tenant_id="t_pg_p3",
        conflict_edge_id=conflict["edge_id"],
        authoritative_knowledge_id=current_id,
        superseded_knowledge_id=old_id,
        command_id=f"p3-pg-resolution-{suffix}",
    )
    results = await service.query_organizational_knowledge(
        tenant_id="t_pg_p3", capability_id="cap_pg_p3", minimum_confidence="high"
    )
    ids = {result["knowledge_entry_id"] for result in results}
    assert current_id in ids and old_id not in ids
    current = next(result for result in results if result["knowledge_entry_id"] == current_id)
    assert any(edge["to_id"] == f"ev_{suffix}" for edge in current["provenance_edges"])


def test_p4_deterministic_primary_and_unhealthy_fallback() -> None:
    primary = resolve_kernel_model_routes(
        routing_context=_routing_context(executor_health="healthy")
    )
    assert primary["executor"]["decision_status"] == "selected"
    assert primary["executor"]["selected_model_id"] == "exec-primary"

    fallback = resolve_kernel_model_routes(
        routing_context=_routing_context(),
        include_verifier=True,
        require_independent_verifier=True,
    )
    assert fallback["executor"]["decision_status"] == "escalated"
    assert fallback["executor"]["selected_model_id"] == "exec-fallback"
    assert fallback["verifier"]["selected_model_id"] == "verify-primary"


def test_p4_blocked_route_fails_closed() -> None:
    context = _routing_context()
    context["health_rows"] = []
    with pytest.raises(ExecutionKernelRepositoryError, match="model gateway blocked executor"):
        resolve_kernel_model_routes(routing_context=context)


@pytest.mark.asyncio
async def test_p4_kernel_prepare_dispatch_carries_gateway_decisions() -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from backend.routers.execution_kernel_router import router as execution_kernel_router

    service = KernelService()
    for aggregate_type, aggregate_id, command_id, payload in (
        ("project", "proj_1", "p4-project", {"status": "ACTIVE"}),
        (
            "capability",
            "cap_1",
            "p4-capability",
            {"status": "ACTIVE", "parent_id": "proj_1"},
        ),
        (
            "problem",
            "pr_1",
            "p4-problem",
            {"status": "EXECUTING", "parent_id": "cap_1"},
        ),
        (
            "work_item",
            "wi_1",
            "p4-work-item",
            {"status": "PENDING", "parent_id": "pr_1", "title": "Route me"},
        ),
    ):
        await service.append_and_project(
            tenant_id="t1",
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            expected_sequence_number=1,
            event_type=f"{aggregate_type.upper()}_CREATED",
            command_id=command_id,
            payload=payload,
        )
    _capsule, dispatch = await service.prepare_dispatch(
        tenant_id="t1",
        work_item_id="wi_1",
        model_routing=_routing_context(),
        include_verifier_route=True,
        require_independent_verifier=True,
    )
    assert dispatch.model_routes["executor"]["selected_model_id"] == "exec-fallback"
    assert dispatch.model_routes["verifier"]["selected_model_id"] == "verify-primary"

    app = FastAPI()
    app.state.execution_kernel_service = service
    app.state.execution_kernel_require_gateway = False
    app.include_router(execution_kernel_router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/aix/execution-kernel/dispatch",
            headers={"X-Tenant-ID": "t1"},
            json={
                "work_item_id": "wi_1",
                "model_routing": _routing_context(),
                "include_verifier_route": True,
                "require_independent_verifier": True,
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["dispatch"]["model_routes"]["executor"]["selected_model_id"] == "exec-fallback"


@pytest.mark.asyncio
async def test_p3_organizational_knowledge_http_surface() -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from backend.routers.execution_kernel_router import router as execution_kernel_router

    service = KernelService()
    await service.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_http",
        knowledge_kind="architecture",
        content="Prefer tenant-scoped keys",
        command_id="p3-http-1",
        capability_id="cap_1",
        project_id="proj_1",
        confidence="high",
    )
    app = FastAPI()
    app.state.execution_kernel_service = service
    app.state.execution_kernel_require_gateway = False
    app.include_router(execution_kernel_router)
    headers = {"X-Tenant-ID": "t1"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        preserved_confidence = await client.post(
            "/aix/execution-kernel/knowledge",
            headers=headers,
            json={
                "knowledge_entry_id": "k_http",
                "knowledge_kind": "architecture",
                "content": "Prefer tenant-scoped keys after review",
                "command_id": "p3-http-preserve-confidence",
                "capability_id": "cap_1",
                "project_id": "proj_1",
            },
        )
        assert preserved_confidence.status_code == 200
        assert preserved_confidence.json()["entry"]["confidence"] == "high"
        stale_write = await client.post(
            "/aix/execution-kernel/knowledge",
            headers=headers,
            json={
                "knowledge_entry_id": "k_http_stale",
                "knowledge_kind": "architecture",
                "content": "Expired guidance",
                "command_id": "p3-http-stale",
                "capability_id": "cap_1",
                "confidence": "high",
                "valid_until": "2020-01-01T00:00:00Z",
            },
        )
        assert stale_write.status_code == 200
        listed = await client.get(
            "/aix/execution-kernel/organizational-knowledge",
            headers=headers,
            params={"capability_id": "cap_1", "minimum_confidence": "medium"},
        )
        assert listed.status_code == 200
        assert listed.json()["count"] == 1
        missing = await client.get(
            "/aix/execution-kernel/organizational-knowledge/missing",
            headers=headers,
            params={"capability_id": "cap_empty", "required_kinds": "pattern"},
        )
        assert missing.status_code == 200
        assert missing.json()["missing"] is True
        conflict = await client.post(
            "/aix/execution-kernel/knowledge/conflicts",
            headers=headers,
            json={
                "left_knowledge_id": "k_http",
                "right_knowledge_id": "k_http",
                "command_id": "p3-http-conflict",
            },
        )
        assert conflict.status_code == 400
