"""EX2-S005/S007: process-local KernelService facade (in-memory default)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
from backend.aix_core.runtime.executor.execution_kernel_adapter.bootstrap import BootstrapSpec, bootstrap_problem_graph
from backend.aix_core.runtime.executor.problem_loop import ProblemStepMode, ProblemStepResult, run_problem_step
from backend.aix_core.runtime.executor.proposals import (
    ProposalLevel,
    open_proposal_dicts,
    raise_proposal,
    resolve_proposal,
)
from backend.aix_core.runtime.objective.api_backend import ObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.checkpoint import InMemoryCheckpointStore, ResumeEngine, ResumeResult
from backend.aix_core.runtime.executor.context_builder import (
    KNOWLEDGE_CONFIDENCE,
    KNOWLEDGE_KINDS,
    ContextBuilder,
    ContextCapsule,
    InMemoryKnowledgeStore,
    KnowledgeEntry,
)
from backend.aix_core.runtime.executor.capability_registry import (
    InMemoryCapabilityRegistry,
    RegisteredCapability,
    registered_capability_to_dict,
)
from backend.aix_core.runtime.executor.dependency_graph import InMemoryDependencyGraph
from backend.aix_core.runtime.executor.knowledge_graph import (
    InMemoryKnowledgeGraph,
    edge_to_dict,
)
from backend.aix_core.runtime.executor.organizational_knowledge import (
    detect_missing_knowledge,
    list_stale_knowledge,
    query_organizational_knowledge,
    reconcile_cross_project_knowledge,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.model_gateway import resolve_kernel_model_routes
from backend.aix_core.runtime.executor.learning_lifecycle import (
    InMemoryLearningLifecycleStore,
    learning_record_to_dict,
)
from backend.aix_core.runtime.executor.pattern_engine import (
    candidate_to_dict,
    discover_pattern_candidates,
    recommend_patterns,
    recommendation_to_dict,
)
from backend.aix_core.runtime.executor.semantic_retrieval import (
    search_semantic,
    semantic_hit_to_dict,
    technology_decision_dict,
)
from backend.aix_core.runtime.executor.planning_engine import (
    autonomy_decision_to_dict,
    bounded_autonomy_for_mutation,
    build_plan_mutation_proposal_payload,
    decompose_plan,
    detect_replan_triggers,
    evaluate_plan_quality,
    evaluate_plan_quality_with_learning,
    interpret_objective,
    simulate_plan,
)
from backend.aix_core.runtime.executor.organizational_reasoning import (
    golden_scenarios,
    reason_organizationally,
)
from backend.aix_core.runtime.executor.enterprise_intelligence import (
    InMemoryEnterpriseAuditLog,
    build_portfolio_intelligence_view,
    enterprise_scenarios,
    start_governed_improvement_cycle,
    strategic_programme_resume,
)
from backend.aix_core.runtime.executor.execution_route import (
    ExecutionRouteContract,
    InMemoryExecutionRouteStore,
    contract_from_model_routes,
)
from backend.aix_core.runtime.executor.projection import InMemoryProjectionStore
from backend.infrastructure.postgres.execution_kernel.repository import (
    ExecutionEvent,
    ExecutionKernelRepositoryError,
    InMemoryExecutionKernelRepository,
)


@dataclass
class KernelService:
    """In-process kernel runtime used by the EX2-S005 API surface."""

    ledger: InMemoryExecutionKernelRepository = field(default_factory=InMemoryExecutionKernelRepository)
    projection: InMemoryProjectionStore = field(default_factory=InMemoryProjectionStore)
    dependencies: InMemoryDependencyGraph = field(default_factory=InMemoryDependencyGraph)
    checkpoints: InMemoryCheckpointStore = field(default_factory=InMemoryCheckpointStore)
    knowledge: InMemoryKnowledgeStore = field(default_factory=InMemoryKnowledgeStore)
    knowledge_graph: InMemoryKnowledgeGraph = field(default_factory=InMemoryKnowledgeGraph)
    capability_registry: InMemoryCapabilityRegistry = field(default_factory=InMemoryCapabilityRegistry)
    learning_lifecycle: InMemoryLearningLifecycleStore = field(
        default_factory=InMemoryLearningLifecycleStore
    )
    enterprise_audit: InMemoryEnterpriseAuditLog = field(
        default_factory=InMemoryEnterpriseAuditLog
    )
    execution_routes: InMemoryExecutionRouteStore = field(
        default_factory=InMemoryExecutionRouteStore
    )
    mode: str = "inprocess"

    def __post_init__(self) -> None:
        self.resume_engine = ResumeEngine(
            projection=self.projection,
            dependencies=self.dependencies,
            checkpoints=self.checkpoints,
        )
        self.context_builder = ContextBuilder(
            projection=self.projection,
            dependencies=self.dependencies,
            knowledge=self.knowledge,
        )

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
        if not event.replayed:
            self.projection.apply_event(event)
        else:
            # Idempotent replay: ensure projection reflects latest from ledger.
            latest = await self.ledger.latest_snapshot(
                tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id
            )
            if latest is not None:
                self.projection.apply_event(latest)
        return event

    async def get_projection(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> dict[str, Any] | None:
        row = self.projection.get(
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
        """DRIFT-021: independently reconstruct this aggregate's current state by
        folding its full event history into a fresh projection store (never touching
        `self.projection`, the live incrementally-maintained one), then compare the
        two. A mismatch means the live projection has drifted from what the ledger —
        the canonical durable record — actually says happened; that is a real
        consistency bug, not a matter of opinion, since payloads are full snapshots
        (see `objective_cycle`'s identical design note) so replay is deterministic.
        `declared_sequence_number`, if given, is checked against the replayed value
        too (e.g. a caller's cached belief of "what version am I at")."""
        replay_store = InMemoryProjectionStore()
        replayed = await replay_store.rebuild_from_ledger(
            self.ledger, tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id
        )
        live = self.projection.get(tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id)

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
        edge = self.dependencies.add_dependency(
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
        return self.dependencies.next_actionable_work_items(
            tenant_id=tenant_id, problem_id=problem_id, projection=self.projection
        )

    async def list_open_proposals(self, *, tenant_id: str, problem_id: str) -> list[dict[str, Any]]:
        rows = self.projection.list_children(
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
        cp = self.checkpoints.capture(
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
        return self.resume_engine.resume(tenant_id=tenant_id, problem_id=problem_id)

    async def build_capsule(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        max_tokens: int = 4000,
        repository: dict[str, Any] | None = None,
    ) -> ContextCapsule:
        return self.context_builder.build(
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
        _ = command_id  # in-memory path is not event-idempotent; kept for API parity
        if knowledge_kind not in KNOWLEDGE_KINDS:
            raise ExecutionKernelRepositoryError(f"invalid knowledge_kind={knowledge_kind!r}")
        existing = next(
            (
                e
                for e in self.knowledge._entries  # noqa: SLF001
                if e.tenant_id == tenant_id and e.knowledge_entry_id == knowledge_entry_id
            ),
            None,
        )
        conf = str(
            confidence
            if confidence is not None
            else (existing.confidence if existing is not None else "unknown")
        )
        if conf not in KNOWLEDGE_CONFIDENCE:
            raise ExecutionKernelRepositoryError(f"invalid knowledge confidence={conf!r}")
        revision = (existing.revision + 1) if existing else 1
        status = "DEPRECATED" if archive else "ACTIVE"
        entry = KnowledgeEntry(
            knowledge_entry_id=knowledge_entry_id,
            tenant_id=tenant_id,
            knowledge_kind=knowledge_kind,
            capability_id=capability_id,
            problem_id=problem_id,
            project_id=project_id,
            content=content,
            applicable_capability_ids=tuple(applicable_capability_ids or ()),
            status=status,
            revision=revision,
            valid_from=valid_from if valid_from is not None else (existing.valid_from if existing else None),
            valid_until=valid_until if valid_until is not None else (existing.valid_until if existing else None),
            superseded_by_entry_id=superseded_by_entry_id
            if superseded_by_entry_id is not None
            else (existing.superseded_by_entry_id if existing else None),
            confidence=conf,  # type: ignore[arg-type]
            review_due_at=review_due_at if review_due_at is not None else (existing.review_due_at if existing else None),
        )
        self.knowledge.upsert(entry)
        if superseded_by_entry_id:
            self.knowledge_graph.add_edge(
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
        edge = self.knowledge_graph.add_edge(
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
        edges = self.knowledge_graph.get_edges(
            tenant_id=tenant_id,
            edge_type=edge_type,
            from_type=from_type,
            from_id=from_id,
            to_type=to_type,
            to_id=to_id,
            active_only=active_only,
        )
        return [edge_to_dict(e) for e in edges]

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
        return self.knowledge_graph.traverse(
            tenant_id=tenant_id,
            start_type=start_type,
            start_id=start_id,
            edge_types=edge_types,
            direction=direction,  # type: ignore[arg-type]
            max_depth=max_depth,
        )

    async def query_organizational_knowledge(
        self,
        *,
        tenant_id: str,
        capability_id: str | None = None,
        project_id: str | None = None,
        minimum_confidence: str = "unknown",
        include_stale: bool = False,
    ) -> list[dict[str, Any]]:
        return query_organizational_knowledge(
            self.knowledge,
            self.knowledge_graph,
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
        return detect_missing_knowledge(
            self.knowledge,
            tenant_id=tenant_id,
            capability_id=capability_id,
            required_kinds=required_kinds,
        )

    async def list_stale_knowledge(
        self, *, tenant_id: str, capability_id: str | None = None
    ) -> list[dict[str, Any]]:
        """EX5-S7: the review worklist — what currently needs attention."""
        return list_stale_knowledge(self.knowledge, tenant_id=tenant_id, capability_id=capability_id)

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
        """EX5-S7: re-confirm a stale-but-still-good entry without forcing
        the caller to resupply the entire payload — the actual 'evolution'
        half of 'never permanently stale' (detection alone, from
        EX4-S002, only ever moved an entry towards stale; nothing
        previously moved it back towards current)."""
        existing = self.knowledge.get(tenant_id=tenant_id, knowledge_entry_id=knowledge_entry_id)
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
        return reconcile_cross_project_knowledge(self.knowledge, tenant_id=tenant_id)

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
            if self.knowledge.get(tenant_id=tenant_id, knowledge_entry_id=knowledge_id) is None:
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
        conflict = self.knowledge_graph.get_edge(tenant_id=tenant_id, edge_id=conflict_edge_id)
        if conflict is None or conflict.edge_type != "CONFLICTS_WITH":
            raise ExecutionKernelRepositoryError(f"active conflict edge not found: {conflict_edge_id}")
        endpoints = {conflict.from_id, conflict.to_id}
        if {authoritative_knowledge_id, superseded_knowledge_id} != endpoints:
            raise ExecutionKernelRepositoryError("resolution ids must match conflict endpoints")
        superseded = self.knowledge.get(
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
        resolved = self.knowledge_graph.supersede_edge(
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
        entry = self.capability_registry.register(
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
        entry = self.capability_registry.get(
            tenant_id=tenant_id, capability_key=capability_key, version=version
        )
        return registered_capability_to_dict(entry) if entry else None

    async def list_registered_capabilities(
        self, *, tenant_id: str, active_only: bool = True, capability_key: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            registered_capability_to_dict(e)
            for e in self.capability_registry.list(
                tenant_id=tenant_id, active_only=active_only, capability_key=capability_key
            )
        ]

    async def resolve_capability_dependencies(
        self, *, tenant_id: str, capability_key: str, version: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            registered_capability_to_dict(e)
            for e in self.capability_registry.resolve_dependencies(
                tenant_id=tenant_id, capability_key=capability_key, version=version
            )
        ]

    async def discover_patterns(
        self, *, tenant_id: str, min_occurrences: int = 2, include_lessons: bool = True
    ) -> list[dict[str, Any]]:
        """EX4-R2-P7-S001: corpus discovery — candidates only; no auto-promotion."""
        return [
            candidate_to_dict(c)
            for c in discover_pattern_candidates(
                self.knowledge,
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
        return [
            recommendation_to_dict(r)
            for r in recommend_patterns(
                self.knowledge,
                self.knowledge_graph,
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
        """Record USES_PATTERN edge — recommendation feedback without promotion."""
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
        rec = self.learning_lifecycle.observe(
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
            self.learning_lifecycle.advance(
                tenant_id=tenant_id, learning_id=learning_id, to_stage=to_stage
            )
        )

    async def classify_learning_scope(
        self, *, tenant_id: str, learning_id: str, scope: str
    ) -> dict[str, Any]:
        return learning_record_to_dict(
            self.learning_lifecycle.classify_scope(
                tenant_id=tenant_id, learning_id=learning_id, scope=scope
            )
        )

    async def set_learning_confidence(
        self, *, tenant_id: str, learning_id: str, confidence: str
    ) -> dict[str, Any]:
        return learning_record_to_dict(
            self.learning_lifecycle.set_confidence(
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
        """Governed activation. Optionally upsert Knowledge only after approval."""
        knowledge_id = None
        rec = self.learning_lifecycle.get(tenant_id=tenant_id, learning_id=learning_id)
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
                confidence=rec.confidence if rec.confidence in {"unknown", "low", "medium", "high"} else "unknown",
            )
        approved = self.learning_lifecycle.approve_activation(
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
            self.learning_lifecycle.reject(tenant_id=tenant_id, learning_id=learning_id)
        )

    async def list_learning(
        self, *, tenant_id: str, stage: str | None = None, active_only: bool = False
    ) -> list[dict[str, Any]]:
        return [
            learning_record_to_dict(r)
            for r in self.learning_lifecycle.list(
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
        """EX4-R3-P9-S001: additive semantic retrieval — does not replace ranked capsules."""
        return [
            semantic_hit_to_dict(h)
            for h in search_semantic(
                self.knowledge,
                tenant_id=tenant_id,
                query=query,
                limit=limit,
                min_score=min_score,
                capability_id=capability_id,
                knowledge_kinds=knowledge_kinds,
            )
        ]

    async def semantic_technology_decision(self) -> dict[str, Any]:
        return technology_decision_dict()

    async def interpret_planning_objective(self, *, objective: str) -> dict[str, Any]:
        """EX4-R3-P10-S001: requirement extraction + ambiguity — no writes."""
        return interpret_objective(objective)

    async def decompose_planning_objective(
        self,
        *,
        objective: str,
        max_work_items: int = 12,
        tenant_id: str | None = None,
        capability_id: str | None = None,
        problem_id: str | None = None,
    ) -> dict[str, Any]:
        """Bounded draft plan — does not mutate projections.

        EX5-S6 (Closed Learning Loop): when tenant_id + capability_id are
        given, quality evaluation additionally consults ACTIVE_KNOWLEDGE-
        stage learning scoped to that capability — Level 2 governance only
        (a quality-score recommendation, never a silent decomposition
        rewrite). This is what makes 'Project 2 plans measurably better
        than Project 1' concretely true: identical objective text, but a
        capability with prior recorded failures scores lower and names
        why, while a capability with no history does not.

        AIX-EXECUTION-006-S7: a zero-trust audit found that omitting
        capability_id silently skipped learning with no signal to the
        caller, and that capability_reuse_search existed but was never
        consulted during planning at all — only reachable as a separate,
        manually-invoked query. Both are fixed here: capability_id is
        derived from problem_id's parent capability when not supplied
        directly (safe — it's the same derivation evaluate_planning_autonomy
        already uses), and the response always reports whether learning
        was actually applied (never silently ambiguous); capability reuse
        is now searched as part of this same call, not a side-query the
        caller has to know to make separately."""
        plan = decompose_plan(objective=objective, max_work_items=max_work_items)

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
        quality = evaluate_plan_quality_with_learning(plan, relevant_learning=relevant_learning)
        simulation = simulate_plan(plan)

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
            **plan,
            "quality": quality,
            "simulation": simulation,
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
        return autonomy_decision_to_dict(
            bounded_autonomy_for_mutation(
                mutation_kind,
                problem_capability_id=problem_capability_id,
                affected_capabilities=affected_capabilities,
                requests_new_capability=requests_new_capability,
                charter_change=charter_change,
            )
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
        if hasattr(self.projection, "list_children"):
            for row in self.projection.list_children(
                tenant_id=tenant_id, aggregate_type="work_item", parent_id=problem_id
            ):
                work_items.append(
                    {
                        "work_item_id": row.aggregate_id,
                        "status": row.status,
                        "blocked_by_deps": False,
                    }
                )

        missing = None
        if cap_id and required_knowledge_kinds is not None:
            missing = detect_missing_knowledge(
                self.knowledge,
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
        triggers = detect_replan_triggers(
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
        """Raise a Proposal for a plan mutation — never silent graph rewrite."""
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
        """EX4-R4-P11-S001: provenance-bearing answers — zero state mutations.
        EX5-S10 added the capability_reuse_search mode, backed by the
        Capability Registry (P6) — 'org reuse' queries."""
        return reason_organizationally(
            self.knowledge,
            self.knowledge_graph,
            tenant_id=tenant_id,
            mode=mode,
            question=question,
            capability_id=capability_id,
            project_id=project_id,
            objective=objective,
            required_knowledge_kinds=required_knowledge_kinds,
            semantic_limit=semantic_limit,
            registry=self.capability_registry,
        )

    async def reasoning_golden_scenarios(self) -> list[dict[str, str]]:
        return golden_scenarios()

    async def enterprise_scenarios(self) -> list[dict[str, str]]:
        return enterprise_scenarios()

    async def portfolio_intelligence_view(self, *, tenant_id: str) -> dict[str, Any]:
        """EX4-R4-P12-S001: multi-project portfolio snapshot."""
        return build_portfolio_intelligence_view(
            self.projection,
            self.knowledge,
            self.knowledge_graph,
            tenant_id=tenant_id,
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
        """Governed org improvement — Learning lifecycle; no silent org promotion."""
        return start_governed_improvement_cycle(
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
        return strategic_programme_resume(
            self.resume_engine,
            self.enterprise_audit,
            self.knowledge,
            self.knowledge_graph,
            tenant_id=tenant_id,
            problem_id=problem_id,
            capability_id=capability_id,
        )

    async def list_enterprise_audit(self, *, tenant_id: str) -> list[dict[str, Any]]:
        return self.enterprise_audit.list(tenant_id=tenant_id)

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
        if routes:
            self._bind_execution_route(tenant_id=tenant_id, work_item_id=work_item_id, model_routes=routes)
        return capsule, build_dispatch_request(
            capsule=capsule,
            timeout_budget_seconds=timeout_budget_seconds,
            model_routes=routes,
        )

    def _bind_execution_route(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        model_routes: dict[str, Any],
        cycle_id: str | None = None,
    ) -> ExecutionRouteContract:
        """EX5-S2: durably bind the resolved model route so it can be enforced,
        audited, and switched with a reason later — never a silent re-selection."""
        contract = contract_from_model_routes(
            tenant_id=tenant_id, work_item_id=work_item_id, model_routes=model_routes, cycle_id=cycle_id,
        )
        return self.execution_routes.bind(contract)

    async def get_execution_route(
        self, *, tenant_id: str, work_item_id: str
    ) -> dict[str, Any] | None:
        route = self.execution_routes.get_active(tenant_id=tenant_id, work_item_id=work_item_id)
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
        """EX5-S2: switching the bound model route always requires a reason and
        is always recorded — the previous route becomes SUPERSEDED, never
        silently dropped."""
        contract = self.execution_routes.switch(
            tenant_id=tenant_id, work_item_id=work_item_id, model_routes=model_routes,
            reason=reason, cycle_id=cycle_id, as_fallback=as_fallback,
        )
        return contract.to_dict()

    async def list_execution_route_audit(
        self, *, tenant_id: str, work_item_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self.execution_routes.audit.list(tenant_id=tenant_id, work_item_id=work_item_id)

    async def list_execution_route_history(
        self, *, tenant_id: str, work_item_id: str | None = None
    ) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.execution_routes.history(tenant_id=tenant_id, work_item_id=work_item_id)]

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
        if routes:
            self._bind_execution_route(tenant_id=tenant_id, work_item_id=work_item_id, model_routes=routes)
        dispatch = build_dispatch_request(
            capsule=capsule,
            timeout_budget_seconds=timeout_budget_seconds,
            model_routes=routes,
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
            wi = self.projection.get(tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=work_item_id)
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
        wi = self.projection.get(tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=work_item_id)
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
        wi = self.projection.get(tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=work_item_id)
        if wi is None:
            raise ExecutionKernelRepositoryError(f"work_item not found: {work_item_id}")
        problem_id = wi.parent_id or str(wi.payload.get("parent_id") or "") or None
        capability_id = None
        if problem_id:
            problem = self.projection.get(tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id)
            if problem:
                capability_id = problem.parent_id or str(problem.payload.get("parent_id") or "") or None
        status = wi.status if wi.status in {"VERIFIED", "FAILED", "BLOCKED", "AWAITING_VERIFICATION"} else wi.status
        proposals = distill_knowledge_proposals(
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            problem_id=problem_id,
            capability_id=capability_id,
            evidence_refs=evidence_refs or list(wi.payload.get("evidence_refs") or []),
            decision=decision,
            work_item_status=status if status in {"VERIFIED", "FAILED"} else "FAILED",  # type: ignore[arg-type]
            lesson_summary=lesson_summary,
            promote_pattern=promote_pattern,
        )
        if status not in {"VERIFIED", "FAILED"}:
            return []
        prefix = command_id_prefix or f"distill:{work_item_id}"
        written: list[dict[str, Any]] = []
        evidence = list(evidence_refs or list(wi.payload.get("evidence_refs") or []))
        for idx, entry in enumerate(proposals):
            cmd = f"{prefix}:{idx}:{new_ulid()[:8]}"
            row = await self.upsert_knowledge(
                tenant_id=tenant_id,
                knowledge_entry_id=entry.knowledge_entry_id,
                knowledge_kind=entry.knowledge_kind,
                content=entry.content,
                command_id=cmd,
                capability_id=entry.capability_id,
                problem_id=entry.problem_id,
                project_id=entry.project_id,
                applicable_capability_ids=list(entry.applicable_capability_ids),
            )
            # EX4-R2-P2-S002: lineage edges from distill
            await self.add_knowledge_graph_edge(
                tenant_id=tenant_id,
                edge_type="DERIVED_FROM",
                from_type="knowledge",
                from_id=entry.knowledge_entry_id,
                to_type="work_item",
                to_id=work_item_id,
                command_id=f"{cmd}:derived:wi",
                provenance=evidence,
            )
            for evidx, evid in enumerate(evidence):
                await self.add_knowledge_graph_edge(
                    tenant_id=tenant_id,
                    edge_type="DERIVED_FROM",
                    from_type="knowledge",
                    from_id=entry.knowledge_entry_id,
                    to_type="evidence",
                    to_id=str(evid),
                    command_id=f"{cmd}:derived:ev:{evidx}",
                    provenance=(str(evid),),
                )
            if entry.knowledge_kind == "pattern" and capability_id:
                await self.add_knowledge_graph_edge(
                    tenant_id=tenant_id,
                    edge_type="ABOUT",
                    from_type="knowledge",
                    from_id=entry.knowledge_entry_id,
                    to_type="capability",
                    to_id=capability_id,
                    command_id=f"{cmd}:about:cap",
                )
            written.append(row)
        return written


def _knowledge_dict(entry: KnowledgeEntry) -> dict[str, Any]:
    return {
        "knowledge_entry_id": entry.knowledge_entry_id,
        "tenant_id": entry.tenant_id,
        "knowledge_kind": entry.knowledge_kind,
        "capability_id": entry.capability_id,
        "problem_id": entry.problem_id,
        "project_id": entry.project_id,
        "content": entry.content,
        "applicable_capability_ids": list(entry.applicable_capability_ids),
        "status": entry.status,
        "revision": entry.revision,
        "valid_from": entry.valid_from,
        "valid_until": entry.valid_until,
        "superseded_by_entry_id": entry.superseded_by_entry_id,
        "confidence": entry.confidence,
        "review_due_at": entry.review_due_at,
    }


def _handle_dict(handle: AdapterHandle) -> dict[str, Any]:
    return {
        "cycle_id": handle.cycle_id,
        "objective_id": handle.objective_id,
        "phase": handle.phase,
        "work_item_id": handle.work_item_id,
        "tenant_id": handle.tenant_id,
        "capsule_content_sha256": handle.capsule_content_sha256,
        "candidate_sha": handle.candidate_sha,
    }


def _outcome_dict(outcome: AdapterOutcome) -> dict[str, Any]:
    return {
        "kind": outcome.kind,
        "evidence_refs": list(outcome.evidence_refs),
        "artifact_refs": list(outcome.artifact_refs),
        "reason_code": outcome.reason_code,
        "proposal_hint": outcome.proposal_hint,
        "cycle_id": outcome.cycle_id,
    }


def require_tenant(tenant_id: str | None) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


__all__ = ["KernelService", "require_tenant"]
