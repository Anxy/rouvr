"""AIX-EXECUTION-006-S3: Postgres Scheduler Performance — proven against REAL Postgres.

A zero-trust audit found EX5-S9's O(N^2) fix was applied only to the
in-memory test-double classes (InMemoryDependencyGraph/InMemoryProjectionStore)
and never to PostgresDependencyGraph.next_actionable_work_items — the class
production actually calls. The 100/1000/5000-item scale claim had therefore
never been tested against real Postgres at all, only in-memory.

This test proves next_actionable_work_items() at 100/1000/5000 Work Items
against a real Postgres database, using the batched
batch_active_dependencies()/get_many() queries added in this sprint (three
queries total regardless of N, replacing the old K+K·D pattern), and that
timing stays roughly flat rather than growing with N.

Uses a random suffix per run so this file is safely re-runnable against a
persistent database.
"""

from __future__ import annotations

import time
from uuid import uuid4

import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter.postgres_service import PostgresKernelService
from backend.infrastructure.postgres.execution_kernel.repository import resolve_test_database_url

SCALES = (100, 1000, 5000)
MAX_SECONDS_PER_QUERY = 2.0


async def _build_chain(svc: PostgresKernelService, *, tenant_id: str, problem_id: str, n: int, suffix: str) -> None:
    await svc.append_and_project(
        tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id,
        expected_sequence_number=1, event_type="PROBLEM_CREATED", command_id=f"pc_{suffix}",
        payload={"status": "EXECUTING"},
    )
    for i in range(n):
        await svc.append_and_project(
            tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=f"wi_{suffix}_{i}",
            expected_sequence_number=1, event_type="WI_CREATED", command_id=f"wic_{suffix}_{i}",
            payload={"status": "PENDING", "parent_id": problem_id},
        )
        if i > 0:
            await svc.add_dependency(
                tenant_id=tenant_id, work_item_id=f"wi_{suffix}_{i}",
                depends_on_work_item_id=f"wi_{suffix}_{i - 1}", command_id=f"dep_{suffix}_{i}",
            )


@pytest.mark.asyncio
async def test_next_actionable_correctness_and_timing_at_scale_against_real_postgres() -> None:
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    results: list[tuple[int, float, float]] = []
    for n in SCALES:
        suffix = uuid4().hex[:10]
        svc = PostgresKernelService(database_url=url)
        await svc.ensure_schema()
        tenant_id = f"t_scale_{suffix}"
        problem_id = f"pr_{suffix}"

        t0 = time.perf_counter()
        await _build_chain(svc, tenant_id=tenant_id, problem_id=problem_id, n=n, suffix=suffix)
        build_seconds = time.perf_counter() - t0

        t1 = time.perf_counter()
        actionable = await svc.next_actionable(tenant_id=tenant_id, problem_id=problem_id)
        query_seconds = time.perf_counter() - t1

        assert actionable == [f"wi_{suffix}_0"], f"correctness must hold at N={n} against real Postgres"
        results.append((n, build_seconds, query_seconds))
        assert query_seconds < MAX_SECONDS_PER_QUERY, (
            f"N={n} next_actionable against real Postgres took {query_seconds:.3f}s — investigate"
        )

    print("\nAIX-EXECUTION-006-S3 measured timing (chain topology, real Postgres, this run):")
    for n, build_s, query_s in results:
        print(f"  N={n:5d}  build={build_s:.4f}s  next_actionable={query_s:.4f}s")

    # The point actually being proven: with three fixed queries regardless of
    # N (list_children + batch_active_dependencies + get_many), query time
    # must not grow anywhere near linearly with N.
    query_at_100 = next(q for n, _, q in results if n == 100)
    query_at_5000 = next(q for n, _, q in results if n == 5000)
    assert query_at_5000 < max(query_at_100 * 10, 0.5), (
        f"next_actionable at N=5000 ({query_at_5000:.4f}s) scaled far worse than N=100 "
        f"({query_at_100:.4f}s) against real Postgres — the batched-query fix may not be effective"
    )


@pytest.mark.asyncio
async def test_batch_methods_match_single_item_semantics_exactly() -> None:
    """Correctness, not just speed: a branching graph (not a pure chain) —
    some items with 0 deps, some with 1, some sharing a dependency target —
    proving batch_active_dependencies/get_many against real Postgres agree
    with the documented next_actionable semantics."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    suffix = uuid4().hex[:10]
    svc = PostgresKernelService(database_url=url)
    await svc.ensure_schema()
    tenant_id = f"t_branch_{suffix}"
    problem_id = f"pr_{suffix}"

    await svc.append_and_project(
        tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id,
        expected_sequence_number=1, event_type="PROBLEM_CREATED", command_id=f"pc_{suffix}",
        payload={"status": "EXECUTING"},
    )
    ids = {name: f"wi_{suffix}_{name}" for name in ("a", "b", "c", "d")}
    for name, wid in ids.items():
        await svc.append_and_project(
            tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=wid,
            expected_sequence_number=1, event_type="WI_CREATED", command_id=f"c_{suffix}_{name}",
            payload={"status": "PENDING", "parent_id": problem_id},
        )
    # b depends on a; c depends on a AND b; d depends on nothing.
    await svc.add_dependency(tenant_id=tenant_id, work_item_id=ids["b"], depends_on_work_item_id=ids["a"], command_id=f"d1_{suffix}")
    await svc.add_dependency(tenant_id=tenant_id, work_item_id=ids["c"], depends_on_work_item_id=ids["a"], command_id=f"d2_{suffix}")
    await svc.add_dependency(tenant_id=tenant_id, work_item_id=ids["c"], depends_on_work_item_id=ids["b"], command_id=f"d3_{suffix}")

    actionable = await svc.next_actionable(tenant_id=tenant_id, problem_id=problem_id)
    assert actionable == sorted([ids["a"], ids["d"]]), "only unblocked items — a and d have no active deps"

    deps_by_item = await svc.dependencies.batch_active_dependencies(
        tenant_id=tenant_id, work_item_ids=list(ids.values())
    )
    assert deps_by_item[ids["c"]] == {ids["a"], ids["b"]}
    assert deps_by_item[ids["d"]] == set()
