"""EX5-S3: Restart & Recovery Engine.

Every prior checkpoint/resume test (e.g. test_checkpoint_supersede_and_resume)
proves resume works within the SAME in-memory objects that captured the
checkpoint — it never proves the claim this sprint is actually about:
that a genuinely independent process (no shared Python state) can rehydrate
a Problem from nothing but what's durably in Postgres.

These tests build state with one PostgresKernelService instance, discard it
completely, then construct a brand-new instance against the same database
and prove IT can resume correctly — the real "process was killed and
restarted" scenario.

Uses a random suffix per run (not a fixed one) so this file is safely
re-runnable against a persistent database without manual cleanup — closing
the test-hygiene gap EX3-S009/EX2-S006 left open.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter.postgres_service import PostgresKernelService
from backend.infrastructure.postgres.execution_kernel.repository import resolve_test_database_url


@pytest.mark.asyncio
async def test_resume_survives_a_simulated_process_restart() -> None:
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    suffix = uuid4().hex[:12]
    tenant_id = f"t_restart_{suffix}"
    proj_id, cap_id, pr_id, wi_a, wi_b = (
        f"proj_{suffix}", f"cap_{suffix}", f"pr_{suffix}", f"wi_a_{suffix}", f"wi_b_{suffix}",
    )

    # --- "Process A": build real state, capture a checkpoint, then die. ---
    service_a = PostgresKernelService(database_url=url)
    await service_a.ensure_schema()
    for aggregate_type, aggregate_id, seq, event_type, command_id, payload in (
        ("project", proj_id, 1, "PROJECT_CREATED", f"c1_{suffix}", {"status": "ACTIVE"}),
        ("capability", cap_id, 1, "CAPABILITY_CREATED", f"c2_{suffix}", {"status": "ACTIVE", "parent_id": proj_id}),
        ("problem", pr_id, 1, "PROBLEM_CREATED", f"c3_{suffix}", {"status": "EXECUTING", "parent_id": cap_id}),
        ("work_item", wi_a, 1, "WI_CREATED", f"c4_{suffix}", {"status": "PENDING", "parent_id": pr_id, "title": "A"}),
        ("work_item", wi_b, 1, "WI_CREATED", f"c5_{suffix}", {"status": "PENDING", "parent_id": pr_id, "title": "B"}),
    ):
        await service_a.append_and_project(
            tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id,
            expected_sequence_number=seq, event_type=event_type, command_id=command_id, payload=payload,
        )
    checkpoint = await service_a.capture_checkpoint(
        tenant_id=tenant_id, problem_id=pr_id,
        repository_state={
            "repository_id": "aix", "branch": "main", "base_commit_sha": "abc1234",
            "current_commit_sha": "abc1234", "working_tree_diff_reference": "",
            "untracked_artifact_references": [], "environment_manifest_reference": "",
        },
    )
    assert checkpoint["status"] == "CAPTURED"
    # Simulate the process dying — no reference to service_a survives past this point.
    del service_a

    # --- "Process B": a completely fresh instance, no shared Python state. ---
    service_b = PostgresKernelService(database_url=url)
    resumed = await service_b.resume(tenant_id=tenant_id, problem_id=pr_id)

    assert resumed.source == "checkpoint", (
        "a fresh process must rehydrate from the durable checkpoint, not report "
        "no-history just because it personally never saw the capture happen"
    )
    assert resumed.checkpoint_id == checkpoint["checkpoint_id"]
    resumed_wi_ids = {w["work_item_id"] for w in resumed.work_items}
    assert {wi_a, wi_b} <= resumed_wi_ids

    # And process B must be able to keep working, not just report history.
    proj_a = await service_b.get_projection(tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=wi_a)
    assert proj_a is not None
    assert proj_a["status"] == "PENDING"

    actionable = await service_b.next_actionable(tenant_id=tenant_id, problem_id=pr_id)
    assert wi_a in actionable and wi_b in actionable


@pytest.mark.asyncio
async def test_resume_survives_restart_with_no_checkpoint_via_ledger_replay() -> None:
    """The harder case: process A dies WITHOUT ever capturing a checkpoint.
    Process B must still be able to reconstruct current state purely from
    the durable ledger (via projection + replay), not report total loss."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    suffix = uuid4().hex[:12]
    tenant_id = f"t_norecpt_{suffix}"
    wi_id = f"wi_{suffix}"

    service_a = PostgresKernelService(database_url=url)
    await service_a.ensure_schema()
    await service_a.append_and_project(
        tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=wi_id,
        expected_sequence_number=1, event_type="WI_CREATED", command_id=f"c1_{suffix}",
        payload={"status": "PENDING", "parent_id": f"pr_{suffix}"},
    )
    await service_a.append_and_project(
        tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=wi_id,
        expected_sequence_number=2, event_type="WI_STARTED", command_id=f"c2_{suffix}",
        payload={"status": "IN_PROGRESS", "parent_id": f"pr_{suffix}"},
    )
    del service_a  # no checkpoint was ever captured

    service_b = PostgresKernelService(database_url=url)
    replay = await service_b.verify_projection_replay(tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=wi_id)
    assert replay["match"] is True
    assert replay["live_sequence_number"] == 2

    proj = await service_b.get_projection(tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=wi_id)
    assert proj is not None
    assert proj["status"] == "IN_PROGRESS"
