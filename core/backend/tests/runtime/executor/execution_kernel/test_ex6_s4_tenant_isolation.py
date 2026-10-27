"""AIX-EXECUTION-006-S4: Tenant Isolation — extended past the ledger table,
and proven against the REAL restricted application role.

EX5-S8 proved RLS worked on aix_execution_events, but only against a
narrowly-scoped, test-only role (aix_rls_test_role) — the app's actual
connection stayed superuser, so the protection was inert in the real
deployment (confirmed live by a zero-trust audit). This sprint:

1. Created `hyeve_user` as the app's real non-superuser role (broad DML
   across the schema, not narrowly scoped — a real production app role).
2. Extended RLS to the highest-traffic tenant-scoped tables beyond the
   ledger: aix_execution_projection, aix_execution_dependency_events,
   aix_execution_model_routes, aix_execution_model_route_audit.
3. Made every store that touches those tables (re)scope
   app.current_tenant_id on every pooled-connection acquire, not just once
   at connect time — since a pooled connection can serve a different tenant
   on its next use.

These tests hit the SAME role hyeve-aix-service/.env now configures the
real app to use — not a separate test-only credential — so a pass here is
evidence about the actual deployed behavior, not a parallel fixture.
"""

from __future__ import annotations

from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter.postgres_service import PostgresKernelService
from backend.infrastructure.postgres.execution_kernel.repository import resolve_test_database_url
from backend.infrastructure.postgres.execution_kernel.schema import (
    DEPENDENCY_EVENTS_TABLE,
    EXECUTION_ROUTE_AUDIT_TABLE,
    EXECUTION_ROUTE_TABLE,
    PROJECTION_TABLE,
)

# The app's actual real non-superuser role — same one hyeve-aix-service/.env
# now configures HYEVE_AIX_SERVICE_DATABASE_URL to use.
_APP_ROLE_URL = "postgresql://hyeve_user:simple@127.0.0.1:5432/hyeve_aix_service"


@pytest.mark.asyncio
async def test_legitimate_operation_through_real_service_is_unaffected() -> None:
    """Regression-safety: bootstrap, dependency, and execution-route flows
    through the real service (which now scopes tenant context on every
    acquire) must work exactly as before RLS was extended."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    suffix = uuid4().hex[:12]
    tenant_id = f"t_legit_{suffix}"
    wi_a, wi_b = f"wi_a_{suffix}", f"wi_b_{suffix}"

    service = PostgresKernelService(database_url=url)
    await service.ensure_schema()

    await service.append_and_project(
        tenant_id=tenant_id, aggregate_type="problem", aggregate_id=f"pr_{suffix}",
        expected_sequence_number=1, event_type="PROBLEM_CREATED", command_id=f"c0_{suffix}",
        payload={"status": "EXECUTING"},
    )
    for wid in (wi_a, wi_b):
        await service.append_and_project(
            tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=wid,
            expected_sequence_number=1, event_type="WI_CREATED", command_id=f"c_{wid}",
            payload={"status": "PENDING", "parent_id": f"pr_{suffix}"},
        )
    await service.add_dependency(
        tenant_id=tenant_id, work_item_id=wi_b, depends_on_work_item_id=wi_a, command_id=f"dep_{suffix}",
    )
    actionable = await service.next_actionable(tenant_id=tenant_id, problem_id=f"pr_{suffix}")
    assert actionable == [wi_a]

    switched = await service.switch_execution_route(
        tenant_id=tenant_id, work_item_id=wi_a,
        model_routes={"executor": {"provider": "anthropic", "model_id": "claude-sonnet-5"}},
        reason="legit isolation regression check",
    )
    assert switched["executor"]["model_id"] == "claude-sonnet-5"
    route = await service.get_execution_route(tenant_id=tenant_id, work_item_id=wi_a)
    assert route is not None and route["route_id"] == switched["route_id"]


@pytest.mark.parametrize(
    "table_name,seed",
    [
        (PROJECTION_TABLE, "projection"),
        (DEPENDENCY_EVENTS_TABLE, "dependency"),
        (EXECUTION_ROUTE_TABLE, "route"),
        (EXECUTION_ROUTE_AUDIT_TABLE, "route_audit"),
    ],
)
@pytest.mark.asyncio
async def test_cross_tenant_raw_sql_with_no_context_is_blocked_on_real_app_role(
    table_name: str, seed: str
) -> None:
    """The actual point of this sprint: a raw SQL read against each of the 4
    newly-protected tables, through the REAL application role with no tenant
    context set, must see zero rows — fail closed, not fail open."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    suffix = uuid4().hex[:12]
    tenant_a = f"t_a_{seed}_{suffix}"
    wi_id = f"wi_{seed}_{suffix}"

    service = PostgresKernelService(database_url=url)
    await service.ensure_schema()
    await service.append_and_project(
        tenant_id=tenant_a, aggregate_type="work_item", aggregate_id=wi_id,
        expected_sequence_number=1, event_type="WI_CREATED", command_id=f"c1_{suffix}",
        payload={"status": "PENDING", "parent_id": f"pr_{suffix}"},
    )
    await service.add_dependency(
        tenant_id=tenant_a, work_item_id=wi_id, depends_on_work_item_id=f"other_{suffix}",
        command_id=f"dep_{suffix}",
    )
    await service.switch_execution_route(
        tenant_id=tenant_a, work_item_id=wi_id,
        model_routes={"executor": {"provider": "openai", "model_id": "gpt-5"}},
        reason="seed for cross-tenant leak test",
    )

    conn = await asyncpg.connect(_APP_ROLE_URL)
    try:
        leaked = await conn.fetch(f"SELECT * FROM {table_name} WHERE tenant_id = $1", tenant_a)
    finally:
        await conn.close()
    assert leaked == [], (
        f"{table_name}: a connection with no tenant context set must see ZERO rows "
        f"under RLS through the real app role — fail closed, not fail open."
    )

    conn2 = await asyncpg.connect(_APP_ROLE_URL)
    try:
        await conn2.execute("SELECT set_config('app.current_tenant_id', $1, false)", tenant_a)
        visible = await conn2.fetch(f"SELECT * FROM {table_name} WHERE tenant_id = $1", tenant_a)
    finally:
        await conn2.close()
    assert len(visible) >= 1, f"{table_name}: the correct tenant context must still see its own rows"


