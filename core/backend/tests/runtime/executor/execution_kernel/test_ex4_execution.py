"""EX4-R2-P2-S002 / EX4-S001 / EX4-S002 execution tests."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.aix_core.runtime.executor.execution_kernel_adapter import (
    ContextBuilder,
    InMemoryDependencyGraph,
    InMemoryExecutionKernelRepository,
    InMemoryKnowledgeStore,
    InMemoryProjectionStore,
    KnowledgeEntry,
    KernelService,
    canonical_content_hash,
    capsule_to_contract_dict,
)
from backend.aix_core.runtime.executor.capability_registry import InMemoryCapabilityRegistry
from backend.aix_core.runtime.executor.knowledge_graph import InMemoryKnowledgeGraph
from backend.infrastructure.postgres.execution_kernel.postgres_stores import (
    PostgresCapabilityRegistry,
    PostgresKnowledgeGraphStore,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.postgres_service import PostgresKernelService
from backend.infrastructure.postgres.execution_kernel.repository import (
    ExecutionKernelRepositoryError,
    resolve_test_database_url,
)
from backend.infrastructure.postgres.execution_kernel.schema import (
    CAPABILITY_REGISTRY_TABLE,
    KNOWLEDGE_EMBEDDINGS_TABLE,
    KNOWLEDGE_GRAPH_EDGES_TABLE,
    LEARNING_LIFECYCLE_TABLE,
)
from backend.routers.execution_kernel_router import router as execution_kernel_router


async def _seed(ledger, projection) -> None:
    events = [
        ("project", "proj_1", 1, "PROJECT_CREATED", "c1", {"status": "ACTIVE", "vision_summary": "Ship", "requirements_summary": "Inv"}),
        ("capability", "cap_1", 1, "CAPABILITY_CREATED", "c2", {"status": "ACTIVE", "name": "Inv", "parent_id": "proj_1"}),
        ("problem", "pr_1", 1, "PROBLEM_CREATED", "c3", {"status": "EXECUTING", "parent_id": "cap_1", "charter_summary": "Reg"}),
        ("work_item", "wi_1", 1, "WI_CREATED", "c4", {"status": "VERIFIED", "parent_id": "pr_1", "title": "Schema", "evidence_refs": ["ev:1"]}),
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


def test_knowledge_graph_edges_table_name() -> None:
    assert KNOWLEDGE_GRAPH_EDGES_TABLE == "aix_execution_knowledge_graph_edges"
    assert CAPABILITY_REGISTRY_TABLE == "aix_execution_capability_registry"
    assert LEARNING_LIFECYCLE_TABLE == "aix_execution_learning_lifecycle"
    assert KNOWLEDGE_EMBEDDINGS_TABLE == "aix_execution_knowledge_embeddings"


def test_ex4_r2_p6_s001_registry_fail_closed_without_tenant() -> None:
    reg = InMemoryCapabilityRegistry()
    from backend.aix_core.runtime.executor.capability_registry import RegisteredCapability
    from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError

    with pytest.raises(ExecutionKernelRepositoryError):
        reg.register(
            RegisteredCapability(
                capability_key="bounded_repair",
                version="1.0.0",
                tenant_id="",
                display_name="Bounded Repair",
            )
        )


@pytest.mark.asyncio
async def test_ex4_r2_p6_s001_register_list_resolve_and_http() -> None:
    svc = KernelService()
    base = await svc.register_capability(
        tenant_id="t1",
        capability_key="schema_edit",
        version="1.0.0",
        display_name="Schema Edit",
        executor_binding="bounded_repair",
        verification_method="tests",
        cost_class="low",
        risk_class="medium",
    )
    assert base["capability_key"] == "schema_edit"
    top = await svc.register_capability(
        tenant_id="t1",
        capability_key="api_repair",
        version="1.0.0",
        display_name="API Repair",
        executor_binding="bounded_repair",
        verification_method="tests",
        dependencies=["schema_edit"],
        cost_class="medium",
        risk_class="high",
    )
    assert top["dependencies"] == ["schema_edit"]
    resolved = await svc.resolve_capability_dependencies(tenant_id="t1", capability_key="api_repair")
    keys = [r["capability_key"] for r in resolved]
    assert keys == ["schema_edit", "api_repair"]

    app = FastAPI()
    app.state.execution_kernel_service = svc
    app.state.execution_kernel_require_gateway = False
    app.include_router(execution_kernel_router)
    transport = ASGITransport(app=app)
    headers = {"X-Tenant-ID": "t1"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/aix/execution-kernel/capability-registry", headers=headers)
        assert listed.status_code == 200
        assert len(listed.json()["capabilities"]) >= 2
        got = await client.get("/aix/execution-kernel/capability-registry/api_repair", headers=headers)
        assert got.status_code == 200
        assert got.json()["capability"]["display_name"] == "API Repair"
        resolved_http = await client.get(
            "/aix/execution-kernel/capability-registry/api_repair/resolve", headers=headers
        )
        assert resolved_http.status_code == 200
        assert [x["capability_key"] for x in resolved_http.json()["resolved"]] == [
            "schema_edit",
            "api_repair",
        ]


@pytest.mark.asyncio
async def test_ex4_r2_p6_s001_postgres_registry_roundtrip_when_url_configured() -> None:
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    from backend.aix_core.runtime.executor.capability_registry import RegisteredCapability

    registry = PostgresCapabilityRegistry(url)
    tenant_id = "t_pg_registry"
    dependency = RegisteredCapability(
        capability_key="schema_edit_ex4r2p6",
        version="1.0.0",
        tenant_id=tenant_id,
        display_name="Schema Edit",
        input_contract={"type": "object"},
        executor_binding="bounded_repair",
        verification_method="pytest",
        cost_class="low",
        risk_class="medium",
    )
    await registry.register(dependency)
    await registry.register(
        RegisteredCapability(
            capability_key="api_repair_ex4r2p6",
            version="1.0.0",
            tenant_id=tenant_id,
            display_name="API Repair",
            dependencies=(dependency.capability_key,),
            executor_binding="bounded_repair",
            verification_method="pytest",
            cost_class="medium",
            risk_class="high",
        )
    )
    resolved = await registry.resolve_dependencies(
        tenant_id=tenant_id, capability_key="api_repair_ex4r2p6"
    )
    assert [entry.capability_key for entry in resolved] == [
        "schema_edit_ex4r2p6",
        "api_repair_ex4r2p6",
    ]
    assert await registry.get(
        tenant_id="another_tenant", capability_key="api_repair_ex4r2p6"
    ) is None


def test_ex4_r2_p2_s002_edge_fail_closed_without_tenant() -> None:
    g = InMemoryKnowledgeGraph()
    with pytest.raises(ExecutionKernelRepositoryError, match="tenant_id is required"):
        g.add_edge(
            tenant_id="",
            edge_type="ABOUT",
            from_type="knowledge",
            from_id="k1",
            to_type="capability",
            to_id="cap_1",
            command_id="cmd1",
        )
    with pytest.raises(ExecutionKernelRepositoryError, match="from_id and to_id"):
        g.add_edge(
            tenant_id="t1",
            edge_type="ABOUT",
            from_type="knowledge",
            from_id="",
            to_type="capability",
            to_id="cap_1",
            command_id="cmd2",
        )


def test_ex4_r2_p2_s002_traversal_never_crosses_tenants() -> None:
    g = InMemoryKnowledgeGraph()
    for tenant_id, problem_id in (("t1", "pr_visible"), ("t2", "pr_secret")):
        g.add_edge(
            tenant_id=tenant_id,
            edge_type="USES_PATTERN",
            from_type="problem",
            from_id=problem_id,
            to_type="pattern",
            to_id="kp_shared_identifier",
            command_id=f"cmd:{tenant_id}",
        )
    paths = g.traverse(
        tenant_id="t1",
        start_type="problem",
        start_id="pr_visible",
        direction="both",
        max_depth=2,
    )
    assert not any("pr_secret" in (path["from_id"], path["to_id"]) for path in paths)


def test_ex4_r2_p2_s002_supersede_retains_old_edge() -> None:
    g = InMemoryKnowledgeGraph()
    first = g.add_edge(
        tenant_id="t1",
        edge_type="USES_PATTERN",
        from_type="work_item",
        from_id="wi_1",
        to_type="pattern",
        to_id="kp_1",
        command_id="e1",
    )
    second = g.add_edge(
        tenant_id="t1",
        edge_type="USES_PATTERN",
        from_type="work_item",
        from_id="wi_1",
        to_type="pattern",
        to_id="kp_1",
        command_id="e2",
        supersede_matching_active=True,
    )
    assert first.status == "ACTIVE"  # immutable return value remains a historical snapshot
    all_edges = g.list_edges(tenant_id="t1", active_only=False)
    statuses = {e.edge_id: e.status for e in all_edges}
    assert statuses[first.edge_id] == "SUPERSEDED"
    assert statuses[second.edge_id] == "ACTIVE"
    assert g.get_edge(tenant_id="t1", edge_id=first.edge_id) is not None


def test_ex4_r2_p2_s002_traverse_multihop() -> None:
    g = InMemoryKnowledgeGraph()
    g.add_edge(
        tenant_id="t1",
        edge_type="USES_PATTERN",
        from_type="problem",
        from_id="pr_a",
        to_type="pattern",
        to_id="kp_1",
        command_id="t1",
    )
    g.add_edge(
        tenant_id="t1",
        edge_type="USES_PATTERN",
        from_type="problem",
        from_id="pr_b",
        to_type="pattern",
        to_id="kp_1",
        command_id="t2",
    )
    paths = g.traverse(
        tenant_id="t1",
        start_type="problem",
        start_id="pr_a",
        edge_types=["USES_PATTERN"],
        direction="both",
        max_depth=2,
    )
    assert any(p["to_id"] == "kp_1" or p["from_id"] == "kp_1" for p in paths)
    assert any(p["from_id"] == "pr_b" or p["to_id"] == "pr_b" for p in paths)


@pytest.mark.asyncio
async def test_ex4_r2_p2_s002_postgres_edge_roundtrip_when_url_configured() -> None:
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    store = PostgresKnowledgeGraphStore(url)
    suffix = "ex4r2p2s002"
    first = await store.add_edge(
        tenant_id="t_pg_graph",
        edge_type="USES_PATTERN",
        from_type="problem",
        from_id=f"pr_a_{suffix}",
        to_type="pattern",
        to_id=f"kp_{suffix}",
        command_id=f"graph_first_{suffix}",
    )
    replay = await store.add_edge(
        tenant_id="t_pg_graph",
        edge_type="USES_PATTERN",
        from_type="problem",
        from_id=f"pr_a_{suffix}",
        to_type="pattern",
        to_id=f"kp_{suffix}",
        command_id=f"graph_first_{suffix}",
    )
    assert replay.edge_id == first.edge_id

    replacement = await store.add_edge(
        tenant_id="t_pg_graph",
        edge_type="USES_PATTERN",
        from_type="problem",
        from_id=f"pr_a_{suffix}",
        to_type="pattern",
        to_id=f"kp_{suffix}",
        command_id=f"graph_replace_{suffix}",
        supersede_matching_active=True,
    )
    all_edges = await store.get_edges(tenant_id="t_pg_graph", active_only=False)
    statuses = {edge.edge_id: edge.status for edge in all_edges}
    assert statuses[first.edge_id] == "SUPERSEDED"
    assert statuses[replacement.edge_id] == "ACTIVE"


@pytest.mark.asyncio
async def test_ex4_r2_p2_s002_distill_emits_derived_from_edges() -> None:
    svc = KernelService()
    await _seed(svc.ledger, svc.projection)
    written = await svc.distill_learning(
        tenant_id="t1",
        work_item_id="wi_1",
        decision="ACCEPT",
        evidence_refs=["ev:1"],
        lesson_summary="Prefer idempotent device registration",
        promote_pattern=True,
    )
    assert written
    edges = await svc.list_knowledge_graph_edges(
        tenant_id="t1", edge_type="DERIVED_FROM", from_type="knowledge"
    )
    assert edges
    assert any(e["to_type"] == "work_item" and e["to_id"] == "wi_1" for e in edges)
    assert any(e["to_type"] == "evidence" and e["to_id"] == "ev:1" for e in edges)


@pytest.mark.asyncio
async def test_ex4_s002_superseded_never_outranks_replacement() -> None:
    ledger = InMemoryExecutionKernelRepository()
    projection = InMemoryProjectionStore()
    deps = InMemoryDependencyGraph()
    knowledge = InMemoryKnowledgeStore()
    await _seed(ledger, projection)
    knowledge.upsert(
        KnowledgeEntry(
            knowledge_entry_id="k_old",
            tenant_id="t1",
            knowledge_kind="lesson_learned",
            capability_id="cap_1",
            problem_id="pr_1",
            content="Old lesson",
            superseded_by_entry_id="k_new",
        )
    )
    knowledge.upsert(
        KnowledgeEntry(
            knowledge_entry_id="k_new",
            tenant_id="t1",
            knowledge_kind="lesson_learned",
            capability_id="cap_1",
            problem_id="pr_1",
            content="New lesson",
        )
    )
    builder = ContextBuilder(projection=projection, dependencies=deps, knowledge=knowledge)
    capsule = builder.build(tenant_id="t1", work_item_id="wi_1", max_tokens=4000)
    ids = [k["knowledge_entry_id"] for k in capsule.knowledge]
    assert ids.index("k_new") < ids.index("k_old")
    assert knowledge.get(tenant_id="t1", knowledge_entry_id="k_old") is not None


@pytest.mark.asyncio
async def test_ex4_s002_stale_marked_not_deleted() -> None:
    ledger = InMemoryExecutionKernelRepository()
    projection = InMemoryProjectionStore()
    deps = InMemoryDependencyGraph()
    knowledge = InMemoryKnowledgeStore()
    await _seed(ledger, projection)
    knowledge.upsert(
        KnowledgeEntry(
            knowledge_entry_id="k_stale",
            tenant_id="t1",
            knowledge_kind="technical_knowledge",
            capability_id="cap_1",
            problem_id="pr_1",
            content="Expired note",
            valid_until="2020-01-01T00:00:00Z",
        )
    )
    builder = ContextBuilder(projection=projection, dependencies=deps, knowledge=knowledge)
    capsule = builder.build(tenant_id="t1", work_item_id="wi_1", max_tokens=4000)
    stale_items = [k for k in capsule.knowledge if k["knowledge_entry_id"] == "k_stale"]
    assert stale_items and stale_items[0].get("stale") is True
    assert knowledge.get(tenant_id="t1", knowledge_entry_id="k_stale") is not None


@pytest.mark.asyncio
async def test_ex4_s001_external_capsule_preserves_internal_hash() -> None:
    app = FastAPI()
    app.state.execution_kernel_service = KernelService()
    app.state.execution_kernel_require_gateway = False
    app.include_router(execution_kernel_router)
    svc: KernelService = app.state.execution_kernel_service
    await _seed(svc.ledger, svc.projection)

    internal = await svc.build_capsule(tenant_id="t1", work_item_id="wi_1", max_tokens=2000)
    assert internal.capsule_content_sha256 == canonical_content_hash(internal.as_hash_payload())
    assert capsule_to_contract_dict(internal)["capsule_content_sha256"] == internal.capsule_content_sha256

    transport = ASGITransport(app=app)
    headers = {"X-Tenant-ID": "t1"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        a = await client.post(
            "/aix/execution-kernel/capsules",
            headers=headers,
            json={"work_item_id": "wi_1", "max_tokens": 2000},
        )
        b = await client.post(
            "/aix/execution-kernel/capsules",
            headers=headers,
            json={"work_item_id": "wi_1", "max_tokens": 2000},
        )
    assert a.status_code == 200 and b.status_code == 200
    ca, cb = a.json()["capsule"], b.json()["capsule"]
    assert ca["capsule_content_sha256"] == cb["capsule_content_sha256"] == internal.capsule_content_sha256
    assert "repository" in ca and "decisions" in ca and "acceptance" in ca


@pytest.mark.asyncio
async def test_ex4_r2_p7_s001_discover_and_recommend_no_autopromote() -> None:
    svc = KernelService()
    await _seed(svc.ledger, svc.projection)
    lesson = "Prefer idempotent device registration before barcodes"
    for i, kid in enumerate(("kl_a", "kl_b")):
        await svc.upsert_knowledge(
            tenant_id="t1",
            knowledge_entry_id=kid,
            knowledge_kind="lesson_learned",
            content=lesson,
            command_id=f"lesson-{i}",
            capability_id="cap_1",
            problem_id="pr_1",
        )
    candidates = await svc.discover_patterns(tenant_id="t1", min_occurrences=2)
    assert any(c["recommendation"] == "promote_candidate" and c["occurrence_count"] >= 2 for c in candidates)
    listed = [e for e in svc.knowledge._entries if e.knowledge_kind == "pattern"]  # noqa: SLF001
    assert listed == []

    await svc.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="kp_1",
        knowledge_kind="pattern",
        content=lesson,
        command_id="pat-1",
        capability_id="cap_1",
        problem_id="pr_1",
        applicable_capability_ids=["cap_1"],
    )
    await svc.record_pattern_use(
        tenant_id="t1",
        work_item_id="wi_1",
        pattern_entry_id="kp_1",
        command_id="use-1",
    )
    recs = await svc.recommend_patterns(tenant_id="t1", capability_id="cap_1", problem_id="pr_1")
    assert recs and recs[0]["knowledge_entry_id"] == "kp_1"
    assert any("uses_pattern_edges" in r for r in recs[0]["reasons"])

    app = FastAPI()
    app.state.execution_kernel_service = svc
    app.state.execution_kernel_require_gateway = False
    app.include_router(execution_kernel_router)
    transport = ASGITransport(app=app)
    headers = {"X-Tenant-ID": "t1"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        disc = await client.get("/aix/execution-kernel/patterns/discover", headers=headers)
        assert disc.status_code == 200
        assert disc.json()["auto_promoted"] is False
        rec = await client.get(
            "/aix/execution-kernel/patterns/recommend",
            headers=headers,
            params={"capability_id": "cap_1"},
        )
        assert rec.status_code == 200
        assert rec.json()["recommendations"]


@pytest.mark.asyncio
async def test_ex4_r3_p8_s001_learning_lifecycle_governed_activation() -> None:
    from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError

    svc = KernelService()
    await _seed(svc.ledger, svc.projection)

    obs = await svc.observe_learning(
        tenant_id="t1",
        summary="Device registration must precede barcode binding",
        source_work_item_id="wi_1",
        capability_id="cap_1",
        problem_id="pr_1",
    )
    lid = obs["learning_id"]
    assert obs["stage"] == "OBSERVATION"
    assert obs["required_proposal_level"] == 1

    with pytest.raises(ExecutionKernelRepositoryError, match="illegal transition"):
        await svc.advance_learning(tenant_id="t1", learning_id=lid, to_stage="ACTIVE_KNOWLEDGE")

    for stage in (
        "CANDIDATE_LESSON",
        "VALIDATION",
        "SCOPE_CLASSIFICATION",
        "CONFIDENCE",
        "AWAITING_APPROVAL",
    ):
        advanced = await svc.advance_learning(tenant_id="t1", learning_id=lid, to_stage=stage)
        assert advanced["stage"] == stage

    await svc.classify_learning_scope(tenant_id="t1", learning_id=lid, scope="organisation")
    scoped = (await svc.list_learning(tenant_id="t1", stage="AWAITING_APPROVAL"))[0]
    assert scoped["required_proposal_level"] == 4

    with pytest.raises(ExecutionKernelRepositoryError, match="proposal_level"):
        await svc.approve_learning(
            tenant_id="t1",
            learning_id=lid,
            actor_id="actor_1",
            proposal_level=2,
        )

    approved = await svc.approve_learning(
        tenant_id="t1",
        learning_id=lid,
        actor_id="actor_1",
        proposal_level=4,
        proposal_id="prop_org_1",
        promote_to_knowledge=True,
        command_id="lrn-approve-1",
    )
    assert approved["stage"] == "ACTIVE_KNOWLEDGE"
    assert approved["approved_by_actor"] == "actor_1"
    assert approved["active_knowledge_entry_id"]
    assert approved["influence_level"] == "recommendation_to_planner"
    knowledge = svc.knowledge.get(
        tenant_id="t1", knowledge_entry_id=approved["active_knowledge_entry_id"]
    )
    assert knowledge is not None
    assert knowledge.knowledge_kind == "lesson_learned"

    # Automatic planner influence blocked without explicit policy
    with pytest.raises(ExecutionKernelRepositoryError, match="auto_promote"):
        svc.learning_lifecycle.set_influence_level(
            tenant_id="t1",
            learning_id=lid,
            influence_level="automatic_heuristic_update",
        )

    rejected = await svc.observe_learning(tenant_id="t1", summary="Noise signal")
    rejected = await svc.reject_learning(tenant_id="t1", learning_id=rejected["learning_id"])
    assert rejected["stage"] == "REJECTED"

    app = FastAPI()
    app.state.execution_kernel_service = svc
    app.state.execution_kernel_require_gateway = False
    app.include_router(execution_kernel_router)
    transport = ASGITransport(app=app)
    headers = {"X-Tenant-ID": "t1", "X-Actor-ID": "actor_http"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/aix/execution-kernel/learning",
            headers=headers,
            json={"summary": "HTTP observation", "capability_id": "cap_1"},
        )
        assert created.status_code == 200
        http_id = created.json()["learning"]["learning_id"]
        for stage in (
            "CANDIDATE_LESSON",
            "VALIDATION",
            "SCOPE_CLASSIFICATION",
            "CONFIDENCE",
            "AWAITING_APPROVAL",
        ):
            adv = await client.post(
                f"/aix/execution-kernel/learning/{http_id}/advance",
                headers=headers,
                json={"to_stage": stage},
            )
            assert adv.status_code == 200, adv.text
        scope = await client.post(
            f"/aix/execution-kernel/learning/{http_id}/scope",
            headers=headers,
            json={"scope": "local"},
        )
        assert scope.status_code == 200
        conf = await client.post(
            f"/aix/execution-kernel/learning/{http_id}/confidence",
            headers=headers,
            json={"confidence": "medium"},
        )
        assert conf.status_code == 200
        approve = await client.post(
            f"/aix/execution-kernel/learning/{http_id}/approve",
            headers=headers,
            json={"actor_id": "actor_http", "proposal_level": 1},
        )
        assert approve.status_code == 200
        assert approve.json()["learning"]["stage"] == "ACTIVE_KNOWLEDGE"
        assert approve.json()["auto_promoted"] is False
        listed = await client.get(
            "/aix/execution-kernel/learning",
            headers=headers,
            params={"active_only": "true"},
        )
        assert listed.status_code == 200
        assert any(r["learning_id"] == http_id for r in listed.json()["learning"])


@pytest.mark.asyncio
async def test_ex4_r3_p9_s001_semantic_retrieval_additive_pgvector_decision() -> None:
    from backend.aix_core.runtime.executor.pattern_engine import content_fingerprint
    from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
    from backend.aix_core.runtime.executor.semantic_retrieval import (
        RANKING_FAILURE_QUERIES,
        TECHNOLOGY_DECISION,
    )

    assert TECHNOLOGY_DECISION["preferred_durable"] == "postgres_pgvector"
    assert TECHNOLOGY_DECISION["replaces_ranked_retrieval"] is False
    assert "dedicated_vector_db" in TECHNOLOGY_DECISION["rejected_first_slice"]
    assert {q["id"] for q in RANKING_FAILURE_QUERIES} >= {"SQ1", "SQ2", "SQ3"}

    svc = KernelService()
    await _seed(svc.ledger, svc.projection)
    lesson = "Prefer idempotent device registration before barcodes"
    distractor = "Always archive superseded architecture decisions after review"
    await svc.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="kl_sem",
        knowledge_kind="lesson_learned",
        content=lesson,
        command_id="sem-1",
        capability_id="cap_1",
        problem_id="pr_1",
    )
    await svc.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="kl_noise",
        knowledge_kind="lesson_learned",
        content=distractor,
        command_id="sem-2",
        capability_id="cap_1",
        problem_id="pr_1",
    )

    # Fingerprint clustering would NOT group the paraphrase with the lesson.
    paraphrase = RANKING_FAILURE_QUERIES[0]["query"]
    assert content_fingerprint(paraphrase) != content_fingerprint(lesson)

    with pytest.raises(ExecutionKernelRepositoryError, match="tenant_id"):
        await svc.semantic_search(tenant_id="", query=paraphrase)

    hits = await svc.semantic_search(tenant_id="t1", query=paraphrase, limit=3)
    assert hits and hits[0]["knowledge_entry_id"] == "kl_sem"
    assert hits[0]["score"] > 0.05
    assert hits[0]["backend"] == "hashed_bow_v1"

    concept_hits = await svc.semantic_search(
        tenant_id="t1",
        query=RANKING_FAILURE_QUERIES[1]["query"],
        knowledge_kinds=["lesson_learned"],
    )
    assert any(h["knowledge_entry_id"] == "kl_sem" for h in concept_hits)

    decision = await svc.semantic_technology_decision()
    assert decision["preferred_durable"] == "postgres_pgvector"
    assert decision["replaces_ranked_retrieval"] is False

    # Ranked capsule path still works (semantic is additive).
    capsule = await svc.build_capsule(tenant_id="t1", work_item_id="wi_1", max_tokens=2000)
    assert any(k["knowledge_entry_id"] == "kl_sem" for k in capsule.knowledge)

    app = FastAPI()
    app.state.execution_kernel_service = svc
    app.state.execution_kernel_require_gateway = False
    app.include_router(execution_kernel_router)
    transport = ASGITransport(app=app)
    headers = {"X-Tenant-ID": "t1"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        dec = await client.get("/aix/execution-kernel/semantic/decision", headers=headers)
        assert dec.status_code == 200
        assert dec.json()["decision"]["preferred_durable"] == "postgres_pgvector"
        searched = await client.post(
            "/aix/execution-kernel/semantic/search",
            headers=headers,
            json={"query": paraphrase, "limit": 3},
        )
        assert searched.status_code == 200
        body = searched.json()
        assert body["replaces_ranked_retrieval"] is False
        assert body["preferred_durable"] == "postgres_pgvector"
        assert body["hits"][0]["knowledge_entry_id"] == "kl_sem"


@pytest.mark.asyncio
async def test_ex4_r2_p7_s001_postgres_service_parity_when_url_configured() -> None:
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    service = PostgresKernelService(database_url=url)
    await service.ensure_schema()
    # AIX-EXECUTION-006-S6: randomized so this file is safely re-runnable
    # against a persistent database (a fixed tenant_id previously collided
    # with rows left by a prior run).
    tenant_id = f"t_pg_patterns_{uuid4().hex[:10]}"
    content = "Prefer idempotent device registration before barcodes"
    command_suffix = uuid4().hex
    for index, knowledge_entry_id in enumerate(("kl_pg_a", "kl_pg_b")):
        await service.upsert_knowledge(
            tenant_id=tenant_id,
            knowledge_entry_id=knowledge_entry_id,
            knowledge_kind="lesson_learned",
            content=content,
            command_id=f"p7-lesson-{index}-{command_suffix}",
            capability_id="cap_pg",
            problem_id="pr_pg",
        )
    candidates = await service.discover_patterns(tenant_id=tenant_id, min_occurrences=2)
    assert any(candidate["recommendation"] == "promote_candidate" for candidate in candidates)

    await service.upsert_knowledge(
        tenant_id=tenant_id,
        knowledge_entry_id="kp_pg",
        knowledge_kind="pattern",
        content=content,
        command_id=f"p7-pattern-{command_suffix}",
        capability_id="cap_pg",
        problem_id="pr_pg",
        applicable_capability_ids=["cap_pg"],
    )
    await service.record_pattern_use(
        tenant_id=tenant_id,
        work_item_id="wi_pg",
        pattern_entry_id="kp_pg",
        command_id=f"p7-use-{command_suffix}",
    )
    recommendations = await service.recommend_patterns(
        tenant_id=tenant_id, capability_id="cap_pg", problem_id="pr_pg"
    )
    assert recommendations[0]["knowledge_entry_id"] == "kp_pg"
    assert any("uses_pattern_edges" in reason for reason in recommendations[0]["reasons"])


# ---------------------------------------------------------------------------
# EX4-R2-P3-S001 — Organizational Brain functional behaviours
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ex4_r2_p3_s001_query_organizational_knowledge_with_provenance() -> None:
    """Governed query returns current entries enriched with graph provenance edges."""
    svc = KernelService()
    await _seed(svc.ledger, svc.projection)

    # Two knowledge entries: one current, one superseded
    await svc.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_current",
        knowledge_kind="lesson_learned",
        content="Always use idempotent registration before barcodes",
        command_id="org-k1",
        capability_id="cap_1",
        problem_id="pr_1",
        confidence="high",
    )
    await svc.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_superseded",
        knowledge_kind="lesson_learned",
        content="Old approach — do not use",
        command_id="org-k2",
        capability_id="cap_1",
        problem_id="pr_1",
        superseded_by_entry_id="k_current",
    )
    # Tenant isolation — different tenant must not appear
    await svc.upsert_knowledge(
        tenant_id="t_other",
        knowledge_entry_id="k_other_tenant",
        knowledge_kind="lesson_learned",
        content="Should not appear",
        command_id="org-k3",
        capability_id="cap_1",
        problem_id="pr_1",
    )

    entries = await svc.query_organizational_knowledge(tenant_id="t1")
    entry_ids = {e["knowledge_entry_id"] for e in entries}
    assert "k_current" in entry_ids
    # Superseded entries still exist in knowledge store but superseded_by_entry_id
    # means knowledge_entry_is_current returns False → excluded from org query.
    assert "k_superseded" not in entry_ids
    assert "k_other_tenant" not in entry_ids

    # Confidence filter: medium floor must exclude unknown/low
    await svc.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_low_conf",
        knowledge_kind="lesson_learned",
        content="Low confidence entry",
        command_id="org-k4",
        capability_id="cap_1",
        problem_id="pr_1",
        confidence="low",
    )
    high_only = await svc.query_organizational_knowledge(
        tenant_id="t1", minimum_confidence="medium"
    )
    high_ids = {e["knowledge_entry_id"] for e in high_only}
    assert "k_current" in high_ids
    assert "k_low_conf" not in high_ids

    # Provenance edges — add an edge and verify it appears
    await svc.add_knowledge_graph_edge(
        tenant_id="t1",
        edge_type="DERIVED_FROM",
        from_type="knowledge",
        from_id="k_current",
        to_type="work_item",
        to_id="wi_1",
        command_id="org-edge-1",
    )
    entries_with_prov = await svc.query_organizational_knowledge(tenant_id="t1")
    current = next(e for e in entries_with_prov if e["knowledge_entry_id"] == "k_current")
    assert any(
        edge["from_id"] == "k_current" or edge["to_id"] == "k_current"
        for edge in current["provenance_edges"]
    )


@pytest.mark.asyncio
async def test_ex4_r2_p3_s001_detect_missing_knowledge() -> None:
    """Missing-knowledge detection correctly identifies absent required kinds."""
    svc = KernelService()
    await _seed(svc.ledger, svc.projection)

    # No knowledge at all → missing == True
    result_empty = await svc.detect_missing_knowledge(
        tenant_id="t1",
        capability_id="cap_1",
        required_kinds=["lesson_learned", "pattern"],
    )
    assert result_empty["missing"] is True
    assert result_empty["has_current_knowledge"] is False
    assert set(result_empty["missing_kinds"]) == {"lesson_learned", "pattern"}

    await svc.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_lesson",
        knowledge_kind="lesson_learned",
        content="Device registration must come first",
        command_id="mk-1",
        capability_id="cap_1",
        problem_id="pr_1",
    )

    # lesson_learned present; pattern still missing
    result_partial = await svc.detect_missing_knowledge(
        tenant_id="t1",
        capability_id="cap_1",
        required_kinds=["lesson_learned", "pattern"],
    )
    assert result_partial["has_current_knowledge"] is True
    assert result_partial["missing_kinds"] == ["pattern"]
    assert result_partial["missing"] is True

    await svc.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_pattern",
        knowledge_kind="pattern",
        content="Always idempotent",
        command_id="mk-2",
        capability_id="cap_1",
        problem_id="pr_1",
    )

    # Both present → no missing kinds
    result_full = await svc.detect_missing_knowledge(
        tenant_id="t1",
        capability_id="cap_1",
        required_kinds=["lesson_learned", "pattern"],
    )
    assert result_full["missing"] is False
    assert result_full["missing_kinds"] == []

    # Fail-closed: blank capability_id must raise
    from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError as _Err
    with pytest.raises(_Err, match="capability_id is required"):
        await svc.detect_missing_knowledge(tenant_id="t1", capability_id="")


@pytest.mark.asyncio
async def test_ex4_r2_p3_s001_reconcile_cross_project_knowledge() -> None:
    """Cross-project reconciliation groups identical-fingerprint entries from 2+ projects."""
    svc = KernelService()
    await _seed(svc.ledger, svc.projection)

    shared_content = "Device registration must precede barcode binding"
    for pid, kid in (("proj_alpha", "k_alpha"), ("proj_beta", "k_beta")):
        await svc.upsert_knowledge(
            tenant_id="t1",
            knowledge_entry_id=kid,
            knowledge_kind="lesson_learned",
            content=shared_content,
            command_id=f"xp-{kid}",
            capability_id="cap_1",
            problem_id="pr_1",
            project_id=pid,
        )

    groups = await svc.reconcile_cross_project_knowledge(tenant_id="t1")
    assert groups, "should detect the cross-project duplicate"
    group = groups[0]
    assert set(group["project_ids"]) == {"proj_alpha", "proj_beta"}
    assert set(group["knowledge_entry_ids"]) == {"k_alpha", "k_beta"}

    # A single-project entry must NOT appear in any group
    await svc.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_solo",
        knowledge_kind="lesson_learned",
        content="Unique content only in one project",
        command_id="xp-solo",
        capability_id="cap_1",
        problem_id="pr_1",
        project_id="proj_solo",
    )
    groups_after = await svc.reconcile_cross_project_knowledge(tenant_id="t1")
    solo_in_group = any("k_solo" in g["knowledge_entry_ids"] for g in groups_after)
    assert not solo_in_group


@pytest.mark.asyncio
async def test_ex4_r2_p3_s001_declare_and_resolve_knowledge_conflict() -> None:
    """Conflict declare→resolve lifecycle: edge created then superseded on resolution."""
    svc = KernelService()
    await _seed(svc.ledger, svc.projection)

    await svc.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_a",
        knowledge_kind="lesson_learned",
        content="Approach A — register first",
        command_id="conf-k-a",
        capability_id="cap_1",
        problem_id="pr_1",
    )
    await svc.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_b",
        knowledge_kind="lesson_learned",
        content="Approach B — scan first",
        command_id="conf-k-b",
        capability_id="cap_1",
        problem_id="pr_1",
    )

    # Declare conflict
    conflict_edge = await svc.declare_knowledge_conflict(
        tenant_id="t1",
        left_knowledge_id="k_a",
        right_knowledge_id="k_b",
        command_id="conf-declare-1",
        authority_context="architecture-review",
    )
    assert conflict_edge["edge_type"] == "CONFLICTS_WITH"
    assert conflict_edge["status"] == "ACTIVE"
    edge_id = conflict_edge["edge_id"]

    # Fail-closed: cannot declare conflict with same ID on both sides
    from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError as _Err
    with pytest.raises(_Err, match="conflict endpoints must differ"):
        await svc.declare_knowledge_conflict(
            tenant_id="t1",
            left_knowledge_id="k_a",
            right_knowledge_id="k_a",
            command_id="conf-declare-bad",
        )

    # Resolve: k_a wins; k_b becomes superseded
    resolution = await svc.resolve_knowledge_conflict(
        tenant_id="t1",
        conflict_edge_id=edge_id,
        authoritative_knowledge_id="k_a",
        superseded_knowledge_id="k_b",
        command_id="conf-resolve-1",
    )
    assert resolution["authoritative_knowledge_id"] == "k_a"
    assert resolution["superseded_knowledge_id"] == "k_b"
    # Conflict edge must now be SUPERSEDED
    edges = await svc.list_knowledge_graph_edges(
        tenant_id="t1", active_only=False
    )
    conflict = next(e for e in edges if e["edge_id"] == edge_id)
    assert conflict["status"] == "SUPERSEDED"
    # The losing entry must now point to the winner
    loser = svc.knowledge.get(tenant_id="t1", knowledge_entry_id="k_b")
    assert loser is not None
    assert loser.superseded_by_entry_id == "k_a"


@pytest.mark.asyncio
async def test_ex4_r2_p3_s001_http_surfaces() -> None:
    """All five EX4-R2-P3-S001 HTTP routes respond correctly end-to-end."""
    svc = KernelService()
    await _seed(svc.ledger, svc.projection)

    # Seed knowledge
    await svc.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_http_1",
        knowledge_kind="lesson_learned",
        content="Register devices before barcodes (HTTP test)",
        command_id="http-k1",
        capability_id="cap_1",
        problem_id="pr_1",
        project_id="proj_a",
        confidence="medium",
    )
    await svc.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_http_2",
        knowledge_kind="lesson_learned",
        content="Register devices before barcodes (HTTP test)",
        command_id="http-k2",
        capability_id="cap_1",
        problem_id="pr_1",
        project_id="proj_b",
        confidence="medium",
    )
    await svc.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_http_conf_a",
        knowledge_kind="technical_knowledge",
        content="Conflicting technical decision A",
        command_id="http-k-ca",
        capability_id="cap_1",
        problem_id="pr_1",
    )
    await svc.upsert_knowledge(
        tenant_id="t1",
        knowledge_entry_id="k_http_conf_b",
        knowledge_kind="technical_knowledge",
        content="Conflicting technical decision B",
        command_id="http-k-cb",
        capability_id="cap_1",
        problem_id="pr_1",
    )

    app = FastAPI()
    app.state.execution_kernel_service = svc
    app.state.execution_kernel_require_gateway = False
    app.include_router(execution_kernel_router)
    transport = ASGITransport(app=app)
    headers = {"X-Tenant-ID": "t1"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET /organizational-knowledge
        org_resp = await client.get(
            "/aix/execution-kernel/organizational-knowledge",
            headers=headers,
            params={"capability_id": "cap_1"},
        )
        assert org_resp.status_code == 200, org_resp.text
        org_body = org_resp.json()
        assert org_body["ok"] is True
        assert org_body["count"] >= 2
        assert all("provenance_edges" in e for e in org_body["entries"])

        # 2. GET /organizational-knowledge/missing
        missing_resp = await client.get(
            "/aix/execution-kernel/organizational-knowledge/missing",
            headers=headers,
            params={"capability_id": "cap_1", "required_kinds": "lesson_learned,pattern"},
        )
        assert missing_resp.status_code == 200, missing_resp.text
        missing_body = missing_resp.json()
        assert missing_body["ok"] is True
        assert missing_body["has_current_knowledge"] is True
        assert "pattern" in missing_body["missing_kinds"]

        # 3. GET /organizational-knowledge/cross-project
        cross_resp = await client.get(
            "/aix/execution-kernel/organizational-knowledge/cross-project",
            headers=headers,
        )
        assert cross_resp.status_code == 200, cross_resp.text
        cross_body = cross_resp.json()
        assert cross_body["ok"] is True
        assert cross_body["count"] >= 1
        group = cross_body["groups"][0]
        assert "proj_a" in group["project_ids"] and "proj_b" in group["project_ids"]

        # 4. POST /knowledge/conflicts — declare
        declare_resp = await client.post(
            "/aix/execution-kernel/knowledge/conflicts",
            headers=headers,
            json={
                "left_knowledge_id": "k_http_conf_a",
                "right_knowledge_id": "k_http_conf_b",
                "command_id": "http-conf-declare-1",
                "authority_context": "architecture-review",
            },
        )
        assert declare_resp.status_code == 200, declare_resp.text
        conflict_edge = declare_resp.json()["conflict_edge"]
        assert conflict_edge["edge_type"] == "CONFLICTS_WITH"
        assert conflict_edge["status"] == "ACTIVE"
        conflict_edge_id = conflict_edge["edge_id"]

        # 5. POST /knowledge/conflicts/{edge_id}/resolve
        resolve_resp = await client.post(
            f"/aix/execution-kernel/knowledge/conflicts/{conflict_edge_id}/resolve",
            headers=headers,
            json={
                "authoritative_knowledge_id": "k_http_conf_a",
                "superseded_knowledge_id": "k_http_conf_b",
                "command_id": "http-conf-resolve-1",
            },
        )
        assert resolve_resp.status_code == 200, resolve_resp.text
        resolve_body = resolve_resp.json()
        assert resolve_body["ok"] is True
        assert resolve_body["authoritative_knowledge_id"] == "k_http_conf_a"
        assert resolve_body["superseded_knowledge_id"] == "k_http_conf_b"
