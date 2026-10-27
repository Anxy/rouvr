"""EX2-S006: Postgres-backed KernelService + bootstrap attach."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse
import structlog

from backend.aix_core.runtime.objective.ulid_compat import new_ulid
from backend.aix_core.runtime.executor.execution_kernel_adapter.adapters import (
    AdapterOutcome,
    DecisionVerdict,
    DispatchRequest,
    build_dispatch_request,
    distill_knowledge_proposals,
    map_adapter_outcome_without_verification,
    verify_adapter_outcome,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.cycle_adapter import (
    AdapterHandle,
    ObjectiveCycleExecutorAdapter,
)
from backend.infrastructure.postgres.execution_kernel.db_pool import acquire_pooled_connection
from backend.aix_core.runtime.executor.execution_kernel_adapter.bootstrap import BootstrapSpec, bootstrap_problem_graph
from backend.aix_core.runtime.executor.problem_loop import ProblemStepMode, ProblemStepResult, run_problem_step
from backend.aix_core.runtime.executor.proposals import (
    ProposalLevel,
    open_proposal_dicts,
    raise_proposal,
    resolve_proposal,
)
from backend.aix_core.runtime.objective.api_backend import ObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.checkpoint import ResumeResult
from backend.aix_core.runtime.executor.capability_registry import (
    InMemoryCapabilityRegistry,
    RegisteredCapability,
    registered_capability_to_dict,
)
from backend.aix_core.runtime.executor.context_builder import (
    ContextBuilder,
    ContextCapsule,
)
from backend.aix_core.runtime.executor.dependency_graph import InMemoryDependencyGraph
from backend.infrastructure.postgres.execution_kernel.postgres_stores import (
    PostgresCheckpointStore,
    PostgresCapabilityRegistry,
    PostgresDependencyGraph,
    PostgresKnowledgeGraphStore,
    PostgresKnowledgeStore,
    PostgresProjectionStore,
)
from backend.infrastructure.postgres.execution_kernel.postgres_intelligence_stores import (
    PostgresEnterpriseStore,
    PostgresExecutionRouteStore,
    PostgresLearningStore,
    PostgresPlanningStore,
    PostgresReasoningStore,
    PostgresSemanticStore,
)
from backend.aix_core.runtime.executor.projection import InMemoryProjectionStore, ProjectionRow
from backend.infrastructure.postgres.execution_kernel.repository import (
    ExecutionEvent,
    ExecutionKernelRepository,
    ExecutionKernelRepositoryError,
    resolve_test_database_url,
)
from backend.infrastructure.postgres.execution_kernel.schema import ACTOR_AUDIT_TABLE, ensure_full_execution_kernel_schema
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import (
    KernelService,
    _handle_dict,
    _knowledge_dict,
    _outcome_dict,
)
from backend.aix_core.runtime.executor.knowledge_graph import InMemoryKnowledgeGraph, edge_to_dict
from backend.aix_core.runtime.executor.pattern_engine import (
    candidate_to_dict,
    discover_pattern_candidates,
    recommend_patterns,
    recommendation_to_dict,
)
from backend.aix_core.runtime.executor.organizational_knowledge import (
    detect_missing_knowledge,
    list_stale_knowledge,
    query_organizational_knowledge,
    reconcile_cross_project_knowledge,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.model_gateway import resolve_kernel_model_routes
from backend.aix_core.runtime.executor.execution_route import (
    ExecutionRouteContract,
    contract_from_model_routes,
    model_routes_payload,
)
from backend.aix_core.runtime.executor.learning_lifecycle import learning_record_to_dict
from backend.aix_core.runtime.executor.planning_engine import (
    autonomy_decision_to_dict,
    bounded_autonomy_for_mutation,
    build_plan_mutation_proposal_payload,
    decompose_plan,
    evaluate_plan_quality,
)
from backend.aix_core.runtime.executor.enterprise_intelligence import (
    build_portfolio_intelligence_view,
    enterprise_scenarios,
    start_governed_improvement_cycle_async,
)

logger = structlog.get_logger("hyeve_aix_service.execution_kernel.bootstrap")


def _as_asyncpg_url(database_url: str) -> str:
    return (
        database_url.replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgres+asyncpg://", "postgresql://")
    )


def _remap_docker_db_host(database_url: str) -> str:
    raw = _as_asyncpg_url(database_url)
    parsed = urlparse(raw)
    if parsed.hostname not in {"db", "postgres", "postgresql"}:
        return raw
    host = "127.0.0.1"
    netloc = f"{parsed.username}:{parsed.password}@{host}:{parsed.port or 5432}"
    return urlunparse(parsed._replace(netloc=netloc))


@dataclass
class PostgresKernelService:
    """Kernel API backed by Postgres ledger + projection/deps/checkpoints/knowledge."""

    database_url: str
    mode: str = "postgres"

    def __post_init__(self) -> None:
        url = _remap_docker_db_host(self.database_url)
        self.database_url = url
        self.ledger = ExecutionKernelRepository(url, ensure_schema=True)
        self.projection = PostgresProjectionStore(url, ensure_schema=True)
        self.dependencies = PostgresDependencyGraph(url, ensure_schema=True)
        self.checkpoints = PostgresCheckpointStore(url, ensure_schema=True)
        self.capability_registry = PostgresCapabilityRegistry(url, ensure_schema=True)
        self.knowledge = PostgresKnowledgeStore(url, ensure_schema=True)
        self.knowledge_graph = PostgresKnowledgeGraphStore(url, ensure_schema=True)
        # AIX-EXECUTION-006-S1: durable Postgres-backed route store — survives restart.
        self.execution_routes = PostgresExecutionRouteStore(url, ensure_schema=True)
        # EX5-S1: P8–P12 durable intelligence stores
        self.learning_lifecycle = PostgresLearningStore(url, ensure_schema=True)
        self.semantic = PostgresSemanticStore(url, ensure_schema=True)
        self.planning = PostgresPlanningStore()
        self.reasoning = PostgresReasoningStore()
        self.enterprise = PostgresEnterpriseStore(url, ensure_schema=True)
        self.enterprise_audit = self.enterprise

    async def ensure_schema(self) -> None:
        conn = await acquire_pooled_connection(
            self.database_url,
            ensure_schema=True,
            ensure_schema_fn=ensure_full_execution_kernel_schema,
        )
        await conn.close()

    async def append_and_project(
        self,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        expected_sequence_number: int,
        event_type: str,
        command_id: str,
        payload: dict[str, Any],
    ) -> ExecutionEvent:
        event = await self.ledger.append_event(
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            expected_sequence_number=expected_sequence_number,
            event_type=event_type,
            command_id=command_id,
            payload=payload,
        )
        latest = event
        if event.replayed:
            loaded = await self.ledger.latest_snapshot(
                tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id
            )
            if loaded is not None:
                latest = loaded
        await self.projection.apply_event(latest)
        return event

    async def get_projection(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> dict[str, Any] | None:
        row = await self.projection.get(
            tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id
        )
        if row is None:
            return None
        return {
            "tenant_id": row.tenant_id,
            "aggregate_type": row.aggregate_type,
            "aggregate_id": row.aggregate_id,
            "sequence_number": row.sequence_number,
            "status": row.status,
            "parent_id": row.parent_id,
            "payload": row.payload,
        }

    async def verify_projection_replay(
        self,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        declared_sequence_number: int | None = None,
    ) -> dict[str, Any]:
        """EX5-S3 (closes a known EX3-S006 gap): the Postgres-backed
        equivalent of KernelService.verify_projection_replay — independently
        reconstruct this aggregate's state by folding its full event history
        (read from the real durable ledger) into a fresh, throwaway
        in-memory projection, then compare against the live
        PostgresProjectionStore. This is the replay half of restart/recovery
        proof: a fresh process must be able to prove its own live state
        against the ledger, not just trust the projection blindly."""
        replay_store = InMemoryProjectionStore()
        replayed = await replay_store.rebuild_from_ledger(
            self.ledger, tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id
        )
        live = await self.projection.get(tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id)

        if replayed is None and live is None:
            return {"match": True, "replayed_sequence_number": None, "live_sequence_number": None, "mismatch_reason": None}

        if replayed is None or live is None:
            return {
                "match": False,
                "replayed_sequence_number": replayed.sequence_number if replayed else None,
                "live_sequence_number": live.sequence_number if live else None,
                "mismatch_reason": "one side has no rows for this aggregate and the other does",
            }

        mismatch_reason = None
        if replayed.sequence_number != live.sequence_number:
            mismatch_reason = "sequence_number differs between replayed and live projection"
        elif replayed.status != live.status:
            mismatch_reason = "status differs between replayed and live projection"
        elif replayed.payload != live.payload:
            mismatch_reason = "payload differs between replayed and live projection"
        elif declared_sequence_number is not None and declared_sequence_number != replayed.sequence_number:
            mismatch_reason = (
                f"declared_sequence_number={declared_sequence_number} does not match "
                f"replayed sequence_number={replayed.sequence_number}"
            )

        return {
            "match": mismatch_reason is None,
            "replayed_sequence_number": replayed.sequence_number,
            "live_sequence_number": live.sequence_number,
            "mismatch_reason": mismatch_reason,
        }

    async def add_dependency(
        self, *, tenant_id: str, work_item_id: str, depends_on_work_item_id: str, command_id: str
    ) -> dict[str, Any]:
        edge = await self.dependencies.add_dependency(
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            depends_on_work_item_id=depends_on_work_item_id,
            command_id=command_id,
        )
        return {
            "event_id": edge.event_id,
            "tenant_id": edge.tenant_id,
            "work_item_id": edge.work_item_id,
            "depends_on_work_item_id": edge.depends_on_work_item_id,
            "event_type": edge.event_type,
            "command_id": edge.command_id,
        }

    async def next_actionable(self, *, tenant_id: str, problem_id: str) -> list[str]:
        return await self.dependencies.next_actionable_work_items(
            tenant_id=tenant_id, problem_id=problem_id, projection=self.projection
        )

    async def list_open_proposals(self, *, tenant_id: str, problem_id: str) -> list[dict[str, Any]]:
        rows = await self.projection.list_children(
            tenant_id=tenant_id, aggregate_type="proposal", parent_id=problem_id
        )
        return open_proposal_dicts(
            [
                {
                    "aggregate_id": r.aggregate_id,
                    "status": r.status,
                    "payload": r.payload,
                }
                for r in rows
            ]
        )

    async def capture_checkpoint(
        self,
        *,
        tenant_id: str,
        problem_id: str,
        repository_state: dict[str, Any],
        open_proposals: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        proposals = open_proposals
        if proposals is None:
            proposals = await self.list_open_proposals(tenant_id=tenant_id, problem_id=problem_id)
        cp = await self.checkpoints.capture(
            tenant_id=tenant_id,
            problem_id=problem_id,
            projection=self.projection,
            repository_state=repository_state,
            open_proposals=proposals,
        )
        return {
            "checkpoint_id": cp.checkpoint_id,
            "tenant_id": cp.tenant_id,
            "problem_id": cp.problem_id,
            "status": cp.status,
            "work_items": cp.work_items,
            "open_proposals": cp.open_proposals,
            "repository_state": cp.repository_state,
            "created_at": cp.created_at,
        }

    async def bootstrap_graph(
        self, *, tenant_id: str, spec: BootstrapSpec, idempotency_key: str
    ) -> dict[str, Any]:
        return await bootstrap_problem_graph(
            self, tenant_id=tenant_id, spec=spec, idempotency_key=idempotency_key
        )

    async def raise_proposal(
        self,
        *,
        tenant_id: str,
        problem_id: str,
        reason: str,
        command_id: str,
        level: ProposalLevel = 2,
        work_item_id: str | None = None,
        affected_capabilities: list[str] | None = None,
        estimated_work_item_delta: int = 0,
        risk: str = "low",
        proposal_id: str | None = None,
        requests_new_capability: bool = False,
        charter_change: bool = False,
    ) -> dict[str, Any]:
        return await raise_proposal(
            self,
            tenant_id=tenant_id,
            problem_id=problem_id,
            reason=reason,
            command_id=command_id,
            level=level,
            work_item_id=work_item_id,
            affected_capabilities=affected_capabilities,
            estimated_work_item_delta=estimated_work_item_delta,
            risk=risk,  # type: ignore[arg-type]
            proposal_id=proposal_id,
            requests_new_capability=requests_new_capability,
            charter_change=charter_change,
        )

    async def resolve_proposal(
        self,
        *,
        tenant_id: str,
        proposal_id: str,
        command_id: str,
        decision: str = "RESOLVED",
    ) -> dict[str, Any]:
        return await resolve_proposal(
            self,
            tenant_id=tenant_id,
            proposal_id=proposal_id,
            command_id=command_id,
            decision=decision,  # type: ignore[arg-type]
        )

    async def resume(self, *, tenant_id: str, problem_id: str) -> ResumeResult:
        checkpoint = await self.checkpoints.current(tenant_id=tenant_id, problem_id=problem_id)
        next_ids = await self.next_actionable(tenant_id=tenant_id, problem_id=problem_id)
        watermark = await self.projection.watermark(tenant_id=tenant_id)
        if checkpoint is not None:
            return ResumeResult(
                problem_id=problem_id,
                checkpoint_id=checkpoint.checkpoint_id,
                work_items=list(checkpoint.work_items),
                next_actionable=next_ids,
                projection_watermark=watermark,
                source="checkpoint",
            )
        children = await self.projection.list_children(
            tenant_id=tenant_id, aggregate_type="work_item", parent_id=problem_id
        )
        work_items = [
            {"work_item_id": r.aggregate_id, "status": r.status, "sequence_number": r.sequence_number}
            for r in children
        ]
        return ResumeResult(
            problem_id=problem_id,
            checkpoint_id=None,
            work_items=work_items,
            next_actionable=next_ids,
            projection_watermark=watermark,
            source="projection_only",
        )

    async def _hydrate_memory_for_work_item(self, *, tenant_id: str, work_item_id: str) -> tuple[InMemoryProjectionStore, InMemoryDependencyGraph]:
        mem_proj = InMemoryProjectionStore()
        mem_deps = InMemoryDependencyGraph()
        wi = await self.projection.get(tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=work_item_id)
        if wi is None:
            raise ExecutionKernelRepositoryError(f"work_item not found: {work_item_id}")
        chain: list[ProjectionRow] = [wi]
        problem_id = wi.parent_id or ""
        if problem_id:
            problem = await self.projection.get(tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id)
            if problem:
                chain.append(problem)
                cap_id = problem.parent_id or ""
                if cap_id:
                    cap = await self.projection.get(tenant_id=tenant_id, aggregate_type="capability", aggregate_id=cap_id)
                    if cap:
                        chain.append(cap)
                        proj_id = cap.parent_id or ""
                        if proj_id:
                            project = await self.projection.get(
                                tenant_id=tenant_id, aggregate_type="project", aggregate_id=proj_id
                            )
                            if project:
                                chain.append(project)
        for row in chain:
            mem_proj._rows[(row.tenant_id, row.aggregate_type, row.aggregate_id)] = row  # noqa: SLF001
        # Hydrate dependency targets as projection rows for status.
        dep_ids = await self.dependencies.active_dependencies(tenant_id=tenant_id, work_item_id=work_item_id)
        for dep_id in dep_ids:
            dep = await self.projection.get(tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=dep_id)
            if dep:
                mem_proj._rows[(dep.tenant_id, dep.aggregate_type, dep.aggregate_id)] = dep  # noqa: SLF001
            mem_deps.add_dependency(
                tenant_id=tenant_id,
                work_item_id=work_item_id,
                depends_on_work_item_id=dep_id,
                command_id=f"hydrate:{work_item_id}:{dep_id}",
            )
        return mem_proj, mem_deps

    async def build_capsule(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        max_tokens: int = 4000,
        repository: dict[str, Any] | None = None,
    ) -> ContextCapsule:
        mem_proj, mem_deps = await self._hydrate_memory_for_work_item(
            tenant_id=tenant_id, work_item_id=work_item_id
        )
        mem_knowledge = await self.knowledge.materialize(tenant_id=tenant_id)
        builder = ContextBuilder(projection=mem_proj, dependencies=mem_deps, knowledge=mem_knowledge)
        return builder.build(
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            max_tokens=max_tokens,
            repository=repository,
        )

    async def upsert_knowledge(
        self,
        *,
        tenant_id: str,
        knowledge_entry_id: str,
        knowledge_kind: str,
        content: str,
        command_id: str,
        capability_id: str | None = None,
        problem_id: str | None = None,
        project_id: str | None = None,
        applicable_capability_ids: list[str] | None = None,
        archive: bool = False,
        valid_from: str | None = None,
        valid_until: str | None = None,
        superseded_by_entry_id: str | None = None,
        confidence: str | None = None,
        review_due_at: str | None = None,
    ) -> dict[str, Any]:
        current = await self.knowledge.get_current(
            tenant_id=tenant_id, knowledge_entry_id=knowledge_entry_id
        )
        if archive:
            entry = await self.knowledge.archive(
                tenant_id=tenant_id, knowledge_entry_id=knowledge_entry_id, command_id=command_id
            )
        elif current is None:
            entry = await self.knowledge.create(
                tenant_id=tenant_id,
                knowledge_entry_id=knowledge_entry_id,
                knowledge_kind=knowledge_kind,
                content=content,
                command_id=command_id,
                capability_id=capability_id,
                problem_id=problem_id,
                project_id=project_id,
                applicable_capability_ids=applicable_capability_ids or (),
                valid_from=valid_from,
                valid_until=valid_until,
                superseded_by_entry_id=superseded_by_entry_id,
                confidence=confidence or "unknown",
                review_due_at=review_due_at,
            )
        else:
            entry = await self.knowledge.update(
                tenant_id=tenant_id,
                knowledge_entry_id=knowledge_entry_id,
                content=content,
                command_id=command_id,
                knowledge_kind=knowledge_kind,
                capability_id=capability_id,
                problem_id=problem_id,
                project_id=project_id,
                applicable_capability_ids=applicable_capability_ids,
                valid_from=valid_from,
                valid_until=valid_until,
                superseded_by_entry_id=superseded_by_entry_id,
                confidence=confidence,
                review_due_at=review_due_at,
            )
        if superseded_by_entry_id:
            await self.add_knowledge_graph_edge(
                tenant_id=tenant_id,
                edge_type="SUPERSEDES",
                from_type="knowledge",
                from_id=superseded_by_entry_id,
                to_type="knowledge",
                to_id=knowledge_entry_id,
                command_id=f"{command_id}:supersedes",
                provenance=(knowledge_entry_id,),
            )
        return _knowledge_dict(entry)

    async def add_knowledge_graph_edge(
        self,
        *,
        tenant_id: str,
        edge_type: str,
        from_type: str,
        from_id: str,
        to_type: str,
        to_id: str,
        command_id: str,
        authority_context: str | None = None,
        provenance: list[str] | tuple[str, ...] | None = None,
        supersede_matching_active: bool = False,
    ) -> dict[str, Any]:
        edge = await self.knowledge_graph.add_edge(
            tenant_id=tenant_id,
            edge_type=edge_type,
            from_type=from_type,
            from_id=from_id,
            to_type=to_type,
            to_id=to_id,
            command_id=command_id,
            authority_context=authority_context,
            provenance=provenance,
            supersede_matching_active=supersede_matching_active,
        )
        return edge_to_dict(edge)

    async def get_knowledge_graph_edges(
        self,
        *,
        tenant_id: str,
        edge_type: str | None = None,
        from_type: str | None = None,
        from_id: str | None = None,
        to_type: str | None = None,
        to_id: str | None = None,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        edges = await self.knowledge_graph.get_edges(
            tenant_id=tenant_id,
            edge_type=edge_type,
            from_type=from_type,
            from_id=from_id,
            to_type=to_type,
            to_id=to_id,
            active_only=active_only,
        )
        return [edge_to_dict(edge) for edge in edges]

    async def list_knowledge_graph_edges(self, **filters: Any) -> list[dict[str, Any]]:
        return await self.get_knowledge_graph_edges(**filters)

    async def traverse_knowledge_graph(
        self,
        *,
        tenant_id: str,
        start_type: str,
        start_id: str,
        edge_types: list[str] | None = None,
        direction: str = "out",
        max_depth: int = 2,
    ) -> list[dict[str, Any]]:
        return await self.knowledge_graph.traverse(
            tenant_id=tenant_id,
            start_type=start_type,
            start_id=start_id,
            edge_types=edge_types,
            direction=direction,
            max_depth=max_depth,
        )

    async def _materialize_organizational_knowledge(
        self, *, tenant_id: str
    ) -> tuple[Any, InMemoryKnowledgeGraph]:
        knowledge = await self.knowledge.materialize(tenant_id=tenant_id)
        edges = await self.knowledge_graph.get_edges(tenant_id=tenant_id, active_only=False)
        return knowledge, InMemoryKnowledgeGraph(_edges=edges)

    async def query_organizational_knowledge(
        self,
        *,
        tenant_id: str,
        capability_id: str | None = None,
        project_id: str | None = None,
        minimum_confidence: str = "unknown",
        include_stale: bool = False,
    ) -> list[dict[str, Any]]:
        knowledge, graph = await self._materialize_organizational_knowledge(
            tenant_id=tenant_id
        )
        return query_organizational_knowledge(
            knowledge,
            graph,
            tenant_id=tenant_id,
            capability_id=capability_id,
            project_id=project_id,
            minimum_confidence=minimum_confidence,
            include_stale=include_stale,
        )

    async def detect_missing_knowledge(
        self,
        *,
        tenant_id: str,
        capability_id: str,
        required_kinds: list[str] | None = None,
    ) -> dict[str, Any]:
        knowledge = await self.knowledge.materialize(tenant_id=tenant_id)
        return detect_missing_knowledge(
            knowledge,
            tenant_id=tenant_id,
            capability_id=capability_id,
            required_kinds=required_kinds,
        )

    async def list_stale_knowledge(
        self, *, tenant_id: str, capability_id: str | None = None
    ) -> list[dict[str, Any]]:
        knowledge = await self.knowledge.materialize(tenant_id=tenant_id)
        return list_stale_knowledge(knowledge, tenant_id=tenant_id, capability_id=capability_id)

    async def revalidate_knowledge(
        self,
        *,
        tenant_id: str,
        knowledge_entry_id: str,
        command_id: str,
        valid_until: str | None = None,
        review_due_at: str | None = None,
        confidence: str | None = None,
    ) -> dict[str, Any]:
        knowledge = await self.knowledge.materialize(tenant_id=tenant_id)
        existing = knowledge.get(tenant_id=tenant_id, knowledge_entry_id=knowledge_entry_id)
        if existing is None:
            raise ExecutionKernelRepositoryError(f"knowledge entry not found: {knowledge_entry_id}")
        return await self.upsert_knowledge(
            tenant_id=tenant_id,
            knowledge_entry_id=knowledge_entry_id,
            knowledge_kind=existing.knowledge_kind,
            content=existing.content,
            command_id=command_id,
            capability_id=existing.capability_id,
            problem_id=existing.problem_id,
            project_id=existing.project_id,
            applicable_capability_ids=list(existing.applicable_capability_ids),
            valid_until=valid_until,
            review_due_at=review_due_at,
            confidence=confidence if confidence is not None else existing.confidence,
        )

    async def reconcile_cross_project_knowledge(
        self, *, tenant_id: str
    ) -> list[dict[str, Any]]:
        knowledge = await self.knowledge.materialize(tenant_id=tenant_id)
        return reconcile_cross_project_knowledge(knowledge, tenant_id=tenant_id)

    async def declare_knowledge_conflict(
        self,
        *,
        tenant_id: str,
        left_knowledge_id: str,
        right_knowledge_id: str,
        command_id: str,
        authority_context: str | None = None,
    ) -> dict[str, Any]:
        if left_knowledge_id == right_knowledge_id:
            raise ExecutionKernelRepositoryError("conflict endpoints must differ")
        for knowledge_id in (left_knowledge_id, right_knowledge_id):
            if await self.knowledge.get_current(
                tenant_id=tenant_id, knowledge_entry_id=knowledge_id
            ) is None:
                raise ExecutionKernelRepositoryError(f"knowledge entry not found: {knowledge_id}")
        return await self.add_knowledge_graph_edge(
            tenant_id=tenant_id,
            edge_type="CONFLICTS_WITH",
            from_type="knowledge",
            from_id=left_knowledge_id,
            to_type="knowledge",
            to_id=right_knowledge_id,
            command_id=command_id,
            authority_context=authority_context,
            provenance=(left_knowledge_id, right_knowledge_id),
        )

    async def resolve_knowledge_conflict(
        self,
        *,
        tenant_id: str,
        conflict_edge_id: str,
        authoritative_knowledge_id: str,
        superseded_knowledge_id: str,
        command_id: str,
    ) -> dict[str, Any]:
        conflict = await self.knowledge_graph.get_edge(
            tenant_id=tenant_id, edge_id=conflict_edge_id
        )
        if conflict is None or conflict.edge_type != "CONFLICTS_WITH":
            raise ExecutionKernelRepositoryError(f"active conflict edge not found: {conflict_edge_id}")
        if {authoritative_knowledge_id, superseded_knowledge_id} != {
            conflict.from_id,
            conflict.to_id,
        }:
            raise ExecutionKernelRepositoryError("resolution ids must match conflict endpoints")
        superseded = await self.knowledge.get_current(
            tenant_id=tenant_id, knowledge_entry_id=superseded_knowledge_id
        )
        if superseded is None:
            raise ExecutionKernelRepositoryError(
                f"knowledge entry not found: {superseded_knowledge_id}"
            )
        await self.upsert_knowledge(
            tenant_id=tenant_id,
            knowledge_entry_id=superseded.knowledge_entry_id,
            knowledge_kind=superseded.knowledge_kind,
            content=superseded.content,
            command_id=f"{command_id}:knowledge",
            capability_id=superseded.capability_id,
            problem_id=superseded.problem_id,
            project_id=superseded.project_id,
            applicable_capability_ids=list(superseded.applicable_capability_ids),
            valid_from=superseded.valid_from,
            valid_until=superseded.valid_until,
            superseded_by_entry_id=authoritative_knowledge_id,
            confidence=superseded.confidence,
            review_due_at=superseded.review_due_at,
        )
        resolved = await self.knowledge_graph.supersede_edge(
            tenant_id=tenant_id, edge_id=conflict_edge_id
        )
        return {
            "conflict": edge_to_dict(resolved),
            "authoritative_knowledge_id": authoritative_knowledge_id,
            "superseded_knowledge_id": superseded_knowledge_id,
        }

    async def register_capability(
        self,
        *,
        tenant_id: str,
        capability_key: str,
        version: str,
        display_name: str,
        description: str = "",
        input_contract: dict[str, Any] | None = None,
        output_contract: dict[str, Any] | None = None,
        authority_requirements: dict[str, Any] | None = None,
        executor_binding: str = "",
        verification_method: str = "",
        dependencies: list[str] | None = None,
        cost_class: str = "medium",
        risk_class: str = "medium",
        status: str = "ACTIVE",
    ) -> dict[str, Any]:
        entry = await self.capability_registry.register(
            RegisteredCapability(
                capability_key=capability_key,
                version=version,
                tenant_id=tenant_id,
                display_name=display_name,
                description=description,
                input_contract=dict(input_contract or {}),
                output_contract=dict(output_contract or {}),
                authority_requirements=dict(authority_requirements or {}),
                executor_binding=executor_binding,
                verification_method=verification_method,
                dependencies=tuple(dependencies or ()),
                cost_class=cost_class,
                risk_class=risk_class,
                status=status,
            )
        )
        return registered_capability_to_dict(entry)

    async def get_registered_capability(
        self, *, tenant_id: str, capability_key: str, version: str | None = None
    ) -> dict[str, Any] | None:
        entry = await self.capability_registry.get(
            tenant_id=tenant_id, capability_key=capability_key, version=version
        )
        return registered_capability_to_dict(entry) if entry else None

    async def list_registered_capabilities(
        self, *, tenant_id: str, active_only: bool = True, capability_key: str | None = None
    ) -> list[dict[str, Any]]:
        entries = await self.capability_registry.list(
            tenant_id=tenant_id, active_only=active_only, capability_key=capability_key
        )
        return [registered_capability_to_dict(entry) for entry in entries]

    async def resolve_capability_dependencies(
        self, *, tenant_id: str, capability_key: str, version: str | None = None
    ) -> list[dict[str, Any]]:
        entries = await self.capability_registry.resolve_dependencies(
            tenant_id=tenant_id, capability_key=capability_key, version=version
        )
        return [registered_capability_to_dict(entry) for entry in entries]

    async def discover_patterns(
        self, *, tenant_id: str, min_occurrences: int = 2, include_lessons: bool = True
    ) -> list[dict[str, Any]]:
        knowledge = await self.knowledge.materialize(tenant_id=tenant_id)
        return [
            candidate_to_dict(candidate)
            for candidate in discover_pattern_candidates(
                knowledge,
                tenant_id=tenant_id,
                min_occurrences=min_occurrences,
                include_lessons=include_lessons,
            )
        ]

    async def recommend_patterns(
        self,
        *,
        tenant_id: str,
        capability_id: str,
        problem_id: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        knowledge = await self.knowledge.materialize(tenant_id=tenant_id)
        edges = await self.knowledge_graph.get_edges(tenant_id=tenant_id, active_only=True)
        graph = InMemoryKnowledgeGraph(_edges=edges)
        return [
            recommendation_to_dict(recommendation)
            for recommendation in recommend_patterns(
                knowledge,
                graph,
                tenant_id=tenant_id,
                capability_id=capability_id,
                problem_id=problem_id,
                limit=limit,
            )
        ]

    async def record_pattern_use(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        pattern_entry_id: str,
        command_id: str,
    ) -> dict[str, Any]:
        return await self.add_knowledge_graph_edge(
            tenant_id=tenant_id,
            edge_type="USES_PATTERN",
            from_type="work_item",
            from_id=work_item_id,
            to_type="knowledge",
            to_id=pattern_entry_id,
            command_id=command_id,
            provenance=(pattern_entry_id,),
        )

    # --- EX5-S1: P8–P12 Postgres parity ---

    async def observe_learning(
        self,
        *,
        tenant_id: str,
        summary: str,
        source_work_item_id: str | None = None,
        source_knowledge_ids: list[str] | None = None,
        capability_id: str | None = None,
        problem_id: str | None = None,
    ) -> dict[str, Any]:
        rec = await self.learning_lifecycle.observe(
            tenant_id=tenant_id,
            summary=summary,
            source_work_item_id=source_work_item_id,
            source_knowledge_ids=source_knowledge_ids,
            capability_id=capability_id,
            problem_id=problem_id,
        )
        return learning_record_to_dict(rec)

    async def advance_learning(
        self, *, tenant_id: str, learning_id: str, to_stage: str
    ) -> dict[str, Any]:
        return learning_record_to_dict(
            await self.learning_lifecycle.advance(
                tenant_id=tenant_id, learning_id=learning_id, to_stage=to_stage
            )
        )

    async def classify_learning_scope(
        self, *, tenant_id: str, learning_id: str, scope: str
    ) -> dict[str, Any]:
        return learning_record_to_dict(
            await self.learning_lifecycle.classify_scope(
                tenant_id=tenant_id, learning_id=learning_id, scope=scope
            )
        )

    async def set_learning_confidence(
        self, *, tenant_id: str, learning_id: str, confidence: str
    ) -> dict[str, Any]:
        return learning_record_to_dict(
            await self.learning_lifecycle.set_confidence(
                tenant_id=tenant_id, learning_id=learning_id, confidence=confidence
            )
        )

    async def approve_learning(
        self,
        *,
        tenant_id: str,
        learning_id: str,
        actor_id: str,
        proposal_level: int,
        proposal_id: str | None = None,
        promote_to_knowledge: bool = False,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        knowledge_id = None
        rec = await self.learning_lifecycle.get(tenant_id=tenant_id, learning_id=learning_id)
        if rec is None:
            raise ExecutionKernelRepositoryError(f"learning record not found: {learning_id}")
        if promote_to_knowledge:
            knowledge_id = f"kl_lrn_{learning_id[-8:]}"
            await self.upsert_knowledge(
                tenant_id=tenant_id,
                knowledge_entry_id=knowledge_id,
                knowledge_kind="lesson_learned",
                content=rec.summary,
                command_id=command_id or f"lrn-approve:{learning_id}",
                capability_id=rec.capability_id,
                problem_id=rec.problem_id,
                confidence=rec.confidence
                if rec.confidence in {"unknown", "low", "medium", "high"}
                else "unknown",
            )
            await self.semantic.upsert_embedding(
                tenant_id=tenant_id,
                knowledge_entry_id=knowledge_id,
                content=rec.summary,
            )
        approved = await self.learning_lifecycle.approve_activation(
            tenant_id=tenant_id,
            learning_id=learning_id,
            actor_id=actor_id,
            proposal_level=proposal_level,
            proposal_id=proposal_id,
            active_knowledge_entry_id=knowledge_id,
        )
        return learning_record_to_dict(approved)

    async def reject_learning(self, *, tenant_id: str, learning_id: str) -> dict[str, Any]:
        return learning_record_to_dict(
            await self.learning_lifecycle.reject(tenant_id=tenant_id, learning_id=learning_id)
        )

    async def list_learning(
        self, *, tenant_id: str, stage: str | None = None, active_only: bool = False
    ) -> list[dict[str, Any]]:
        return [
            learning_record_to_dict(r)
            for r in await self.learning_lifecycle.list(
                tenant_id=tenant_id, stage=stage, active_only=active_only
            )
        ]

    async def semantic_search(
        self,
        *,
        tenant_id: str,
        query: str,
        limit: int = 5,
        min_score: float = 0.05,
        capability_id: str | None = None,
        knowledge_kinds: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        knowledge = await self.knowledge.materialize(tenant_id=tenant_id)
        return await self.semantic.search(
            knowledge,
            tenant_id=tenant_id,
            query=query,
            limit=limit,
            min_score=min_score,
            capability_id=capability_id,
            knowledge_kinds=knowledge_kinds,
        )

    async def semantic_technology_decision(self) -> dict[str, Any]:
        return self.semantic.technology_decision()

    async def interpret_planning_objective(self, *, objective: str) -> dict[str, Any]:
        return self.planning.interpret(objective=objective)

    async def decompose_planning_objective(
        self,
        *,
        objective: str,
        max_work_items: int = 12,
        tenant_id: str | None = None,
        capability_id: str | None = None,
        problem_id: str | None = None,
    ) -> dict[str, Any]:
        """EX5-S6 (Closed Learning Loop): see KernelService's twin for the
        governance rationale — Level 2 only, a quality-score
        recommendation, never a silent decomposition rewrite.

        AIX-EXECUTION-006-S7: see KernelService's twin for the full
        rationale — capability_id is derived from problem_id when not
        supplied, learning application status is always reported (never
        silently skipped with no signal), and capability reuse search now
        runs as part of this same call."""
        resolved_capability_id = str(capability_id or "").strip() or None
        capability_id_source = "explicit" if resolved_capability_id else None
        if not resolved_capability_id and problem_id and tenant_id:
            problem = await self.get_projection(
                tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id
            )
            if problem:
                derived = (problem.get("payload") or {}).get("parent_id")
                if derived:
                    resolved_capability_id = derived
                    capability_id_source = "derived_from_problem"

        relevant_learning: list[dict[str, Any]] = []
        if not tenant_id:
            learning_status = "skipped_no_tenant_id"
        elif not resolved_capability_id:
            learning_status = "skipped_no_capability_id"
        else:
            records = await self.list_learning(tenant_id=tenant_id, active_only=True)
            relevant_learning = [r for r in records if r.get("capability_id") == resolved_capability_id]
            learning_status = "applied" if relevant_learning else "no_relevant_history"

        result = self.planning.decompose(
            objective=objective, max_work_items=max_work_items, relevant_learning=relevant_learning,
        )

        reuse_recommendation: dict[str, Any] | None = None
        if tenant_id:
            try:
                reuse_recommendation = await self.reason_organizationally(
                    tenant_id=tenant_id, mode="capability_reuse_search",
                    objective=objective, capability_id=resolved_capability_id,
                )
            except ExecutionKernelRepositoryError:
                reuse_recommendation = None

        return {
            **result,
            "capability_id_used": resolved_capability_id,
            "capability_id_source": capability_id_source,
            "learning_status": learning_status,
            "relevant_learning_count": len(relevant_learning),
            "reuse_recommendation": reuse_recommendation,
        }

    async def evaluate_planning_autonomy(
        self,
        *,
        mutation_kind: str,
        problem_id: str | None = None,
        tenant_id: str = "",
        affected_capabilities: list[str] | None = None,
        requests_new_capability: bool = False,
        charter_change: bool = False,
    ) -> dict[str, Any]:
        problem_capability_id = None
        if problem_id and tenant_id:
            problem = await self.get_projection(
                tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id
            )
            if problem:
                problem_capability_id = (problem.get("payload") or {}).get("parent_id")
        return self.planning.autonomy(
            mutation_kind=mutation_kind,
            problem_capability_id=problem_capability_id,
            affected_capabilities=affected_capabilities,
            requests_new_capability=requests_new_capability,
            charter_change=charter_change,
        )

    async def detect_planning_replan_triggers(
        self,
        *,
        tenant_id: str,
        problem_id: str,
        capability_id: str | None = None,
        required_knowledge_kinds: list[str] | None = None,
        objective: str | None = None,
    ) -> dict[str, Any]:
        tenant_id = str(tenant_id or "").strip()
        if not tenant_id:
            raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
        problem = await self.get_projection(
            tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id
        )
        if problem is None:
            raise ExecutionKernelRepositoryError(f"problem not found: {problem_id}")
        cap_id = capability_id or (problem.get("payload") or {}).get("parent_id")
        work_items: list[dict[str, Any]] = []
        children = await self.projection.list_children(
            tenant_id=tenant_id, aggregate_type="work_item", parent_id=problem_id
        )
        for row in children:
            work_items.append(
                {
                    "work_item_id": row.aggregate_id,
                    "status": row.status,
                    "blocked_by_deps": False,
                }
            )

        missing = None
        if cap_id and required_knowledge_kinds is not None:
            knowledge = await self.knowledge.materialize(tenant_id=tenant_id)
            missing = detect_missing_knowledge(
                knowledge,
                tenant_id=tenant_id,
                capability_id=str(cap_id),
                required_kinds=required_knowledge_kinds,
            )
        objective_ambiguous = False
        quality = None
        if objective:
            plan = decompose_plan(objective=objective)
            quality = evaluate_plan_quality(plan)
            objective_ambiguous = bool(plan.get("ambiguous"))
        triggers = self.planning.replan_triggers(
            tenant_id=tenant_id,
            work_items=work_items,
            missing_knowledge=missing,
            plan_quality=quality,
            objective_ambiguous=objective_ambiguous,
        )
        return {
            "problem_id": problem_id,
            "capability_id": cap_id,
            "triggers": triggers,
            "trigger_count": len(triggers),
            "work_item_count": len(work_items),
        }

    async def propose_plan_mutation(
        self,
        *,
        tenant_id: str,
        problem_id: str,
        mutation_kind: str,
        reason: str,
        command_id: str,
        work_item_id: str | None = None,
        affected_capabilities: list[str] | None = None,
        objective: str | None = None,
        requests_new_capability: bool = False,
        charter_change: bool = False,
    ) -> dict[str, Any]:
        problem = await self.get_projection(
            tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id
        )
        if problem is None:
            raise ExecutionKernelRepositoryError(f"problem not found: {problem_id}")
        problem_capability_id = (problem.get("payload") or {}).get("parent_id")
        autonomy = bounded_autonomy_for_mutation(
            mutation_kind,
            problem_capability_id=problem_capability_id,
            affected_capabilities=affected_capabilities,
            requests_new_capability=requests_new_capability,
            charter_change=charter_change,
        )
        draft = decompose_plan(objective=objective) if objective else {}
        meta = build_plan_mutation_proposal_payload(
            mutation_kind=mutation_kind,
            reason=reason,
            draft_plan=draft,
            autonomy=autonomy,
        )
        enriched_reason = f"{reason} | planning={meta}"
        proposal = await self.raise_proposal(
            tenant_id=tenant_id,
            problem_id=problem_id,
            reason=enriched_reason,
            command_id=command_id,
            level=autonomy.required_proposal_level,  # type: ignore[arg-type]
            work_item_id=work_item_id,
            affected_capabilities=affected_capabilities,
            estimated_work_item_delta=len(draft.get("work_items") or []),
            risk="high" if autonomy.required_proposal_level >= 3 else "medium",
            requests_new_capability=requests_new_capability
            or mutation_kind == "new_capability_or_charter",
            charter_change=charter_change or mutation_kind == "new_capability_or_charter",
        )
        return {
            "proposal": proposal,
            "autonomy": autonomy_decision_to_dict(autonomy),
            "silent_graph_mutation": False,
            "draft_plan": draft or None,
        }

    async def reason_organizationally(
        self,
        *,
        tenant_id: str,
        mode: str,
        question: str = "",
        capability_id: str | None = None,
        project_id: str | None = None,
        objective: str | None = None,
        required_knowledge_kinds: list[str] | None = None,
        semantic_limit: int = 5,
    ) -> dict[str, Any]:
        knowledge, graph = await self._materialize_organizational_knowledge(tenant_id=tenant_id)
        # EX5-S10: reason_organizationally's capability_reuse_search branch
        # calls registry.list() synchronously — PostgresCapabilityRegistry's
        # own .list() is async, so materialize a throwaway in-memory
        # snapshot first, same pattern already used for knowledge/graph
        # above and for resolve_dependencies() on the Postgres registry
        # itself.
        registry_snapshot = InMemoryCapabilityRegistry()
        for entry in await self.capability_registry.list(tenant_id=tenant_id, active_only=True):
            registry_snapshot.register(entry)
        return self.reasoning.reason(
            knowledge,
            graph,
            tenant_id=tenant_id,
            mode=mode,
            question=question,
            capability_id=capability_id,
            project_id=project_id,
            objective=objective,
            required_knowledge_kinds=required_knowledge_kinds,
            semantic_limit=semantic_limit,
            registry=registry_snapshot,
        )

    async def reasoning_golden_scenarios(self) -> list[dict[str, str]]:
        return self.reasoning.golden_scenarios()

    async def enterprise_scenarios(self) -> list[dict[str, str]]:
        return enterprise_scenarios()

    async def portfolio_intelligence_view(self, *, tenant_id: str) -> dict[str, Any]:
        mem_proj = InMemoryProjectionStore()
        for aggregate_type in ("project", "capability", "problem"):
            rows = await self.projection.list_current(
                tenant_id=tenant_id, aggregate_type=aggregate_type
            )
            for row in rows:
                mem_proj._rows[(row.tenant_id, row.aggregate_type, row.aggregate_id)] = row  # noqa: SLF001
        knowledge, graph = await self._materialize_organizational_knowledge(tenant_id=tenant_id)
        return build_portfolio_intelligence_view(
            mem_proj, knowledge, graph, tenant_id=tenant_id
        )

    async def start_enterprise_improvement_cycle(
        self,
        *,
        tenant_id: str,
        summary: str,
        scope: str = "organisation",
        capability_id: str | None = None,
        actor_id: str = "enterprise_operator",
    ) -> dict[str, Any]:
        return await start_governed_improvement_cycle_async(
            self.learning_lifecycle,
            self.enterprise_audit,
            tenant_id=tenant_id,
            summary=summary,
            scope=scope,
            capability_id=capability_id,
            actor_id=actor_id,
        )

    async def strategic_programme_resume(
        self,
        *,
        tenant_id: str,
        problem_id: str,
        capability_id: str | None = None,
    ) -> dict[str, Any]:
        resume = await self.resume(tenant_id=tenant_id, problem_id=problem_id)
        brief = None
        if capability_id:
            knowledge, graph = await self._materialize_organizational_knowledge(
                tenant_id=tenant_id
            )
            brief = self.reasoning.reason(
                knowledge,
                graph,
                tenant_id=tenant_id,
                mode="pre_planning_brief",
                capability_id=capability_id,
                question="Strategic resume: what do we know?",
            )
        audit_event = await self.enterprise_audit.append(
            tenant_id=tenant_id,
            event_type="STRATEGIC_RESUME",
            payload={
                "problem_id": problem_id,
                "checkpoint_id": resume.checkpoint_id,
                "next_actionable": list(resume.next_actionable),
                "capability_id": capability_id,
            },
        )
        return {
            "scenario": "long_horizon_strategic_resume",
            "resume": {
                "problem_id": resume.problem_id,
                "checkpoint_id": resume.checkpoint_id,
                "work_items": resume.work_items,
                "next_actionable": resume.next_actionable,
                "projection_watermark": resume.projection_watermark,
                "source": resume.source,
            },
            "pre_planning_brief": brief,
            "state_mutations_from_platform": [],
            "audit_event": audit_event,
            "ai_ceo_claim": False,
        }

    async def list_enterprise_audit(self, *, tenant_id: str) -> list[dict[str, Any]]:
        return await self.enterprise_audit.list(tenant_id=tenant_id)

    async def prepare_dispatch(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        max_tokens: int = 4000,
        timeout_budget_seconds: int = 600,
        model_routing: dict[str, Any] | None = None,
        include_verifier_route: bool = False,
        require_independent_verifier: bool = False,
    ) -> tuple[ContextCapsule, DispatchRequest]:
        capsule = await self.build_capsule(tenant_id=tenant_id, work_item_id=work_item_id, max_tokens=max_tokens)
        routes = (
            resolve_kernel_model_routes(
                routing_context=model_routing,
                include_verifier=include_verifier_route,
                require_independent_verifier=require_independent_verifier,
            )
            if model_routing is not None
            else {}
        )
        enforced_routes: dict[str, Any] = {}
        if routes:
            # AIX-EXECUTION-006-S2: dispatch must carry the *bound, durable* route
            # (enforced=True, route_id set), not the raw policy-engine payload —
            # the raw payload has neither key, which made every live dispatch that
            # actually supplied model_routing raise EXECUTION_ROUTE_REQUIRED.
            contract = await self._bind_execution_route(
                tenant_id=tenant_id, work_item_id=work_item_id, model_routes=routes
            )
            enforced_routes = model_routes_payload(contract)
        return capsule, build_dispatch_request(
            capsule=capsule,
            timeout_budget_seconds=timeout_budget_seconds,
            model_routes=enforced_routes,
        )

    async def _bind_execution_route(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        model_routes: dict[str, Any],
        cycle_id: str | None = None,
    ) -> ExecutionRouteContract:
        contract = contract_from_model_routes(
            tenant_id=tenant_id, work_item_id=work_item_id, model_routes=model_routes, cycle_id=cycle_id,
        )
        return await self.execution_routes.bind(contract)

    async def get_execution_route(
        self, *, tenant_id: str, work_item_id: str
    ) -> dict[str, Any] | None:
        route = await self.execution_routes.get_active(tenant_id=tenant_id, work_item_id=work_item_id)
        return route.to_dict() if route else None

    async def switch_execution_route(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        model_routes: dict[str, Any],
        reason: str,
        cycle_id: str | None = None,
        as_fallback: bool = False,
    ) -> dict[str, Any]:
        contract = await self.execution_routes.switch(
            tenant_id=tenant_id, work_item_id=work_item_id, model_routes=model_routes,
            reason=reason, cycle_id=cycle_id, as_fallback=as_fallback,
        )
        return contract.to_dict()

    async def list_execution_route_audit(
        self, *, tenant_id: str, work_item_id: str | None = None
    ) -> list[dict[str, Any]]:
        return await self.execution_routes.list_audit(tenant_id=tenant_id, work_item_id=work_item_id)

    async def list_execution_route_history(
        self, *, tenant_id: str, work_item_id: str | None = None
    ) -> list[dict[str, Any]]:
        rows = await self.execution_routes.history(tenant_id=tenant_id, work_item_id=work_item_id)
        return [c.to_dict() for c in rows]

    async def record_actor_mutation(
        self, *, tenant_id: str, actor_id: str, roles: tuple[str, ...], action: str, request_id: str | None = None,
    ) -> dict[str, Any]:
        """AIX-EXECUTION-006-S5: durable "who did what, when" for every
        mutating execution_kernel call — closes the gap where none of the
        ledger event types, and only 2 of the many mutating router
        endpoints, ever recorded an actor identity anywhere."""
        tenant_id = str(tenant_id or "").strip()
        if not tenant_id:
            raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
        audit_id = f"aud_{new_ulid()[:12]}"
        conn = await acquire_pooled_connection(self.database_url, tenant_id=tenant_id)
        try:
            await conn.execute(
                f"""
                INSERT INTO {ACTOR_AUDIT_TABLE}
                    (audit_id, tenant_id, actor_id, roles, action, request_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                audit_id, tenant_id, str(actor_id or "").strip() or "unknown",
                ",".join(roles), action, request_id,
            )
        finally:
            await conn.close()
        return {"audit_id": audit_id, "tenant_id": tenant_id, "actor_id": actor_id, "action": action}

    async def list_actor_audit(self, *, tenant_id: str, limit: int = 200) -> list[dict[str, Any]]:
        tenant_id = str(tenant_id or "").strip()
        if not tenant_id:
            raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
        conn = await acquire_pooled_connection(self.database_url, tenant_id=tenant_id)
        try:
            rows = await conn.fetch(
                f"""
                SELECT * FROM {ACTOR_AUDIT_TABLE} WHERE tenant_id = $1
                ORDER BY recorded_at DESC LIMIT $2
                """,
                tenant_id, limit,
            )
        finally:
            await conn.close()
        return [
            {
                "audit_id": r["audit_id"],
                "tenant_id": r["tenant_id"],
                "actor_id": r["actor_id"],
                "roles": r["roles"].split(",") if r["roles"] else [],
                "action": r["action"],
                "request_id": r["request_id"],
                "recorded_at": r["recorded_at"].isoformat().replace("+00:00", "Z"),
            }
            for r in rows
        ]

    async def live_dispatch(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        work_item_id: str,
        backend: ObjectiveCycleApiBackend,
        workhub_task_id: str,
        idempotency_key: str,
        max_tokens: int = 4000,
        timeout_budget_seconds: int = 600,
        task_class: str = "bounded_repair",
        candidate_sha: str | None = None,
        repository_id: str | None = None,
        definition_of_done: list[str] | None = None,
        allowed_files: list[str] | None = None,
        project_id: str | None = None,
        budgets: dict[str, int] | None = None,
        expected_sequence_number: int | None = None,
        mark_in_progress: bool = True,
        repository: dict[str, Any] | None = None,
        model_routing: dict[str, Any] | None = None,
        include_verifier_route: bool = False,
        require_independent_verifier: bool = False,
    ) -> dict[str, Any]:
        capsule = await self.build_capsule(
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            max_tokens=max_tokens,
            repository=repository,
        )
        routes = (
            resolve_kernel_model_routes(
                routing_context=model_routing,
                include_verifier=include_verifier_route,
                require_independent_verifier=require_independent_verifier,
            )
            if model_routing is not None
            else {}
        )
        enforced_routes: dict[str, Any] = {}
        if routes:
            # AIX-EXECUTION-006-S2: see prepare_dispatch — dispatch must carry the
            # bound, durable route payload (enforced=True, route_id), not the raw
            # policy-engine payload.
            contract = await self._bind_execution_route(
                tenant_id=tenant_id, work_item_id=work_item_id, model_routes=routes
            )
            enforced_routes = model_routes_payload(contract)
        dispatch = build_dispatch_request(
            capsule=capsule,
            timeout_budget_seconds=timeout_budget_seconds,
            model_routes=enforced_routes,
        )
        adapter = ObjectiveCycleExecutorAdapter(backend)
        handle, outcome = await adapter.dispatch(
            tenant_id=tenant_id,
            actor_id=actor_id,
            capsule=capsule,
            dispatch=dispatch,
            workhub_task_id=workhub_task_id,
            idempotency_key=idempotency_key,
            task_class=task_class,
            candidate_sha=candidate_sha,
            repository_id=repository_id,
            definition_of_done=definition_of_done,
            allowed_files=allowed_files,
            project_id=project_id,
            budgets=budgets,
        )
        event_info: dict[str, Any] | None = None
        if mark_in_progress and expected_sequence_number is not None:
            wi = await self.projection.get(
                tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=work_item_id
            )
            if wi is None:
                raise ExecutionKernelRepositoryError(f"work_item not found: {work_item_id}")
            payload = dict(wi.payload)
            payload["status"] = "IN_PROGRESS"
            payload["adapter"] = {
                "cycle_id": handle.cycle_id,
                "objective_id": handle.objective_id,
                "capsule_content_sha256": handle.capsule_content_sha256,
            }
            if wi.parent_id:
                payload.setdefault("parent_id", wi.parent_id)
            event = await self.append_and_project(
                tenant_id=tenant_id,
                aggregate_type="work_item",
                aggregate_id=work_item_id,
                expected_sequence_number=expected_sequence_number,
                event_type="WI_IN_PROGRESS",
                command_id=f"{idempotency_key}:wi_in_progress",
                payload=payload,
            )
            event_info = {
                "id": event.id,
                "sequence_number": event.sequence_number,
                "event_type": event.event_type,
                "replayed": event.replayed,
            }
        return {
            "handle": _handle_dict(handle),
            "outcome": _outcome_dict(outcome) if outcome else None,
            "dispatch": {
                "work_item_id": dispatch.work_item_id,
                "capsule_content_sha256": dispatch.capsule_content_sha256,
                "timeout_budget_seconds": dispatch.timeout_budget_seconds,
                "model_routes": dict(dispatch.model_routes),
            },
            "event": event_info,
        }

    async def poll_live_dispatch(
        self, *, tenant_id: str, backend: ObjectiveCycleApiBackend, handle: AdapterHandle
    ) -> dict[str, Any] | None:
        adapter = ObjectiveCycleExecutorAdapter(backend)
        outcome = await adapter.poll(tenant_id=tenant_id, handle=handle)
        return _outcome_dict(outcome) if outcome else None

    async def run_problem_step(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        problem_id: str,
        mode: ProblemStepMode,
        repository_state: dict[str, Any],
        idempotency_key: str,
        max_tokens: int = 4000,
        timeout_budget_seconds: int = 600,
        distill: bool = True,
        lesson_summary: str | None = None,
        promote_pattern: bool = False,
        cycle_backend: ObjectiveCycleApiBackend | None = None,
        workhub_task_id: str | None = None,
        task_class: str = "bounded_repair",
        candidate_sha: str | None = None,
        repository_id: str | None = None,
        definition_of_done: list[str] | None = None,
        allowed_files: list[str] | None = None,
        project_id: str | None = None,
        budgets: dict[str, int] | None = None,
        repository: dict[str, Any] | None = None,
        work_item_id: str | None = None,
        outcome: AdapterOutcome | None = None,
        decision: DecisionVerdict | None = None,
        waiver_id: str | None = None,
        waiver_rationale: str | None = None,
        waiver_approver_actor_id: str | None = None,
        waiver_approver_role: str | None = None,
        waiver_expires_at: str | None = None,
        required_evidence: bool = True,
        dry_auto_verify: bool = True,
        proposal_raise: dict[str, Any] | None = None,
    ) -> ProblemStepResult:
        return await run_problem_step(
            self,
            tenant_id=tenant_id,
            actor_id=actor_id,
            problem_id=problem_id,
            mode=mode,
            repository_state=repository_state,
            idempotency_key=idempotency_key,
            max_tokens=max_tokens,
            timeout_budget_seconds=timeout_budget_seconds,
            distill=distill,
            lesson_summary=lesson_summary,
            promote_pattern=promote_pattern,
            cycle_backend=cycle_backend,
            workhub_task_id=workhub_task_id,
            task_class=task_class,
            candidate_sha=candidate_sha,
            repository_id=repository_id,
            definition_of_done=definition_of_done,
            allowed_files=allowed_files,
            project_id=project_id,
            budgets=budgets,
            repository=repository,
            work_item_id=work_item_id,
            outcome=outcome,
            decision=decision,
            waiver_id=waiver_id,
            waiver_rationale=waiver_rationale,
            waiver_approver_actor_id=waiver_approver_actor_id,
            waiver_approver_role=waiver_approver_role,
            waiver_expires_at=waiver_expires_at,
            required_evidence=required_evidence,
            dry_auto_verify=dry_auto_verify,
            proposal_raise=proposal_raise,
        )

    async def apply_verification(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        outcome: AdapterOutcome,
        decision: DecisionVerdict,
        expected_sequence_number: int,
        command_id: str,
        waiver_id: str | None = None,
        waiver_rationale: str | None = None,
        waiver_approver_actor_id: str | None = None,
        waiver_approver_role: str | None = None,
        waiver_expires_at: str | None = None,
        required_evidence: bool = True,
    ) -> dict[str, Any]:
        mapped = map_adapter_outcome_without_verification(outcome)
        result = verify_adapter_outcome(
            outcome=outcome,
            decision=decision,
            required_evidence=required_evidence,
            waiver_id=waiver_id,
            waiver_rationale=waiver_rationale,
            waiver_approver_actor_id=waiver_approver_actor_id,
            waiver_approver_role=waiver_approver_role,  # type: ignore[arg-type]
            waiver_expires_at=waiver_expires_at,
        )
        wi = await self.projection.get(
            tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=work_item_id
        )
        if wi is None:
            raise ExecutionKernelRepositoryError(f"work_item not found: {work_item_id}")
        payload = dict(wi.payload)
        payload["status"] = result.work_item_status
        payload["evidence_refs"] = list(result.evidence_refs)
        payload["verification"] = {
            "decision": result.decision,
            "reason_code": result.reason_code,
            "waiver_id": result.waiver_id,
            "waiver_rationale": result.waiver_rationale,
            "waiver_approver_actor_id": result.waiver_approver_actor_id,
            "waiver_approver_role": result.waiver_approver_role,
            "waiver_expires_at": result.waiver_expires_at,
            "adapter_pre_map": mapped,
            "cycle_id": outcome.cycle_id,
        }
        if wi.parent_id:
            payload.setdefault("parent_id", wi.parent_id)
        event = await self.append_and_project(
            tenant_id=tenant_id,
            aggregate_type="work_item",
            aggregate_id=work_item_id,
            expected_sequence_number=expected_sequence_number,
            event_type=f"WI_{result.work_item_status}",
            command_id=command_id,
            payload=payload,
        )
        return {
            "verification": {
                "work_item_status": result.work_item_status,
                "decision": result.decision,
                "evidence_refs": list(result.evidence_refs),
                "reason_code": result.reason_code,
                "waiver_id": result.waiver_id,
                "waiver_rationale": result.waiver_rationale,
                "waiver_approver_actor_id": result.waiver_approver_actor_id,
                "waiver_approver_role": result.waiver_approver_role,
                "waiver_expires_at": result.waiver_expires_at,
            },
            "event": {
                "id": event.id,
                "sequence_number": event.sequence_number,
                "event_type": event.event_type,
                "replayed": event.replayed,
            },
        }

    async def distill_learning(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        decision: DecisionVerdict,
        evidence_refs: list[str] | None = None,
        lesson_summary: str | None = None,
        promote_pattern: bool = False,
        command_id_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        wi = await self.projection.get(
            tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=work_item_id
        )
        if wi is None:
            raise ExecutionKernelRepositoryError(f"work_item not found: {work_item_id}")
        problem_id = wi.parent_id or str(wi.payload.get("parent_id") or "") or None
        capability_id = None
        if problem_id:
            problem = await self.projection.get(
                tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id
            )
            if problem:
                capability_id = problem.parent_id or str(problem.payload.get("parent_id") or "") or None
        status = wi.status
        if status not in {"VERIFIED", "FAILED"}:
            return []
        proposals = distill_knowledge_proposals(
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            problem_id=problem_id,
            capability_id=capability_id,
            evidence_refs=evidence_refs or list(wi.payload.get("evidence_refs") or []),
            decision=decision,
            work_item_status=status,  # type: ignore[arg-type]
            lesson_summary=lesson_summary,
            promote_pattern=promote_pattern,
        )
        prefix = command_id_prefix or f"distill:{work_item_id}"
        written: list[dict[str, Any]] = []
        evidence = list(evidence_refs or list(wi.payload.get("evidence_refs") or []))
        for idx, entry in enumerate(proposals):
            command_id = f"{prefix}:{idx}:{new_ulid()[:8]}"
            row = await self.upsert_knowledge(
                tenant_id=tenant_id,
                knowledge_entry_id=entry.knowledge_entry_id,
                knowledge_kind=entry.knowledge_kind,
                content=entry.content,
                command_id=command_id,
                capability_id=entry.capability_id,
                problem_id=entry.problem_id,
                project_id=entry.project_id,
                applicable_capability_ids=list(entry.applicable_capability_ids),
            )
            await self.add_knowledge_graph_edge(
                tenant_id=tenant_id,
                edge_type="DERIVED_FROM",
                from_type="knowledge",
                from_id=entry.knowledge_entry_id,
                to_type="work_item",
                to_id=work_item_id,
                command_id=f"{command_id}:derived:wi",
                provenance=evidence,
            )
            for evidence_index, evidence_ref in enumerate(evidence):
                await self.add_knowledge_graph_edge(
                    tenant_id=tenant_id,
                    edge_type="DERIVED_FROM",
                    from_type="knowledge",
                    from_id=entry.knowledge_entry_id,
                    to_type="evidence",
                    to_id=str(evidence_ref),
                    command_id=f"{command_id}:derived:ev:{evidence_index}",
                    provenance=(str(evidence_ref),),
                )
            if entry.knowledge_kind == "pattern" and capability_id:
                await self.add_knowledge_graph_edge(
                    tenant_id=tenant_id,
                    edge_type="ABOUT",
                    from_type="knowledge",
                    from_id=entry.knowledge_entry_id,
                    to_type="capability",
                    to_id=capability_id,
                    command_id=f"{command_id}:about:cap",
                )
            written.append(row)
        return written


class SuperuserCredentialsForbiddenError(ExecutionKernelRepositoryError):
    """AIX-EXECUTION-006-S4: raised, never silently swallowed, when the
    execution kernel's real (non-test) Postgres credentials connect as a
    superuser — which unconditionally bypasses every RLS tenant-isolation
    policy this kernel relies on, making them inert without any error to
    signal it. Deliberately NOT caught by build_execution_kernel_service's
    connectivity-fallback except clause; this must fail startup loudly, not
    downgrade to in-memory mode."""


async def _enforce_non_superuser_credentials(
    service: "PostgresKernelService", settings: object | None
) -> None:
    allow_superuser = False
    if settings is not None:
        allow_superuser = bool(getattr(settings, "EXECUTION_KERNEL_ALLOW_SUPERUSER_DB", False))
    if not allow_superuser:
        allow_superuser = str(os.getenv("EXECUTION_KERNEL_ALLOW_SUPERUSER_DB", "")).strip().lower() in (
            "1", "true", "yes",
        )
    if allow_superuser:
        logger.warning("execution_kernel.security.superuser_db_allowed_by_override")
        return
    conn = await acquire_pooled_connection(service.database_url)
    try:
        is_superuser = await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    finally:
        await conn.close()
    if is_superuser:
        raise SuperuserCredentialsForbiddenError(
            "SUPERUSER_DB_CREDENTIALS_FORBIDDEN: the execution kernel's configured Postgres "
            "credentials connect as a database superuser, which unconditionally bypasses "
            "FORCE ROW LEVEL SECURITY tenant isolation. Configure a restricted, non-superuser "
            "application role (see AIX-EXECUTION-006-S4), or set "
            "EXECUTION_KERNEL_ALLOW_SUPERUSER_DB=true to explicitly override for local "
            "development only."
        )


async def build_execution_kernel_service(settings: object | None = None) -> KernelService | PostgresKernelService:
    """Prefer Postgres when URL is reachable; else in-process memory."""
    allow_inprocess = True
    if settings is not None:
        allow_inprocess = bool(getattr(settings, "EXECUTION_KERNEL_ALLOW_INPROCESS", True))
    prefer_postgres = True
    if settings is not None:
        prefer_postgres = bool(getattr(settings, "EXECUTION_KERNEL_PREFER_POSTGRES", True))

    database_url = resolve_test_database_url()
    # Only the real (non-test) credential path is subject to the superuser
    # guard — the dev/test database is a deliberately superuser sandbox.
    is_test_url = database_url is not None
    if not database_url and settings is not None:
        database_url = str(getattr(settings, "DATABASE_URL", "") or "").strip() or None
    if not database_url:
        database_url = str(os.getenv("DATABASE_URL", "") or "").strip() or None

    if database_url and prefer_postgres:
        remapped = _remap_docker_db_host(database_url)
        try:
            service = PostgresKernelService(database_url=remapped)
            await service.ensure_schema()
            # Connectivity already proven by ensure_schema.
            if not is_test_url:
                await _enforce_non_superuser_credentials(service, settings)
            logger.info("execution_kernel.backend.postgres_ready")
            return service
        except SuperuserCredentialsForbiddenError:
            raise
        except Exception as exc:  # noqa: BLE001 - bootstrap fallthrough
            logger.warning(
                "execution_kernel.backend.postgres_unavailable",
                error_type=type(exc).__name__,
                allow_inprocess=allow_inprocess,
            )

    if allow_inprocess:
        logger.info("execution_kernel.backend.inprocess_enabled")
        return KernelService()
    raise ExecutionKernelRepositoryError("execution kernel backend not configured")


__all__ = ["PostgresKernelService", "build_execution_kernel_service"]