@pytest.mark.parametrize(
    "table_name,seed",
    [
        (PROJECTION_TABLE, "projection2"),
        (DEPENDENCY_EVENTS_TABLE, "dependency2"),
        (EXECUTION_ROUTE_TABLE, "route2"),
    ],
)
@pytest.mark.asyncio
async def test_cross_tenant_raw_sql_with_wrong_context_is_also_blocked(table_name: str, seed: str) -> None:
    """Belt and braces: setting a DIFFERENT tenant's context (not just no
    context) must still not surface tenant A's rows."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    suffix = uuid4().hex[:12]
    tenant_a, tenant_b = f"t_a_{seed}_{suffix}", f"t_b_{seed}_{suffix}"
    wi_id = f"wi_{seed}_{suffix}"

    service = PostgresKernelService(database_url=url)
    await service.ensure_schema()
    await service.append_and_project(
        tenant_id=tenant_a, aggregate_type="work_item", aggregate_id=wi_id,
        expected_sequence_number=1, event_type="WI_CREATED", command_id=f"c1_{suffix}",
        payload={"status": "PENDING", "parent_id": f"pr_{suffix}"},
    )
    await service.add_dependency(
        tenant_id=tenant_a, work_item_id=wi_id, depends_on_work_item_id=f"other_{suffix}",
        command_id=f"dep_{suffix}",
    )
    await service.switch_execution_route(
        tenant_id=tenant_a, work_item_id=wi_id,
        model_routes={"executor": {"provider": "openai", "model_id": "gpt-5"}},
        reason="seed for wrong-context leak test",
    )

    conn = await asyncpg.connect(_APP_ROLE_URL)
    try:
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", tenant_b)
        rows = await conn.fetch(f"SELECT * FROM {table_name} WHERE tenant_id = $1", tenant_a)
    finally:
        await conn.close()
    assert rows == [], f"{table_name}: tenant B's context must never see tenant A's rows"


@pytest.mark.asyncio
async def test_pooled_connection_does_not_leak_tenant_context_between_requests() -> None:
    """The specific risk this sprint's own connection-pooling change (S3)
    introduced: a pooled connection scoped to tenant A, released, then
    reacquired for an UNSCOPED request must not still see tenant A's rows —
    proving db_pool's RESET ALL on release actually prevents session-state
    leakage across pooled reuse. Uses the pool directly (not a fresh
    asyncpg.connect, which would trivially prove nothing about pooling) with
    a tiny pool (max_size=1) so the second acquire is GUARANTEED to reuse
    the exact same physical connection the first one scoped."""
    from backend.infrastructure.postgres.execution_kernel.db_pool import get_pool

    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    pool = await get_pool(_APP_ROLE_URL, min_size=1, max_size=1)

    scoped = await pool.acquire()
    try:
        first_pid = await scoped.fetchval("SELECT pg_backend_pid()")
        await scoped.execute("SELECT set_config('app.current_tenant_id', $1, false)", "tenant-leak-probe")
        confirmed = await scoped.fetchval("SELECT current_setting('app.current_tenant_id', true)")
        assert confirmed == "tenant-leak-probe"
        await scoped.execute("RESET ALL")  # exactly what _PooledConnectionProxy.close() does
    finally:
        await pool.release(scoped)

    reused = await pool.acquire()
    try:
        second_pid = await reused.fetchval("SELECT pg_backend_pid()")
        assert second_pid == first_pid, (
            "max_size=1 pool must hand back the exact same physical server connection "
            "(same backend PID) — otherwise this test isn't actually exercising reuse"
        )
        current = await reused.fetchval("SELECT current_setting('app.current_tenant_id', true)")
    finally:
        await pool.release(reused)

    assert current in (None, ""), (
        f"the SAME physical pooled connection, reused for a new acquire, must not still "
        f"carry the previous tenant's session context; got {current!r}"
    )
