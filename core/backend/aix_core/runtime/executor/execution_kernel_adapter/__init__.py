"""AIX Execution Kernel persistence (Sprint 002)."""

from backend.aix_core.runtime.executor.checkpoint import (
    Checkpoint,
    InMemoryCheckpointStore,
    ResumeEngine,
    ResumeResult,
)
from backend.aix_core.runtime.executor.context_builder import (
    ContextBuilder,
    ContextCapsule,
    InMemoryKnowledgeStore,
    KnowledgeEntry,
    canonical_content_hash,
    capsule_to_contract_dict,
)
from backend.aix_core.runtime.executor.dependency_graph import (
    DependencyEdgeEvent,
    InMemoryDependencyGraph,
)
from backend.aix_core.runtime.executor.capability_registry import (
    InMemoryCapabilityRegistry,
    RegisteredCapability,
)
from backend.aix_core.runtime.executor.pattern_engine import (
    discover_pattern_candidates,
    recommend_patterns,
)
from backend.aix_core.runtime.executor.learning_lifecycle import (
    InMemoryLearningLifecycleStore,
    LearningRecord,
    PROPOSAL_LEVEL_FOR_SCOPE,
    learning_record_to_dict,
)
from backend.aix_core.runtime.executor.semantic_retrieval import (
    RANKING_FAILURE_QUERIES,
    TECHNOLOGY_DECISION,
    search_semantic,
    technology_decision_dict,
)
from backend.aix_core.runtime.executor.organizational_knowledge import (
    detect_missing_knowledge,
    query_organizational_knowledge,
    reconcile_cross_project_knowledge,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.model_gateway import resolve_kernel_model_routes
from backend.aix_core.runtime.executor.planning_engine import (
    MAX_WORK_ITEMS,
    bounded_autonomy_for_mutation,
    decompose_plan,
    detect_replan_triggers,
)
from backend.aix_core.runtime.executor.organizational_reasoning import (
    GOLDEN_SCENARIOS,
    reason_organizationally,
)
from backend.aix_core.runtime.executor.enterprise_intelligence import (
    ENTERPRISE_SCENARIOS,
    build_portfolio_intelligence_view,
    enterprise_scenarios,
)
from backend.aix_core.runtime.executor.knowledge_graph import (
    InMemoryKnowledgeGraph,
    KnowledgeGraphEdge,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.model_gateway import resolve_kernel_model_routes
from backend.aix_core.runtime.executor.organizational_knowledge import (
    detect_missing_knowledge,
    query_organizational_knowledge,
    reconcile_cross_project_knowledge,
)
from backend.infrastructure.postgres.execution_kernel.ledger_schema import (
    AGGREGATE_TYPES,
    EXECUTION_EVENTS_TABLE,
    ensure_execution_kernel_schema,
)
from backend.infrastructure.postgres.execution_kernel.schema import (
    CAPABILITY_REGISTRY_TABLE,
    KNOWLEDGE_EMBEDDINGS_TABLE,
    KNOWLEDGE_GRAPH_EDGES_TABLE,
    KNOWLEDGE_TABLE,
    LEARNING_LIFECYCLE_TABLE,
    ensure_full_execution_kernel_schema,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService
from backend.aix_core.runtime.executor.execution_kernel_adapter.adapters import (
    AdapterOutcome,
    DispatchRequest,
    VerificationResult,
    build_dispatch_request,
    distill_knowledge_proposals,
    map_adapter_outcome_without_verification,
    verify_adapter_outcome,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.cycle_adapter import (
    AdapterHandle,
    ObjectiveCycleExecutorAdapter,
    outcome_from_cycle_view,
)
from backend.aix_core.runtime.executor.problem_loop import (
    ProblemStepResult,
    run_problem_step,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.bootstrap import (
    BootstrapSpec,
    BootstrapWorkItem,
    bootstrap_problem_graph,
)
from backend.aix_core.runtime.executor.proposals import (
    raise_proposal,
    resolve_proposal,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.postgres_service import (
    PostgresKernelService,
    build_execution_kernel_service,
)
from backend.infrastructure.postgres.execution_kernel.postgres_stores import (
    PostgresCapabilityRegistry,
    PostgresCheckpointStore,
    PostgresDependencyGraph,
    PostgresKnowledgeGraphStore,
    PostgresKnowledgeStore,
    PostgresProjectionStore,
)
from backend.aix_core.runtime.executor.projection import (
    InMemoryProjectionStore,
    ProjectionRow,
)
from backend.infrastructure.postgres.execution_kernel.repository import (
    ConcurrencyConflictError,
    ExecutionEvent,
    ExecutionKernelRepository,
    ExecutionKernelRepositoryError,
    InMemoryExecutionKernelRepository,
)

__all__ = [
    "AGGREGATE_TYPES",
    "EXECUTION_EVENTS_TABLE",
    "ensure_execution_kernel_schema",
    "ConcurrencyConflictError",
    "ExecutionEvent",
    "ExecutionKernelRepository",
    "ExecutionKernelRepositoryError",
    "InMemoryExecutionKernelRepository",
    "InMemoryProjectionStore",
    "ProjectionRow",
    "InMemoryDependencyGraph",
    "DependencyEdgeEvent",
    "InMemoryCheckpointStore",
    "Checkpoint",
    "ResumeEngine",
    "ResumeResult",
    "ContextBuilder",
    "ContextCapsule",
    "InMemoryKnowledgeStore",
    "KnowledgeEntry",
    "canonical_content_hash",
    "capsule_to_contract_dict",
    "InMemoryKnowledgeGraph",
    "KnowledgeGraphEdge",
    "resolve_kernel_model_routes",
    "query_organizational_knowledge",
    "detect_missing_knowledge",
    "reconcile_cross_project_knowledge",
    "InMemoryCapabilityRegistry",
    "RegisteredCapability",
    "discover_pattern_candidates",
    "recommend_patterns",
    "InMemoryLearningLifecycleStore",
    "LearningRecord",
    "PROPOSAL_LEVEL_FOR_SCOPE",
    "learning_record_to_dict",
    "TECHNOLOGY_DECISION",
    "RANKING_FAILURE_QUERIES",
    "search_semantic",
    "technology_decision_dict",
    "query_organizational_knowledge",
    "detect_missing_knowledge",
    "reconcile_cross_project_knowledge",
    "resolve_kernel_model_routes",
    "MAX_WORK_ITEMS",
    "bounded_autonomy_for_mutation",
    "decompose_plan",
    "detect_replan_triggers",
    "GOLDEN_SCENARIOS",
    "reason_organizationally",
    "ENTERPRISE_SCENARIOS",
    "enterprise_scenarios",
    "build_portfolio_intelligence_view",
    "ensure_full_execution_kernel_schema",
    "KNOWLEDGE_TABLE",
    "KNOWLEDGE_GRAPH_EDGES_TABLE",
    "CAPABILITY_REGISTRY_TABLE",
    "LEARNING_LIFECYCLE_TABLE",
    "KNOWLEDGE_EMBEDDINGS_TABLE",
    "KernelService",
    "PostgresKernelService",
    "PostgresProjectionStore",
    "PostgresDependencyGraph",
    "PostgresKnowledgeGraphStore",
    "PostgresCheckpointStore",
    "PostgresCapabilityRegistry",
    "PostgresKnowledgeStore",
    "build_execution_kernel_service",
    "AdapterOutcome",
    "DispatchRequest",
    "VerificationResult",
    "build_dispatch_request",
    "distill_knowledge_proposals",
    "map_adapter_outcome_without_verification",
    "verify_adapter_outcome",
    "AdapterHandle",
    "ObjectiveCycleExecutorAdapter",
    "outcome_from_cycle_view",
    "ProblemStepResult",
    "run_problem_step",
    "BootstrapSpec",
    "BootstrapWorkItem",
    "bootstrap_problem_graph",
    "raise_proposal",
    "resolve_proposal",
]
