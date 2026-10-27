"""EX2-S005/S007: full execution-kernel Postgres DDL (ledger + stores + knowledge)."""

from __future__ import annotations

import logging

import asyncpg  # type: ignore[import-untyped]

from backend.infrastructure.postgres.db_schema_utils import create_index_tolerating_non_owner
from backend.infrastructure.postgres.execution_kernel.ledger_schema import (
    AGGREGATE_TYPES,
    COMMAND_CONSTRAINT_NAME,
    EXECUTION_EVENTS_TABLE,
    SEQUENCE_CONSTRAINT_NAME,
    ensure_execution_kernel_schema,
)

logger = logging.getLogger(__name__)

PROJECTION_TABLE = "aix_execution_projection"
DEPENDENCY_EVENTS_TABLE = "aix_execution_dependency_events"
CHECKPOINT_TABLE = "aix_execution_checkpoints"
KNOWLEDGE_TABLE = "aix_execution_knowledge"
KNOWLEDGE_GRAPH_EDGES_TABLE = "aix_execution_knowledge_graph_edges"
CAPABILITY_REGISTRY_TABLE = "aix_execution_capability_registry"
LEARNING_LIFECYCLE_TABLE = "aix_execution_learning_lifecycle"
# EX4-R3-P9-S001: embedding store. JSONB vectors for first slice; migrate column
# to pgvector `vector(N)` when the Hyeve Postgres extension is available.
KNOWLEDGE_EMBEDDINGS_TABLE = "aix_execution_knowledge_embeddings"
# EX5-S1: durable enterprise intelligence audit trail
ENTERPRISE_AUDIT_TABLE = "aix_execution_enterprise_audit"
# EX5-S2: durable execution route history (model integrity)
EXECUTION_ROUTE_TABLE = "aix_execution_model_routes"
EXECUTION_ROUTE_AUDIT_TABLE = "aix_execution_model_route_audit"
# AIX-EXECUTION-006-S5: actor-level mutation audit — who did what, when.
# A zero-trust audit found execution_kernel's mutating endpoints had no
# actor_id anywhere (not on ExecutionEvent, not in the pre-existing
# enterprise audit sink, which only records tenant_id/event_type/payload).
ACTOR_AUDIT_TABLE = "aix_execution_actor_audit"


async def _ensure_tenant_isolation_policy(conn: asyncpg.Connection, table_name: str) -> None:
    """AIX-EXECUTION-006-S4: same DB-level tenant isolation pattern EX5-S8
    proved for aix_execution_events, extended to the other highest-traffic
    tenant-scoped tables. FORCE is essential (RLS does not apply to the
    owning role by default, and this service connects as the table owner).
    `current_setting(..., true)` returns NULL when unset, and
    `tenant_id = NULL` is never true, so a connection that forgot to scope
    its tenant context sees zero rows / rejects writes — fails closed, not
    open. Every caller of this table's store now sets that session variable
    on every connection acquire (see db_pool.acquire_pooled_connection),
    not just once at connect time, since pooled connections are reused
    across requests/tenants."""
    await conn.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    await conn.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    await conn.execute(f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name}")
    await conn.execute(
        f"""
        CREATE POLICY {table_name}_tenant_isolation ON {table_name}
        USING (tenant_id = current_setting('app.current_tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))
        """
    )


