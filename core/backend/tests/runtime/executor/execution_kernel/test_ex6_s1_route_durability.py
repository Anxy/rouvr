"""AIX-EXECUTION-006-S1: Model Route Durability.

The prior sprint (EX5-S2) wired execution-route binding but backed it with
an in-memory store even under PostgresKernelService — a fact discovered by
an independent zero-trust audit via a live black-box HTTP restart test, not
by reading the code. This test proves the fix: a genuinely fresh
PostgresKernelService instance (no shared Python state) rehydrates the
active route, its full switch history, and its audit trail from Postgres
alone.

Uses a random suffix per run so this file is safely re-runnable against a
persistent database.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter.postgres_service import PostgresKernelService
from backend.infrastructure.postgres.execution_kernel.repository import resolve_test_database_url


@pytest.mark.asyncio
async def test_execution_route_survives_a_simulated_process_restart() -> None:
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    suffix = uuid4().hex[:12]
    tenant_id = f"t_route_restart_{suffix}"
    wi_id = f"wi_{suffix}"

    # --- "Process A": bind a route, switch it, then die. ---
    service_a = PostgresKernelService(database_url=url)
    await service_a.ensure_schema()

    bound = await service_a.switch_execution_route(
        tenant_id=tenant_id,
        work_item_id=wi_id,
        model_routes={
            "executor": {"provider": "anthropic", "model_id": "claude-sonnet-5"},
            "verifier": {"provider": "openai", "model_id": "gpt-5"},
        },
        reason="initial route assignment",
    )
    switched = await service_a.switch_execution_route(
        tenant_id=tenant_id,
        work_item_id=wi_id,
        model_routes={
            "executor": {"provider": "google", "model_id": "gemini-2.5-pro"},
            "verifier": {"provider": "openai", "model_id": "gpt-5"},
        },
        reason="switching executor mid-flight",
    )
    assert switched["executor"]["model_id"] == "gemini-2.5-pro"
    del service_a

    # --- "Process B": a completely fresh instance, no shared Python state. ---
    service_b = PostgresKernelService(database_url=url)

    active = await service_b.get_execution_route(tenant_id=tenant_id, work_item_id=wi_id)
    assert active is not None, "active route must survive restart"
    assert active["route_id"] == switched["route_id"]
    assert active["executor"]["model_id"] == "gemini-2.5-pro"
    assert active["verifier"]["model_id"] == "gpt-5"

    history = await service_b.list_execution_route_history(tenant_id=tenant_id, work_item_id=wi_id)
    assert len(history) == 2, "both bind and switch must be durable, not just the latest"
    route_ids = {h["route_id"] for h in history}
    assert bound["route_id"] in route_ids
    assert switched["route_id"] in route_ids
    superseded = next(h for h in history if h["route_id"] == bound["route_id"])
    assert superseded["status"] == "SUPERSEDED"

    audit = await service_b.list_execution_route_audit(tenant_id=tenant_id, work_item_id=wi_id)
    event_types = [a["event_type"] for a in audit]
    assert "ROUTE_BOUND" in event_types
    assert "ROUTE_SUPERSEDED" in event_types
    assert "ROUTE_SWITCHED" in event_types


@pytest.mark.asyncio
async def test_execution_route_tenant_isolated_after_restart() -> None:
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    suffix = uuid4().hex[:12]
    tenant_a, tenant_b = f"t_route_a_{suffix}", f"t_route_b_{suffix}"
    wi_id = f"wi_{suffix}"

    service_a = PostgresKernelService(database_url=url)
    await service_a.ensure_schema()
    await service_a.switch_execution_route(
        tenant_id=tenant_a,
        work_item_id=wi_id,
        model_routes={"executor": {"provider": "anthropic", "model_id": "claude-sonnet-5"}},
        reason="tenant a route",
    )
    del service_a

    service_b = PostgresKernelService(database_url=url)
    cross_tenant_read = await service_b.get_execution_route(tenant_id=tenant_b, work_item_id=wi_id)
    assert cross_tenant_read is None, "a different tenant must never see tenant A's route after restart"
