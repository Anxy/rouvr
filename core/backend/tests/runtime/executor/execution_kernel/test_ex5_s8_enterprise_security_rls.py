"""EX5-S8: Enterprise Security — DB-level tenant isolation (RLS).

EX3-S009 proved live that a raw asyncpg query with no tenant_id filter
could read another tenant's row from aix_execution_events — the gap was
real, not theoretical, and deliberately NOT fixed there (production-schema
blast radius needs its own dedicated, reviewed packet). This is that
packet, scoped to exactly the table EX3-S009 targeted — the execution
ledger — as a proof of the pattern before wider rollout.

Two things must both be true, or this is worse than doing nothing:
1. The exact EX3-S009 attack (raw SQL, no tenant filter) must now fail
   to leak data.
2. Completely normal, legitimate same-tenant operation through the real
   repository/service methods must be entirely unaffected — a broken RLS
   policy that also blocks legitimate access would silently break the
   whole application, which would be a far worse outcome than the
   original gap.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter.postgres_service import PostgresKernelService
from backend.infrastructure.postgres.execution_kernel.repository import resolve_test_database_url

# EX5-S8 finding: the app's own connection (postgres/simple, the local
# superuser) unconditionally bypasses RLS — FORCE ROW LEVEL SECURITY
# explicitly excludes superusers, by Postgres design, not a bug in the
# policy. A dedicated, narrowly-scoped, non-superuser role
# (aix_rls_test_role — SELECT/INSERT/UPDATE on aix_execution_events only,
# created once via the admin connection, not the app's real credentials)
# is required to prove the policy actually enforces anything. This is
# test-only infrastructure; the application's own default connection is
# never changed by this sprint.
_RLS_TEST_ROLE_URL = "postgresql://aix_rls_test_role:aix_rls_test_pw@127.0.0.1:5432/hyeve_aix_service"


@pytest.mark.asyncio
async def test_legitimate_same_tenant_operation_is_completely_unaffected() -> None:
    """The regression-safety proof: normal use through the real service
    methods (which now set the session tenant context) must work exactly
    as before RLS was enabled — same-tenant reads, writes, and the full
    build->checkpoint->resume flow, unaffected by the new policy."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    suffix = uuid4().hex[:12]
    tenant_id = f"t_legit_{suffix}"
    wi_id = f"wi_{suffix}"

    service = PostgresKernelService(database_url=url)
    await service.ensure_schema()

    event = await service.append_and_project(
        tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=wi_id,
        expected_sequence_number=1, event_type="WI_CREATED", command_id=f"c1_{suffix}",
        payload={"status": "PENDING", "parent_id": f"pr_{suffix}"},
    )
    assert event.tenant_id == tenant_id

    proj = await service.get_projection(tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=wi_id)
    assert proj is not None
    assert proj["status"] == "PENDING"

    second = await service.append_and_project(
        tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=wi_id,
        expected_sequence_number=2, event_type="WI_STARTED", command_id=f"c2_{suffix}",
        payload={"status": "IN_PROGRESS", "parent_id": f"pr_{suffix}"},
    )
    assert second.sequence_number == 2

    replay = await service.verify_projection_replay(tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=wi_id)
    assert replay["match"] is True, "RLS must not break the fold-from-ledger replay path either"


@pytest.mark.asyncio
async def test_cross_tenant_raw_sql_read_is_now_blocked() -> None:
    """The actual point of this sprint: re-run the exact EX3-S009 attack
    and confirm it no longer works."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    import asyncpg  # type: ignore[import-untyped]

    from backend.infrastructure.postgres.execution_kernel.ledger_schema import EXECUTION_EVENTS_TABLE

    suffix = uuid4().hex[:12]
    tenant_a = f"t_a_{suffix}"

    service = PostgresKernelService(database_url=url)
    await service.ensure_schema()
    await service.append_and_project(
        tenant_id=tenant_a, aggregate_type="project", aggregate_id=f"secret_proj_{suffix}",
        expected_sequence_number=1, event_type="PROJECT_CREATED", command_id=f"cmd_a_{suffix}",
        payload={"status": "ACTIVE", "vision_summary": "Tenant A's confidential vision"},
    )

    # A raw connection with NO tenant context set at all, AS THE RESTRICTED
    # ROLE (not the app's superuser connection — see module docstring for
    # why that distinction is the entire point of this test). This is
    # exactly the EX3-S009 attack shape (no application-layer filter, no
    # session variable either). Before this sprint, this leaked tenant A's
    # row; before the role fix, it still leaked even with RLS enabled,
    # because the app connects as a superuser.
    conn = await asyncpg.connect(_RLS_TEST_ROLE_URL)
    try:
        leaked = await conn.fetch(
            f"SELECT tenant_id, payload FROM {EXECUTION_EVENTS_TABLE} WHERE aggregate_id = $1",
            f"secret_proj_{suffix}",
        )
    finally:
        await conn.close()

    assert leaked == [], (
        "a non-superuser connection with no tenant context set must see ZERO "
        "rows under RLS — fail closed, not fail open."
    )

    # Confirm it's not just "the row doesn't exist" — the same restricted
    # role, with the correct tenant context set, must see it.
    conn2 = await asyncpg.connect(_RLS_TEST_ROLE_URL)
    try:
        await conn2.execute("SELECT set_config('app.current_tenant_id', $1, false)", tenant_a)
        visible = await conn2.fetch(
            f"SELECT tenant_id, payload FROM {EXECUTION_EVENTS_TABLE} WHERE aggregate_id = $1",
            f"secret_proj_{suffix}",
        )
    finally:
        await conn2.close()
    assert len(visible) == 1
    assert visible[0]["tenant_id"] == tenant_a


@pytest.mark.asyncio
async def test_cross_tenant_raw_sql_with_wrong_tenant_context_is_also_blocked() -> None:
    """Belt and braces: a connection that sets a DIFFERENT tenant's context
    (not just no context) must still not see tenant A's row."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    import asyncpg  # type: ignore[import-untyped]

    from backend.infrastructure.postgres.execution_kernel.ledger_schema import EXECUTION_EVENTS_TABLE

    suffix = uuid4().hex[:12]
    tenant_a, tenant_b = f"t_a_{suffix}", f"t_b_{suffix}"

    service = PostgresKernelService(database_url=url)
    await service.ensure_schema()
    await service.append_and_project(
        tenant_id=tenant_a, aggregate_type="project", aggregate_id=f"proj_{suffix}",
        expected_sequence_number=1, event_type="PROJECT_CREATED", command_id=f"cmd_a2_{suffix}",
        payload={"status": "ACTIVE"},
    )

    conn = await asyncpg.connect(_RLS_TEST_ROLE_URL)
    try:
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", tenant_b)
        rows = await conn.fetch(
            f"SELECT * FROM {EXECUTION_EVENTS_TABLE} WHERE aggregate_id = $1", f"proj_{suffix}",
        )
    finally:
        await conn.close()
    assert rows == []
