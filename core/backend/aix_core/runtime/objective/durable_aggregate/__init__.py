"""C010/C011: durable Objective/Run aggregate schema + replay (ADR-004 / P1).

Extends the existing append-only `objective_cycle_events` ledger with
reconstructable serving-state tables. Schema ensure is idempotent; no silent
drop. C011 reconstructs decision state from snapshots (see `replay`).
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg  # type: ignore[import-untyped]

from backend.aix_core.runtime.objective.durable_aggregate.replay import (
    DECISION_STATE_SCHEMA,
    REDUCER_INVARIANTS,
    AggregateSnapshot,
    InMemoryProjectionStore,
    ReplayError,
    apply_snapshot_to_objective_fields,
    apply_snapshot_to_run_fields,
    decision_bytes,
    decision_state_objective,
    decision_state_run,
    fold_events,
    reconstruct_decision_pair,
    wrap_objective_snapshot,
    wrap_run_snapshot,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "aix.durable_aggregate.v1"

# Aggregate kinds that must survive process death (C010 DoD).
REQUIRED_AGGREGATE_KINDS = frozenset(
    {
        "objective",
        "run",
        "plan_revision",
        "step_contract",
        "attempt",
        "budget",
        "approval",
        "evidence_ref",
        "completion_decision",
        "command_idempotency",
        "outbox",
    }
)

# Table names — keep stable; migrations evolve columns, not silent renames.
TABLE_OBJECTIVE = "aix_objectives"
TABLE_RUN = "aix_runs"
TABLE_PLAN_REVISION = "aix_plan_revisions"
TABLE_STEP = "aix_step_contracts"
TABLE_ATTEMPT = "aix_attempts"
TABLE_BUDGET = "aix_budgets"
TABLE_APPROVAL = "aix_approvals"
TABLE_EVIDENCE = "aix_evidence_refs"
TABLE_COMPLETION = "aix_completion_decisions"
TABLE_IDEMPOTENCY = "aix_command_idempotency"
TABLE_OUTBOX = "aix_outbox"
TABLE_SNAPSHOT = "aix_aggregate_snapshots"

ALL_TABLES: tuple[str, ...] = (
    TABLE_OBJECTIVE,
    TABLE_RUN,
    TABLE_PLAN_REVISION,
    TABLE_STEP,
    TABLE_ATTEMPT,
    TABLE_BUDGET,
    TABLE_APPROVAL,
    TABLE_EVIDENCE,
    TABLE_COMPLETION,
    TABLE_IDEMPOTENCY,
    TABLE_OUTBOX,
    TABLE_SNAPSHOT,
)

DDL_STATEMENTS: tuple[str, ...] = (
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_OBJECTIVE} (
        tenant_id TEXT NOT NULL,
        objective_id TEXT NOT NULL,
        objective_revision INT NOT NULL DEFAULT 1,
        workhub_project_id TEXT NULL,
        workhub_task_number INT NULL,
        workhub_task_id TEXT NULL,
        repository_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'ADMITTED',
        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        version INT NOT NULL DEFAULT 1,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, objective_id),
        CONSTRAINT aix_objectives_workhub_uniq
            UNIQUE (tenant_id, workhub_project_id, workhub_task_number)
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_RUN} (
        tenant_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        objective_id TEXT NOT NULL,
        legacy_cycle_id TEXT NULL,
        phase TEXT NOT NULL DEFAULT 'STARTING',
        terminal_reason TEXT NULL,
        trace_id TEXT NOT NULL,
        plan_revision INT NOT NULL DEFAULT 1,
        version INT NOT NULL DEFAULT 1,
        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, run_id)
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_PLAN_REVISION} (
        tenant_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        objective_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        revision INT NOT NULL,
        superseded_by TEXT NULL,
        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, plan_id, revision)
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_STEP} (
        tenant_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        objective_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, step_id)
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_ATTEMPT} (
        tenant_id TEXT NOT NULL,
        attempt_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        outcome TEXT NOT NULL DEFAULT 'IN_PROGRESS',
        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, attempt_id)
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_BUDGET} (
        tenant_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        budget_key TEXT NOT NULL,
        limit_value INT NOT NULL,
        consumed_value INT NOT NULL DEFAULT 0,
        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        PRIMARY KEY (tenant_id, run_id, budget_key)
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_APPROVAL} (
        tenant_id TEXT NOT NULL,
        approval_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        plan_revision INT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, approval_id)
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_EVIDENCE} (
        tenant_id TEXT NOT NULL,
        evidence_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        attempt_id TEXT NULL,
        content_hash TEXT NOT NULL,
        uri TEXT NOT NULL,
        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, evidence_id)
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_COMPLETION} (
        tenant_id TEXT NOT NULL,
        decision_id TEXT NOT NULL,
        objective_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        verdict TEXT NOT NULL,
        emitter TEXT NOT NULL,
        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, decision_id),
        CONSTRAINT aix_completion_run_uniq UNIQUE (tenant_id, run_id)
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_IDEMPOTENCY} (
        tenant_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        command_name TEXT NOT NULL,
        response JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, idempotency_key)
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_OUTBOX} (
        tenant_id TEXT NOT NULL,
        outbox_id TEXT NOT NULL,
        aggregate_type TEXT NOT NULL,
        aggregate_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        published_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, outbox_id)
    )
    """.strip(),
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_SNAPSHOT} (
        tenant_id TEXT NOT NULL,
        aggregate_type TEXT NOT NULL,
        aggregate_id TEXT NOT NULL,
        sequence_number INT NOT NULL,
        schema_version TEXT NOT NULL,
        snapshot JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, aggregate_type, aggregate_id, sequence_number)
    )
    """.strip(),
)


INVARIANTS: tuple[str, ...] = (
    "Every Objective/Run/Plan/Step/Attempt is tenant-scoped.",
    "cycle_id maps to run_id during migration (C002).",
    "CompletionDecision is unique per run and only written by Completion Authority.",
    "Command idempotency is unique per (tenant_id, idempotency_key).",
    "Event ledger (objective_cycle_events) remains append-only source of truth;",
    "serving tables are projections reconstructed by C011 reducer.",
    "No default tenant; empty tenant_id is rejected.",
)


def schema_manifest() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "required_aggregate_kinds": sorted(REQUIRED_AGGREGATE_KINDS),
        "tables": list(ALL_TABLES),
        "ddl_statement_count": len(DDL_STATEMENTS),
        "invariants": list(INVARIANTS),
        "depends_on_event_ledger": "objective_cycle_events",
    }


async def _create_index_if_owner(conn: Any, *, index_name: str, statement: str) -> None:
    """CREATE INDEX has no separate privilege in Postgres -- it always requires
    owning the target table, even with IF NOT EXISTS, even to no-op past an
    index that already exists (unlike CREATE TABLE IF NOT EXISTS, which only
    needs schema CREATE and skips existence checks before any ownership check).
    A runtime role with DML-only rights on a table provisioned by a different
    (migration/admin) role hits this on every call, since schema-ensure here
    is not a one-shot latch. Live proof (2026-08-15): with `aix_runs` already
    present but idx_aix_runs_objective missing, this raised
    InsufficientPrivilegeError on every objective admit, surfacing as a hard
    500 through append_event -- i.e. it blocked all new writes, not just an
    optimization. The table is what read/write correctness depends on; the
    index is a query-speed nicety a non-owner role cannot provision, so this
    must not fail the caller."""
    try:
        await conn.execute(statement)
    except asyncpg.InsufficientPrivilegeError:
        logger.warning(
            "ensure_durable_aggregate_schema: skipping %s (runtime role is not "
            "the table owner, cannot CREATE INDEX)",
            index_name,
        )


async def ensure_durable_aggregate_schema(conn: Any) -> None:
    """Idempotent DDL apply. `conn` is an asyncpg connection."""
    for statement in DDL_STATEMENTS:
        await conn.execute(statement)
    # Helpful indexes (IF NOT EXISTS)
    await _create_index_if_owner(
        conn,
        index_name=f"idx_{TABLE_RUN}_objective",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE_RUN}_objective "
            f"ON {TABLE_RUN} (tenant_id, objective_id)"
        ),
    )
    await _create_index_if_owner(
        conn,
        index_name=f"idx_{TABLE_OUTBOX}_unpublished",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE_OUTBOX}_unpublished "
            f"ON {TABLE_OUTBOX} (tenant_id, created_at) WHERE published_at IS NULL"
        ),
    )


__all__ = [
    "SCHEMA_VERSION",
    "REQUIRED_AGGREGATE_KINDS",
    "ALL_TABLES",
    "DDL_STATEMENTS",
    "INVARIANTS",
    "TABLE_SNAPSHOT",
    "schema_manifest",
    "ensure_durable_aggregate_schema",
    "DECISION_STATE_SCHEMA",
    "REDUCER_INVARIANTS",
    "AggregateSnapshot",
    "InMemoryProjectionStore",
    "ReplayError",
    "apply_snapshot_to_objective_fields",
    "apply_snapshot_to_run_fields",
    "decision_bytes",
    "decision_state_objective",
    "decision_state_run",
    "fold_events",
    "reconstruct_decision_pair",
    "wrap_objective_snapshot",
    "wrap_run_snapshot",
]