async def ensure_full_execution_kernel_schema(conn: asyncpg.Connection) -> None:
    """Idempotent DDL for all Sprint 002 kernel tables."""
    await ensure_execution_kernel_schema(conn)

    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PROJECTION_TABLE} (
            tenant_id TEXT NOT NULL,
            aggregate_type TEXT NOT NULL
                CHECK (aggregate_type IN (
                    'project', 'capability', 'problem', 'work_item',
                    'decision', 'artifact', 'proposal'
                )),
            aggregate_id TEXT NOT NULL,
            sequence_number INTEGER NOT NULL,
            status TEXT NOT NULL,
            parent_id TEXT,
            payload JSONB NOT NULL,
            state_current BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, aggregate_type, aggregate_id)
        )
        """
    )
    await create_index_tolerating_non_owner(
        conn,
        index_name=f"idx_{PROJECTION_TABLE}_parent",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{PROJECTION_TABLE}_parent "
            f"ON {PROJECTION_TABLE} (tenant_id, aggregate_type, parent_id) "
            f"WHERE state_current = TRUE"
        ),
        logger=logger,
    )
    await _ensure_tenant_isolation_policy(conn, PROJECTION_TABLE)

    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {DEPENDENCY_EVENTS_TABLE} (
            event_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            work_item_id TEXT NOT NULL,
            depends_on_work_item_id TEXT NOT NULL,
            event_type TEXT NOT NULL
                CHECK (event_type IN ('DEPENDENCY_ADDED', 'DEPENDENCY_REMOVED')),
            command_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT aix_execution_dependency_events_tenant_command_key
                UNIQUE (tenant_id, command_id)
        )
        """
    )
    await create_index_tolerating_non_owner(
        conn,
        index_name=f"idx_{DEPENDENCY_EVENTS_TABLE}_work_item",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{DEPENDENCY_EVENTS_TABLE}_work_item "
            f"ON {DEPENDENCY_EVENTS_TABLE} (tenant_id, work_item_id, created_at)"
        ),
        logger=logger,
    )
    await _ensure_tenant_isolation_policy(conn, DEPENDENCY_EVENTS_TABLE)

    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CHECKPOINT_TABLE} (
            checkpoint_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            problem_id TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('CAPTURING', 'CAPTURED', 'SUPERSEDED', 'FAILED')),
            work_items JSONB NOT NULL,
            open_proposals JSONB NOT NULL DEFAULT '[]'::jsonb,
            evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            decision_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            repository_state JSONB NOT NULL,
            superseded_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    await create_index_tolerating_non_owner(
        conn,
        index_name=f"idx_{CHECKPOINT_TABLE}_current",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{CHECKPOINT_TABLE}_current "
            f"ON {CHECKPOINT_TABLE} (tenant_id, problem_id, created_at DESC) "
            f"WHERE status = 'CAPTURED'"
        ),
        logger=logger,
    )

    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {KNOWLEDGE_TABLE} (
            event_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            knowledge_entry_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            event_type TEXT NOT NULL
                CHECK (event_type IN (
                    'KNOWLEDGE_CREATED', 'KNOWLEDGE_UPDATED', 'KNOWLEDGE_ARCHIVED'
                )),
            knowledge_kind TEXT NOT NULL
                CHECK (knowledge_kind IN (
                    'architecture', 'lesson_learned', 'pattern',
                    'business_knowledge', 'technical_knowledge'
                )),
            capability_id TEXT,
            problem_id TEXT,
            project_id TEXT,
            content TEXT NOT NULL,
            applicable_capability_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            valid_from TIMESTAMPTZ,
            valid_until TIMESTAMPTZ,
            superseded_by_entry_id TEXT,
            confidence TEXT NOT NULL DEFAULT 'unknown'
                CHECK (confidence IN ('unknown', 'low', 'medium', 'high')),
            review_due_at TIMESTAMPTZ,
            status TEXT NOT NULL
                CHECK (status IN ('ACTIVE', 'SUPERSEDED', 'DEPRECATED')),
            command_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT aix_execution_knowledge_tenant_command_key
                UNIQUE (tenant_id, command_id),
            CONSTRAINT aix_execution_knowledge_revision_key
                UNIQUE (tenant_id, knowledge_entry_id, revision)
        )
        """
    )
    await create_index_tolerating_non_owner(
        conn,
        index_name=f"idx_{KNOWLEDGE_TABLE}_capability",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{KNOWLEDGE_TABLE}_capability "
            f"ON {KNOWLEDGE_TABLE} (tenant_id, capability_id, status) "
            f"WHERE status = 'ACTIVE'"
        ),
        logger=logger,
    )

    # EX4-R2-P2-S002: Persistent Knowledge Graph edges (Postgres adjacency — not a graph DB)
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {KNOWLEDGE_GRAPH_EDGES_TABLE} (
            edge_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            edge_type TEXT NOT NULL
                CHECK (edge_type IN (
                    'DERIVED_FROM', 'SUPERSEDES', 'ABOUT', 'DECIDED_BY',
                    'USES_PATTERN', 'CONFLICTS_WITH'
                )),
            from_type TEXT NOT NULL,
            from_id TEXT NOT NULL,
            to_type TEXT NOT NULL,
            to_id TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('ACTIVE', 'SUPERSEDED')),
            command_id TEXT NOT NULL,
            authority_context TEXT,
            provenance JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT aix_execution_kge_tenant_command_key
                UNIQUE (tenant_id, command_id)
        )
        """
    )
    await create_index_tolerating_non_owner(
        conn,
        index_name=f"idx_{KNOWLEDGE_GRAPH_EDGES_TABLE}_from",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{KNOWLEDGE_GRAPH_EDGES_TABLE}_from "
            f"ON {KNOWLEDGE_GRAPH_EDGES_TABLE} (tenant_id, from_type, from_id, status)"
        ),
        logger=logger,
    )
    await conn.execute(
        f"""
        ALTER TABLE {KNOWLEDGE_TABLE}
            ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS valid_until TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS superseded_by_entry_id TEXT,
            ADD COLUMN IF NOT EXISTS confidence TEXT NOT NULL DEFAULT 'unknown',
            ADD COLUMN IF NOT EXISTS review_due_at TIMESTAMPTZ
        """
    )
    await conn.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{KNOWLEDGE_TABLE}'::regclass
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%confidence%'
            ) THEN
                ALTER TABLE {KNOWLEDGE_TABLE}
                    ADD CONSTRAINT {KNOWLEDGE_TABLE}_confidence_check
                    CHECK (confidence IN ('unknown', 'low', 'medium', 'high'));
            END IF;
        END $$
        """
    )
    await conn.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{KNOWLEDGE_GRAPH_EDGES_TABLE}'::regclass
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%CONFLICTS_WITH%'
            ) THEN
                ALTER TABLE {KNOWLEDGE_GRAPH_EDGES_TABLE}
                    DROP CONSTRAINT IF EXISTS {KNOWLEDGE_GRAPH_EDGES_TABLE}_edge_type_check;
                ALTER TABLE {KNOWLEDGE_GRAPH_EDGES_TABLE}
                    ADD CONSTRAINT {KNOWLEDGE_GRAPH_EDGES_TABLE}_edge_type_check
                    CHECK (edge_type IN (
                        'DERIVED_FROM', 'SUPERSEDES', 'ABOUT', 'DECIDED_BY',
                        'USES_PATTERN', 'CONFLICTS_WITH'
                    ));
            END IF;
        END $$
        """
    )
    await create_index_tolerating_non_owner(
        conn,
        index_name=f"idx_{KNOWLEDGE_GRAPH_EDGES_TABLE}_to",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{KNOWLEDGE_GRAPH_EDGES_TABLE}_to "
            f"ON {KNOWLEDGE_GRAPH_EDGES_TABLE} (tenant_id, to_type, to_id, status)"
        ),
        logger=logger,
    )

    # EX4-R2-P6-S001: Internal Capability Registry (not ontology Capability; not marketplace)
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CAPABILITY_REGISTRY_TABLE} (
            tenant_id TEXT NOT NULL,
            capability_key TEXT NOT NULL,
            version TEXT NOT NULL,
            display_name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            input_contract JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            output_contract JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            authority_requirements JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            executor_binding TEXT NOT NULL DEFAULT '',
            verification_method TEXT NOT NULL DEFAULT '',
            dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
            cost_class TEXT NOT NULL
                CHECK (cost_class IN ('low', 'medium', 'high')),
            risk_class TEXT NOT NULL
                CHECK (risk_class IN ('low', 'medium', 'high')),
            status TEXT NOT NULL
                CHECK (status IN ('ACTIVE', 'DEPRECATED', 'RETIRED')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, capability_key, version)
        )
        """
    )
    await create_index_tolerating_non_owner(
        conn,
        index_name=f"idx_{CAPABILITY_REGISTRY_TABLE}_active",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{CAPABILITY_REGISTRY_TABLE}_active "
            f"ON {CAPABILITY_REGISTRY_TABLE} (tenant_id, capability_key, status) "
            f"WHERE status = 'ACTIVE'"
        ),
        logger=logger,
    )

    # EX4-R3-P8-S001: Learning promotion lifecycle (DDL ready; in-memory store proven first)
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {LEARNING_LIFECYCLE_TABLE} (
            learning_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            stage TEXT NOT NULL
                CHECK (stage IN (
                    'OBSERVATION', 'CANDIDATE_LESSON', 'VALIDATION',
                    'SCOPE_CLASSIFICATION', 'CONFIDENCE', 'AWAITING_APPROVAL',
                    'ACTIVE_KNOWLEDGE', 'MONITORED_USE', 'REVALIDATION',
                    'SUPERSEDED', 'REJECTED'
                )),
            summary TEXT NOT NULL,
            scope TEXT NOT NULL
                CHECK (scope IN ('local', 'capability', 'organisation')),
            confidence TEXT NOT NULL
                CHECK (confidence IN ('unknown', 'low', 'medium', 'high')),
            influence_level TEXT NOT NULL,
            source_work_item_id TEXT,
            source_knowledge_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            capability_id TEXT,
            problem_id TEXT,
            proposal_id TEXT,
            required_proposal_level INT NOT NULL DEFAULT 1,
            approved_by_actor TEXT,
            active_knowledge_entry_id TEXT,
            auto_promote_allowed BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, learning_id)
        )
        """
    )
    await create_index_tolerating_non_owner(
        conn,
        index_name=f"idx_{LEARNING_LIFECYCLE_TABLE}_stage",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{LEARNING_LIFECYCLE_TABLE}_stage "
            f"ON {LEARNING_LIFECYCLE_TABLE} (tenant_id, stage)"
        ),
        logger=logger,
    )

    # EX4-R3-P9-S001: semantic embeddings (JSONB pilot; pgvector is durable target)
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {KNOWLEDGE_EMBEDDINGS_TABLE} (
            tenant_id TEXT NOT NULL,
            knowledge_entry_id TEXT NOT NULL,
            embedding_model TEXT NOT NULL,
            dims INT NOT NULL,
            embedding JSONB NOT NULL,
            content_sha256 TEXT NOT NULL,
            backend TEXT NOT NULL DEFAULT 'hashed_bow_v1',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, knowledge_entry_id, embedding_model)
        )
        """
    )
    await create_index_tolerating_non_owner(
        conn,
        index_name=f"idx_{KNOWLEDGE_EMBEDDINGS_TABLE}_tenant",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{KNOWLEDGE_EMBEDDINGS_TABLE}_tenant "
            f"ON {KNOWLEDGE_EMBEDDINGS_TABLE} (tenant_id, embedding_model)"
        ),
        logger=logger,
    )

    # EX5-S1: Enterprise intelligence audit (P12)
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ENTERPRISE_AUDIT_TABLE} (
            event_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, event_id)
        )
        """
    )
    await create_index_tolerating_non_owner(
        conn,
        index_name=f"idx_{ENTERPRISE_AUDIT_TABLE}_tenant_time",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{ENTERPRISE_AUDIT_TABLE}_tenant_time "
            f"ON {ENTERPRISE_AUDIT_TABLE} (tenant_id, recorded_at)"
        ),
        logger=logger,
    )

    # EX5-S2: Execution route contract + audit (model integrity)
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {EXECUTION_ROUTE_TABLE} (
            route_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            work_item_id TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('ACTIVE', 'SUPERSEDED', 'FALLBACK')),
            executor_provider TEXT NOT NULL,
            executor_model_id TEXT NOT NULL,
            verifier_provider TEXT,
            verifier_model_id TEXT,
            cycle_id TEXT,
            superseded_by TEXT,
            switch_reason TEXT,
            executor_raw JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            verifier_raw JSONB,
            bound_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, route_id)
        )
        """
    )
    await create_index_tolerating_non_owner(
        conn,
        index_name=f"idx_{EXECUTION_ROUTE_TABLE}_active",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{EXECUTION_ROUTE_TABLE}_active "
            f"ON {EXECUTION_ROUTE_TABLE} (tenant_id, work_item_id, status)"
        ),
        logger=logger,
    )
    await _ensure_tenant_isolation_policy(conn, EXECUTION_ROUTE_TABLE)
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {EXECUTION_ROUTE_AUDIT_TABLE} (
            event_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            work_item_id TEXT NOT NULL,
            route_id TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, event_id)
        )
        """
    )
    await create_index_tolerating_non_owner(
        conn,
        index_name=f"idx_{EXECUTION_ROUTE_AUDIT_TABLE}_wi",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{EXECUTION_ROUTE_AUDIT_TABLE}_wi "
            f"ON {EXECUTION_ROUTE_AUDIT_TABLE} (tenant_id, work_item_id, recorded_at)"
        ),
        logger=logger,
    )
    await _ensure_tenant_isolation_policy(conn, EXECUTION_ROUTE_AUDIT_TABLE)

    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ACTOR_AUDIT_TABLE} (
            audit_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            roles TEXT NOT NULL,
            action TEXT NOT NULL,
            request_id TEXT,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, audit_id)
        )
        """
    )
    await create_index_tolerating_non_owner(
        conn,
        index_name=f"idx_{ACTOR_AUDIT_TABLE}_tenant_time",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{ACTOR_AUDIT_TABLE}_tenant_time "
            f"ON {ACTOR_AUDIT_TABLE} (tenant_id, recorded_at DESC)"
        ),
        logger=logger,
    )
    await _ensure_tenant_isolation_policy(conn, ACTOR_AUDIT_TABLE)


__all__ = [
    "AGGREGATE_TYPES",
    "EXECUTION_EVENTS_TABLE",
    "SEQUENCE_CONSTRAINT_NAME",
    "COMMAND_CONSTRAINT_NAME",
    "PROJECTION_TABLE",
    "DEPENDENCY_EVENTS_TABLE",
    "CHECKPOINT_TABLE",
    "KNOWLEDGE_TABLE",
    "KNOWLEDGE_GRAPH_EDGES_TABLE",
    "CAPABILITY_REGISTRY_TABLE",
    "LEARNING_LIFECYCLE_TABLE",
    "KNOWLEDGE_EMBEDDINGS_TABLE",
    "ENTERPRISE_AUDIT_TABLE",
    "EXECUTION_ROUTE_TABLE",
    "EXECUTION_ROUTE_AUDIT_TABLE",
    "ACTOR_AUDIT_TABLE",
    "ensure_execution_kernel_schema",
    "ensure_full_execution_kernel_schema",
]
