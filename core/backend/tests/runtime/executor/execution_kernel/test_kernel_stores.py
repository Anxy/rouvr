"""EX2-S002/S003/S004 integration tests for projection, deps, checkpoint, context."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter import (
    ContextBuilder,
    InMemoryCheckpointStore,
    InMemoryDependencyGraph,
    InMemoryExecutionKernelRepository,
    InMemoryKnowledgeStore,
    InMemoryProjectionStore,
    KnowledgeEntry,
    ResumeEngine,
    canonical_content_hash,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService


async def _seed_problem_graph(ledger, projection):
    events = [
        ("project", "proj_1", 1, "PROJECT_CREATED", "c1", {"status": "ACTIVE", "vision_summary": "Ship ERP", "requirements_summary": "Inventory first"}),
        ("capability", "cap_1", 1, "CAPABILITY_CREATED", "c2", {"status": "ACTIVE", "name": "Inventory", "parent_id": "proj_1", "roadmap_position": 1}),
        ("problem", "pr_1", 1, "PROBLEM_CREATED", "c3", {"status": "EXECUTING", "parent_id": "cap_1", "charter_summary": "Device registry"}),
        ("work_item", "wi_a", 1, "WI_CREATED", "c4", {"status": "PENDING", "parent_id": "pr_1", "title": "Schema"}),
        ("work_item", "wi_b", 1, "WI_CREATED", "c5", {"status": "PENDING", "parent_id": "pr_1", "title": "API"}),
    ]
    for aggregate_type, aggregate_id, seq, event_type, command_id, payload in events:
        ev = await ledger.append_event(
            tenant_id="t1",
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            expected_sequence_number=seq,
            event_type=event_type,
            command_id=command_id,
            payload=payload,
        )
        projection.apply_event(ev)


@pytest.mark.asyncio
async def test_projection_and_next_actionable() -> None:
    ledger = InMemoryExecutionKernelRepository()
    projection = InMemoryProjectionStore()
    deps = InMemoryDependencyGraph()
    await _seed_problem_graph(ledger, projection)

    deps.add_dependency(tenant_id="t1", work_item_id="wi_b", depends_on_work_item_id="wi_a", command_id="dep1")
    actionable = deps.next_actionable_work_items(tenant_id="t1", problem_id="pr_1", projection=projection)
    assert actionable == ["wi_a"]

    # Complete wi_a
    ev = await ledger.append_event(
        tenant_id="t1",
        aggregate_type="work_item",
        aggregate_id="wi_a",
        expected_sequence_number=2,
        event_type="WI_VERIFIED",
        command_id="c6",
        payload={"status": "VERIFIED", "parent_id": "pr_1", "title": "Schema"},
    )
    projection.apply_event(ev)
    actionable = deps.next_actionable_work_items(tenant_id="t1", problem_id="pr_1", projection=projection)
    assert actionable == ["wi_b"]


@pytest.mark.asyncio
async def test_checkpoint_supersede_and_resume() -> None:
    ledger = InMemoryExecutionKernelRepository()
    projection = InMemoryProjectionStore()
    deps = InMemoryDependencyGraph()
    checkpoints = InMemoryCheckpointStore()
    await _seed_problem_graph(ledger, projection)

    repo_state = {
        "repository_id": "aix-frontend",
        "branch": "main",
        "base_commit_sha": "abc1234",
        "current_commit_sha": "abc1234",
        "working_tree_diff_reference": "",
        "untracked_artifact_references": [],
        "environment_manifest_reference": "",
    }
    first = checkpoints.capture(
        tenant_id="t1", problem_id="pr_1", projection=projection, repository_state=repo_state
    )
    assert first.status == "CAPTURED"
    second = checkpoints.capture(
        tenant_id="t1", problem_id="pr_1", projection=projection, repository_state=repo_state
    )
    assert second.checkpoint_id != first.checkpoint_id
    assert checkpoints.current(tenant_id="t1", problem_id="pr_1").checkpoint_id == second.checkpoint_id

    engine = ResumeEngine(projection=projection, dependencies=deps, checkpoints=checkpoints)
    result = engine.resume(tenant_id="t1", problem_id="pr_1")
    assert result.source == "checkpoint"
    assert result.checkpoint_id == second.checkpoint_id
    assert "wi_a" in [w["work_item_id"] for w in result.work_items]


@pytest.mark.asyncio
async def test_context_builder_stable_hash_and_cache() -> None:
    ledger = InMemoryExecutionKernelRepository()
    projection = InMemoryProjectionStore()
    deps = InMemoryDependencyGraph()
    knowledge = InMemoryKnowledgeStore()
    await _seed_problem_graph(ledger, projection)

    knowledge.upsert(
        KnowledgeEntry(
            knowledge_entry_id="k1",
            tenant_id="t1",
            knowledge_kind="lesson_learned",
            capability_id="cap_1",
            problem_id="pr_1",
            content="Register devices before barcodes",
        )
    )
    knowledge.upsert(
        KnowledgeEntry(
            knowledge_entry_id="k2",
            tenant_id="t1",
            knowledge_kind="pattern",
            capability_id="cap_other",
            problem_id=None,
            content="Use idempotent APIs",
            applicable_capability_ids=("cap_1",),
        )
    )

    builder = ContextBuilder(projection=projection, dependencies=deps, knowledge=knowledge)
    a = builder.build(tenant_id="t1", work_item_id="wi_a", max_tokens=2000)
    b = builder.build(tenant_id="t1", work_item_id="wi_a", max_tokens=2000)
    assert a.capsule_content_sha256 == b.capsule_content_sha256
    assert a.capsule_content_sha256 == canonical_content_hash(a.as_hash_payload())
    assert a.schema_version == "aix.execution-context-capsule.v1"
    assert any(k["knowledge_entry_id"] == "k1" for k in a.knowledge)


@pytest.mark.asyncio
async def test_context_builder_omits_when_over_budget() -> None:
    ledger = InMemoryExecutionKernelRepository()
    projection = InMemoryProjectionStore()
    deps = InMemoryDependencyGraph()
    knowledge = InMemoryKnowledgeStore()
    await _seed_problem_graph(ledger, projection)
    knowledge.upsert(
        KnowledgeEntry(
            knowledge_entry_id="huge",
            tenant_id="t1",
            knowledge_kind="technical_knowledge",
            capability_id="cap_1",
            problem_id=None,
            content="X" * 5000,
        )
    )
    builder = ContextBuilder(projection=projection, dependencies=deps, knowledge=knowledge)
    capsule = builder.build(tenant_id="t1", work_item_id="wi_a", max_tokens=800)
    assert "huge" in capsule.omitted_knowledge_ids or not capsule.knowledge


@pytest.mark.asyncio
async def test_ex3_s003_ranking_prefers_same_capability_and_problem() -> None:
    """EX3-S003 assessment finding: ranking (_rank_key) already exists in
    EX2-S004 and matches AIX-CONTEXT-ENGINE.md's priority order (same
    Capability/Problem > cross-Capability pattern > general lesson), but
    nothing previously asserted the *order* itself — only that a repeat
    build is stable. This closes that verification gap."""
    ledger = InMemoryExecutionKernelRepository()
    projection = InMemoryProjectionStore()
    deps = InMemoryDependencyGraph()
    knowledge = InMemoryKnowledgeStore()
    await _seed_problem_graph(ledger, projection)

    # k_general: same Capability+Problem as wi_a -> should rank first.
    knowledge.upsert(
        KnowledgeEntry(
            knowledge_entry_id="k_same",
            tenant_id="t1",
            knowledge_kind="lesson_learned",
            capability_id="cap_1",
            problem_id="pr_1",
            content="Same capability and problem",
        )
    )
    # k_cross: a different Capability's pattern, only applicable to cap_1
    # via cross-reference -> should rank after k_same.
    knowledge.upsert(
        KnowledgeEntry(
            knowledge_entry_id="k_cross",
            tenant_id="t1",
            knowledge_kind="pattern",
            capability_id="cap_other",
            problem_id=None,
            content="Cross-capability pattern",
            applicable_capability_ids=("cap_1",),
        )
    )

    builder = ContextBuilder(projection=projection, dependencies=deps, knowledge=knowledge)
    capsule = builder.build(tenant_id="t1", work_item_id="wi_a", max_tokens=4000)
    ids_in_order = [k["knowledge_entry_id"] for k in capsule.knowledge]
    assert ids_in_order == ["k_same", "k_cross"], (
        "same-Capability/Problem Knowledge must rank ahead of cross-capability "
        f"pattern Knowledge; got order {ids_in_order}"
    )


@pytest.mark.asyncio
async def test_ex3_s003_cache_invalidates_on_new_knowledge_write() -> None:
    """EX3-S003 assessment finding: the cache key already includes the
    Knowledge Store's watermark (EX2-S004), but nothing previously proved
    a write actually changes the resulting capsule — only that a *repeat*
    call with no writes in between is stable. This is the missing half of
    Packet 6's own approval criterion ('produces the same canonical
    content hash absent a ledger write in between' implies it must differ
    *with* one)."""
    ledger = InMemoryExecutionKernelRepository()
    projection = InMemoryProjectionStore()
    deps = InMemoryDependencyGraph()
    knowledge = InMemoryKnowledgeStore()
    await _seed_problem_graph(ledger, projection)

    builder = ContextBuilder(projection=projection, dependencies=deps, knowledge=knowledge)
    before = builder.build(tenant_id="t1", work_item_id="wi_a", max_tokens=4000)
    assert before.knowledge == []

    knowledge.upsert(
        KnowledgeEntry(
            knowledge_entry_id="k_new",
            tenant_id="t1",
            knowledge_kind="lesson_learned",
            capability_id="cap_1",
            problem_id="pr_1",
            content="Learned after the first capsule was built",
        )
    )
    after = builder.build(tenant_id="t1", work_item_id="wi_a", max_tokens=4000)
    assert after.capsule_content_sha256 != before.capsule_content_sha256
    assert any(k["knowledge_entry_id"] == "k_new" for k in after.knowledge)

    # And a third call with no further writes is stable again (the
    # already-tested half), confirming this isn't just always-fresh.
    again = builder.build(tenant_id="t1", work_item_id="wi_a", max_tokens=4000)
    assert again.capsule_content_sha256 == after.capsule_content_sha256


@pytest.mark.asyncio
async def test_ex3_s006_replay_from_ledger_matches_live_projection() -> None:
    """DRIFT-021: a fresh projection independently folded from the ledger
    (the canonical durable record) must agree with the live, incrementally
    -maintained projection — this is the "replay/comparison" evidence the
    drift item names as missing. Uses the real KernelService flow (not a
    hand-built projection) so the live side is genuinely produced the
    normal way, not specially constructed to match."""
    svc = KernelService()
    for body in (
        ("project", "proj_1", 1, "PROJECT_CREATED", "c1", {"status": "ACTIVE"}),
        ("capability", "cap_1", 1, "CAPABILITY_CREATED", "c2", {"status": "ACTIVE", "parent_id": "proj_1"}),
        ("problem", "pr_1", 1, "PROBLEM_CREATED", "c3", {"status": "EXECUTING", "parent_id": "cap_1"}),
        ("work_item", "wi_1", 1, "WI_CREATED", "c4", {"status": "PENDING", "parent_id": "pr_1"}),
        ("work_item", "wi_1", 2, "WI_STARTED", "c5", {"status": "IN_PROGRESS", "parent_id": "pr_1"}),
    ):
        await svc.append_and_project(
            tenant_id="t1", aggregate_type=body[0], aggregate_id=body[1],
            expected_sequence_number=body[2], event_type=body[3], command_id=body[4], payload=body[5],
        )

    result = await svc.verify_projection_replay(
        tenant_id="t1", aggregate_type="work_item", aggregate_id="wi_1", declared_sequence_number=2
    )
    assert result == {
        "match": True,
        "replayed_sequence_number": 2,
        "live_sequence_number": 2,
        "mismatch_reason": None,
    }


@pytest.mark.asyncio
async def test_ex3_s006_replay_detects_live_projection_drift_and_declared_version_mismatch() -> None:
    """Hostile half of the same proof: if the live projection ever diverges
    from what the ledger actually says (simulated here the only way that
    can happen — an out-of-band write to the live store that never went
    through the ledger), replay must catch it rather than silently
    agreeing. Also proves declared_sequence_number comparison works."""
    svc = KernelService()
    await svc.append_and_project(
        tenant_id="t1", aggregate_type="work_item", aggregate_id="wi_1",
        expected_sequence_number=1, event_type="WI_CREATED", command_id="c1",
        payload={"status": "PENDING", "parent_id": "pr_1"},
    )

    # Simulate drift: something wrote directly to the live projection
    # without going through the ledger (exactly the bug class DRIFT-021
    # worries about going undetected).
    from backend.infrastructure.postgres.execution_kernel.repository import ExecutionEvent

    svc.projection.apply_event(
        ExecutionEvent(
            id="ev_out_of_band", tenant_id="t1", aggregate_type="work_item", aggregate_id="wi_1",
            sequence_number=1, event_type="WI_CREATED", command_id="c1",
            payload={"status": "CORRUPTED_BY_DRIFT", "parent_id": "pr_1"}, created_at="2026-08-04T00:00:00Z",
        )
    )

    result = await svc.verify_projection_replay(tenant_id="t1", aggregate_type="work_item", aggregate_id="wi_1")
    assert result["match"] is False
    assert "status" in result["mismatch_reason"]

    # Declared-version comparison, independent of the drift above: a
    # caller's stale belief about "what version am I at" must be caught
    # even when the live and replayed projections themselves agree.
    svc2 = KernelService()
    await svc2.append_and_project(
        tenant_id="t1", aggregate_type="work_item", aggregate_id="wi_2",
        expected_sequence_number=1, event_type="WI_CREATED", command_id="c2",
        payload={"status": "PENDING", "parent_id": "pr_1"},
    )
    stale = await svc2.verify_projection_replay(
        tenant_id="t1", aggregate_type="work_item", aggregate_id="wi_2", declared_sequence_number=99
    )
    assert stale["match"] is False
    assert "declared_sequence_number" in stale["mismatch_reason"]
