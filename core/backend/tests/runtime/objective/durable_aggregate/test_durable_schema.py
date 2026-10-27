"""C010 durable aggregate schema contract tests."""

from __future__ import annotations

from backend.aix_core.runtime.objective.durable_aggregate import (
    ALL_TABLES,
    DDL_STATEMENTS,
    REQUIRED_AGGREGATE_KINDS,
    SCHEMA_VERSION,
    schema_manifest,
)


def test_schema_version_and_required_kinds() -> None:
    assert SCHEMA_VERSION == "aix.durable_aggregate.v1"
    for kind in (
        "objective",
        "run",
        "plan_revision",
        "step_contract",
        "attempt",
        "budget",
        "approval",
        "evidence_ref",
        "completion_decision",
    ):
        assert kind in REQUIRED_AGGREGATE_KINDS


def test_tables_cover_required_kinds() -> None:
    joined = " ".join(ALL_TABLES)
    assert "aix_objectives" in joined
    assert "aix_runs" in joined
    assert "aix_plan_revisions" in joined
    assert "aix_step_contracts" in joined
    assert "aix_attempts" in joined
    assert "aix_budgets" in joined
    assert "aix_approvals" in joined
    assert "aix_evidence_refs" in joined
    assert "aix_completion_decisions" in joined
    assert "aix_command_idempotency" in joined
    assert "aix_outbox" in joined
    assert len(ALL_TABLES) == len(DDL_STATEMENTS)


def test_ddl_is_idempotent_create_if_not_exists() -> None:
    for stmt in DDL_STATEMENTS:
        assert "CREATE TABLE IF NOT EXISTS" in stmt
        assert "DROP TABLE" not in stmt.upper()
        assert "tenant_id" in stmt


def test_completion_unique_per_run() -> None:
    completion_ddl = next(s for s in DDL_STATEMENTS if "aix_completion_decisions" in s)
    assert "aix_completion_run_uniq" in completion_ddl


def test_schema_manifest_machine_readable() -> None:
    manifest = schema_manifest()
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["ddl_statement_count"] == len(DDL_STATEMENTS)
    assert "objective_cycle_events" in manifest["depends_on_event_ledger"]
