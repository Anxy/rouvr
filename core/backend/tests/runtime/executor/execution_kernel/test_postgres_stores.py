"""EX2-S006: postgres bootstrap + optional live postgres store tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter.postgres_service import (
    PostgresKernelService,
    build_execution_kernel_service,
)
from backend.infrastructure.postgres.execution_kernel.repository import resolve_test_database_url
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService


@pytest.mark.asyncio
async def test_build_kernel_falls_back_to_inprocess_without_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIX_EXECUTION_KERNEL_TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("AIX_OBJECTIVE_CYCLE_TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("AIX_LEDGER_DAL_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    service = await build_execution_kernel_service(None)
    assert isinstance(service, KernelService)
    assert service.mode == "inprocess"


@pytest.mark.asyncio
async def test_postgres_kernel_roundtrip_when_url_configured() -> None:
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    service = PostgresKernelService(database_url=url)
    await service.ensure_schema()
    # AIX-EXECUTION-006-S6: randomized so this file is safely re-runnable
    # against a persistent database (fixed tenant_id/suffix previously
    # collided with rows left by a prior run).
    from uuid import uuid4

    run_suffix = uuid4().hex[:10]
    tenant_id = f"t_pg_{run_suffix}"
    suffix = f"ex2s006_{run_suffix}"
    await service.append_and_project(
        tenant_id=tenant_id,
        aggregate_type="project",
        aggregate_id=f"proj_{suffix}",
        expected_sequence_number=1,
        event_type="PROJECT_CREATED",
        command_id=f"cmd_p_{suffix}",
        payload={"status": "ACTIVE", "vision_summary": "V", "requirements_summary": "R"},
    )
    await service.append_and_project(
        tenant_id=tenant_id,
        aggregate_type="capability",
        aggregate_id=f"cap_{suffix}",
        expected_sequence_number=1,
        event_type="CAPABILITY_CREATED",
        command_id=f"cmd_c_{suffix}",
        payload={"status": "ACTIVE", "name": "C", "parent_id": f"proj_{suffix}"},
    )
    await service.append_and_project(
        tenant_id=tenant_id,
        aggregate_type="problem",
        aggregate_id=f"pr_{suffix}",
        expected_sequence_number=1,
        event_type="PROBLEM_CREATED",
        command_id=f"cmd_pr_{suffix}",
        payload={"status": "EXECUTING", "parent_id": f"cap_{suffix}", "charter_summary": "X"},
    )
    await service.append_and_project(
        tenant_id=tenant_id,
        aggregate_type="work_item",
        aggregate_id=f"wi_{suffix}",
        expected_sequence_number=1,
        event_type="WI_CREATED",
        command_id=f"cmd_w_{suffix}",
        payload={"status": "PENDING", "parent_id": f"pr_{suffix}", "title": "T"},
    )
    proj = await service.get_projection(
        tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=f"wi_{suffix}"
    )
    assert proj is not None
    assert proj["status"] == "PENDING"
    actionable = await service.next_actionable(tenant_id=tenant_id, problem_id=f"pr_{suffix}")
    assert f"wi_{suffix}" in actionable
    cp = await service.capture_checkpoint(
        tenant_id=tenant_id,
        problem_id=f"pr_{suffix}",
        repository_state={
            "repository_id": "repo",
            "branch": "main",
            "base_commit_sha": "abc1234",
            "current_commit_sha": "abc1234",
            "working_tree_diff_reference": "",
            "untracked_artifact_references": [],
            "environment_manifest_reference": "",
        },
    )
    assert cp["status"] == "CAPTURED"
    resume = await service.resume(tenant_id=tenant_id, problem_id=f"pr_{suffix}")
    assert resume.source == "checkpoint"
    capsule = await service.build_capsule(tenant_id=tenant_id, work_item_id=f"wi_{suffix}", max_tokens=2000)
    assert len(capsule.capsule_content_sha256) == 64


@pytest.mark.asyncio
async def test_ex3_s009_hostile_cross_tenant_read_bypassing_repository_layer() -> None:
    """DRIFT-035, UPDATED by AIX-EXECUTION-006-S4: this originally proved no
    RLS/policy existed on `aix_execution_events` at all. That's no longer
    true — EX5-S8 added RLS to this table, and AIX-EXECUTION-006-S4 added it
    to four more, both FORCEd and verified against a real non-superuser
    application role (see test_ex6_s4_tenant_isolation.py). This test still
    demonstrates a real, current gap, but a narrower one: it connects using
    `resolve_test_database_url()` — this repo's deliberately-superuser test
    sandbox credential — and Postgres superusers unconditionally bypass
    FORCE ROW LEVEL SECURITY. So the leak below is not "no isolation
    exists"; it's "a superuser connection bypasses the isolation that does
    exist," which is exactly why AIX-EXECUTION-006-S4 also made the real
    app's credentials non-superuser and added a startup guard that refuses
    to run if they ever are again."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    import asyncpg  # type: ignore[import-untyped]
    from uuid import uuid4

    from backend.infrastructure.postgres.execution_kernel.ledger_schema import EXECUTION_EVENTS_TABLE

    # AIX-EXECUTION-006-S6: randomized so this file is safely re-runnable
    # against a persistent database.
    suffix = f"ex3s009_{uuid4().hex[:10]}"
    tenant_a = f"t_a_{suffix}"
    tenant_b = f"t_b_{suffix}"

    service = PostgresKernelService(database_url=url)
    await service.ensure_schema()
    await service.append_and_project(
        tenant_id=tenant_a,
        aggregate_type="project",
        aggregate_id=f"secret_proj_{suffix}",
        expected_sequence_number=1,
        event_type="PROJECT_CREATED",
        command_id=f"cmd_a_{suffix}",
        payload={"status": "ACTIVE", "vision_summary": "Tenant A's confidential vision"},
    )

    conn = await asyncpg.connect(url)
    try:
        # Deliberately bypasses ExecutionKernelRepository entirely: a raw
        # query as tenant B, filtering only by aggregate_id, no tenant_id
        # clause anywhere. If DB-level isolation existed, this would see
        # zero rows for a connection scoped to tenant_b. It does not.
        leaked = await conn.fetch(
            f"SELECT tenant_id, payload FROM {EXECUTION_EVENTS_TABLE} WHERE aggregate_id = $1",
            f"secret_proj_{suffix}",
        )
    finally:
        await conn.close()

    assert len(leaked) >= 1, "expected the seeded row to be readable at all"
    assert any(row["tenant_id"] == tenant_a for row in leaked), (
        "tenant A's row must exist as seeded"
    )
    # The actual finding: a query scoped to tenant_b's *intent* (no
    # tenant_id filter applied) still surfaces tenant_a's row. Nothing in
    # the database itself enforces the boundary the application code
    # relies on.
    assert not any(row["tenant_id"] == tenant_b for row in leaked), (
        "sanity check: tenant B never wrote anything here"
    )
