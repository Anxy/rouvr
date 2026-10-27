"""EX5-S11: Production Hardening.

Charter theme: "Chaos, soak, DR, migration/rollback certification
package." Honest scoping, decided before writing any code:

- SOAK (sustained real elapsed time, hours) cannot be honestly produced in
  a single session/turn. Not attempted, not faked.
- A destructive backup/restore DR drill against the shared local dev
  Postgres this whole session has used for real test data would risk
  other legitimate data — the same reasoning that declined this back in
  the original DRIFT scoping pass (DRIFT-029). Not attempted here either.
- What IS genuinely achievable and valuable: a real CHAOS test — actually
  interrupt a real multi-step operation partway through against live
  Postgres, and prove the system fails *safely* (no corruption, no silent
  duplication, no lost tenant boundary) even though it does not
  automatically self-heal — and a migration ROLLBACK proof for the RLS
  change (EX5-S8), the newest schema change, since reversibility matters
  for any real production change.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter.bootstrap import BootstrapSpec, BootstrapWorkItem, bootstrap_problem_graph
from backend.aix_core.runtime.executor.execution_kernel_adapter.postgres_service import PostgresKernelService
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError, resolve_test_database_url


@pytest.mark.asyncio
async def test_chaos_crash_mid_bootstrap_leaves_safe_not_corrupted_partial_state() -> None:
    """bootstrap_problem_graph makes multiple separate DB round-trips
    (project -> capability -> problem -> work items), each its own
    transaction — there is no single top-level transaction spanning all
    of them. A real process crash between steps is a real, not
    theoretical, risk. This proves what actually happens: the already-
    written steps are durable and correct (no corruption), the crash
    point is exactly where expected (no partial/inconsistent aggregate),
    and — the actual finding — retrying naively does NOT self-heal; it
    fails closed with a clear, actionable error, not a duplicate or a
    silent no-op."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    suffix = uuid4().hex[:12]
    tenant_id = f"t_chaos_{suffix}"
    project_id, capability_id, problem_id = f"proj_{suffix}", f"cap_{suffix}", f"pr_{suffix}"

    service_a = PostgresKernelService(database_url=url)
    await service_a.ensure_schema()

    real_append = service_a.append_and_project
    call_count = {"n": 0}

    async def _crash_after_project(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:  # PROJECT_CREATED succeeds (call 1); crash before CAPABILITY_CREATED
            raise ConnectionError("simulated process kill mid-bootstrap")
        return await real_append(**kwargs)

    service_a.append_and_project = _crash_after_project  # type: ignore[method-assign]

    spec = BootstrapSpec(
        vision_summary="V", requirements_summary="R", capability_name="C", charter_summary="Ch",
        work_items=[BootstrapWorkItem(title="WI", work_item_id=f"wi_{suffix}")],
        project_id=project_id, capability_id=capability_id, problem_id=problem_id,
    )
    with pytest.raises(ConnectionError, match="simulated process kill"):
        await bootstrap_problem_graph(service_a, tenant_id=tenant_id, spec=spec, idempotency_key=f"boot_{suffix}")
    del service_a  # the "crash" — no shared state survives

    # "Restart": a completely fresh process/instance against the same DB.
    service_b = PostgresKernelService(database_url=url)

    project = await service_b.get_projection(tenant_id=tenant_id, aggregate_type="project", aggregate_id=project_id)
    capability = await service_b.get_projection(tenant_id=tenant_id, aggregate_type="capability", aggregate_id=capability_id)
    problem = await service_b.get_projection(tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id)

    assert project is not None and project["status"] == "ACTIVE", (
        "the step that completed before the crash must be durable and correct"
    )
    assert capability is None, "the step that crashed mid-flight must not exist as a corrupted/partial row"
    assert problem is None, "steps after the crash point must never have been attempted"

    # The actual finding: does a naive retry self-heal, or does it need a
    # human/caller to notice and recover deliberately?
    with pytest.raises(ExecutionKernelRepositoryError, match="already exists"):
        await bootstrap_problem_graph(service_b, tenant_id=tenant_id, spec=spec, idempotency_key=f"boot_{suffix}")
    # This is the honest finding, recorded in the task packet: bootstrap
    # is NOT automatically resumable. A caller must detect the partial
    # state and either clean it up or use a different recovery path — this
    # is a real, stated gap, not silently papered over.


@pytest.mark.asyncio
async def test_rls_migration_is_cleanly_reversible() -> None:
    """Migration/rollback proof for EX5-S8's schema change — a real
    production change must be provably reversible, not a one-way door."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    import asyncpg  # type: ignore[import-untyped]

    from backend.infrastructure.postgres.execution_kernel.ledger_schema import EXECUTION_EVENTS_TABLE

    conn = await asyncpg.connect(url)
    try:
        before = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = $1", EXECUTION_EVENTS_TABLE
        )
        assert before["relrowsecurity"] is True

        # Rollback.
        await conn.execute(f"DROP POLICY IF EXISTS {EXECUTION_EVENTS_TABLE}_tenant_isolation ON {EXECUTION_EVENTS_TABLE}")
        await conn.execute(f"ALTER TABLE {EXECUTION_EVENTS_TABLE} NO FORCE ROW LEVEL SECURITY")
        await conn.execute(f"ALTER TABLE {EXECUTION_EVENTS_TABLE} DISABLE ROW LEVEL SECURITY")

        after_rollback = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = $1", EXECUTION_EVENTS_TABLE
        )
        assert after_rollback["relrowsecurity"] is False
        assert after_rollback["relforcerowsecurity"] is False

        # Re-apply (this is what ensure_schema() does on every real
        # startup) — must be idempotent and restore the exact same state.
        from backend.infrastructure.postgres.execution_kernel.ledger_schema import ensure_execution_kernel_schema

        await ensure_execution_kernel_schema(conn)
        after_reapply = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = $1", EXECUTION_EVENTS_TABLE
        )
        assert after_reapply["relrowsecurity"] is True
        assert after_reapply["relforcerowsecurity"] is True
    finally:
        await conn.close()
