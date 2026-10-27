"""AIX-E2E-CYCLE-001B-W03/W04/W05 repository tests.

Unit tests (no DB) run always. Integration tests need a real Postgres and are gated by
`resolve_test_database_url()` (env var `AIX_OBJECTIVE_CYCLE_TEST_DATABASE_URL` or
`AIX_LEDGER_DAL_DATABASE_URL`) — they are skipped, not faked, when no test database is
configured. This session did not have a known-safe test database to point at (a local
Postgres was found listening on 5432, but its credentials/target database were not
established, and guessing credentials against a live service is not something this
session will do) — see docs/current/benchmarks/AIX-E2E-CYCLE-001/001B-W02..W05/result.md.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from backend.aix_core.runtime.objective.ledger_schema import (
    AGGREGATE_TYPES,
    COMMAND_CONSTRAINT_NAME,
    OBJECTIVE_CYCLE_EVENTS_TABLE,
    SEQUENCE_CONSTRAINT_NAME,
)
from backend.aix_core.runtime.objective.repository import (
    ConcurrencyConflictError,
    ObjectiveCycleRepository,
    ObjectiveCycleRepositoryError,
    ObjectiveCycleUnitOfWork,
    resolve_test_database_url,
)

TEST_DATABASE_URL = resolve_test_database_url()
requires_test_database = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason=(
        "No AIX_OBJECTIVE_CYCLE_TEST_DATABASE_URL / AIX_LEDGER_DAL_DATABASE_URL configured; "
        "integration tests against a real Postgres are skipped, not faked."
    ),
)


class TestSchemaConstants:
    def test_aggregate_types_cover_all_six_domain_concepts(self) -> None:
        assert AGGREGATE_TYPES == {"objective", "cycle", "step", "attempt", "replan", "outcome"}

    def test_plan_revision_accepted_uses_cycle_ledger_aggregate(self) -> None:
        src = (
            Path(__file__).resolve().parents[3]
            / "aix_core"
            / "runtime"
            / "objective"
            / "durable_api_backend.py"
        ).read_text(encoding="utf-8")
        assert 'event_type="plan_revision.accepted"' in src
        assert 'aggregate_type="plan_revision"' not in src
        assert AGGREGATE_TYPES == {"objective", "cycle", "step", "attempt", "replan", "outcome"}

    def test_constraint_names_are_stable(self) -> None:
        # repository.py inspects these by name; renaming either without updating both
        # files silently breaks conflict/idempotency classification.
        assert SEQUENCE_CONSTRAINT_NAME == "objective_cycle_events_tenant_aggregate_seq_key"
        assert COMMAND_CONSTRAINT_NAME == "objective_cycle_events_tenant_command_key"
        assert OBJECTIVE_CYCLE_EVENTS_TABLE == "objective_cycle_events"


class TestRepositoryConstructionFailsClosed:
    def test_empty_database_url_rejected(self) -> None:
        with pytest.raises(ObjectiveCycleRepositoryError):
            ObjectiveCycleRepository("")

    def test_valid_construction_does_not_connect(self) -> None:
        # Constructing a repository must not open a connection or touch any database —
        # only append_event/load_events/latest_snapshot do, and only when awaited.
        repo = ObjectiveCycleRepository("postgresql://example-host-that-does-not-exist/db")
        assert repo is not None


class TestResolveTestDatabaseUrl:
    def test_returns_none_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AIX_OBJECTIVE_CYCLE_TEST_DATABASE_URL", raising=False)
        monkeypatch.delenv("AIX_LEDGER_DAL_DATABASE_URL", raising=False)
        assert resolve_test_database_url() is None

    def test_returns_configured_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AIX_OBJECTIVE_CYCLE_TEST_DATABASE_URL", "postgresql://test/db")
        assert resolve_test_database_url() == "postgresql://test/db"


@requires_test_database
class TestLiveLedgerBehavior:
    """Integration tests. Only run when a real Postgres test database is configured."""

    def _repo(self) -> ObjectiveCycleRepository:
        assert TEST_DATABASE_URL is not None
        return ObjectiveCycleRepository(TEST_DATABASE_URL, ensure_schema=True)

    def test_append_then_load_reconstructs_after_simulated_restart(self) -> None:
        async def scenario() -> None:
            repo = self._repo()
            tenant_id = "tenant-it-1"
            aggregate_id = "cycle-it-1"
            await repo.append_event(
                tenant_id=tenant_id,
                aggregate_type="cycle",
                aggregate_id=aggregate_id,
                expected_sequence_number=1,
                event_type="CYCLE_CREATED",
                command_id="cmd-it-1",
                payload={"status": "ADMITTED"},
            )
            await repo.append_event(
                tenant_id=tenant_id,
                aggregate_type="cycle",
                aggregate_id=aggregate_id,
                expected_sequence_number=2,
                event_type="CYCLE_STATUS_CHANGED",
                command_id="cmd-it-2",
                payload={"status": "GROUNDING"},
            )
            # A fresh repository instance simulates "after process restart": no
            # in-memory state is reused, only what is in PostgreSQL.
            fresh_repo = ObjectiveCycleRepository(TEST_DATABASE_URL)  # type: ignore[arg-type]
            latest = await fresh_repo.latest_snapshot(
                tenant_id=tenant_id, aggregate_type="cycle", aggregate_id=aggregate_id
            )
            assert latest is not None
            assert latest.payload["status"] == "GROUNDING"
            assert latest.sequence_number == 2

        asyncio.run(scenario())

    def test_concurrency_conflict_on_reused_sequence_number(self) -> None:
        async def scenario() -> None:
            repo = self._repo()
            tenant_id = "tenant-it-2"
            aggregate_id = "cycle-it-2"
            await repo.append_event(
                tenant_id=tenant_id,
                aggregate_type="cycle",
                aggregate_id=aggregate_id,
                expected_sequence_number=1,
                event_type="CYCLE_CREATED",
                command_id="cmd-it-3",
                payload={"status": "ADMITTED"},
            )
            with pytest.raises(ConcurrencyConflictError):
                await repo.append_event(
                    tenant_id=tenant_id,
                    aggregate_type="cycle",
                    aggregate_id=aggregate_id,
                    expected_sequence_number=1,  # already taken
                    event_type="CYCLE_CREATED",
                    command_id="cmd-it-4",  # different command, same slot
                    payload={"status": "ADMITTED"},
                )

        asyncio.run(scenario())

    def test_duplicate_command_id_replays_instead_of_erroring(self) -> None:
        async def scenario() -> None:
            repo = self._repo()
            tenant_id = "tenant-it-3"
            aggregate_id = "cycle-it-3"
            first = await repo.append_event(
                tenant_id=tenant_id,
                aggregate_type="cycle",
                aggregate_id=aggregate_id,
                expected_sequence_number=1,
                event_type="CYCLE_CREATED",
                command_id="cmd-it-5",
                payload={"status": "ADMITTED"},
            )
            assert first.replayed is False
            second = await repo.append_event(
                tenant_id=tenant_id,
                aggregate_type="cycle",
                aggregate_id=aggregate_id,
                expected_sequence_number=1,  # same command retried with same args
                event_type="CYCLE_CREATED",
                command_id="cmd-it-5",  # same command_id
                payload={"status": "ADMITTED"},
            )
            assert second.replayed is True
            assert second.id == first.id

        asyncio.run(scenario())

    def test_unit_of_work_appends_two_events_atomically(self) -> None:
        async def scenario() -> None:
            tenant_id = "tenant-it-4"
            async with ObjectiveCycleUnitOfWork(TEST_DATABASE_URL, ensure_schema=True) as uow:  # type: ignore[arg-type]
                await uow.append(
                    tenant_id=tenant_id,
                    aggregate_type="step",
                    aggregate_id="step-it-1",
                    expected_sequence_number=1,
                    event_type="STEP_ATTEMPT_COMPLETED",
                    command_id="cmd-it-uow-1",
                    payload={"status": "VERIFIED"},
                )
                await uow.append(
                    tenant_id=tenant_id,
                    aggregate_type="cycle",
                    aggregate_id="cycle-it-4",
                    expected_sequence_number=1,
                    event_type="CYCLE_CURSOR_ADVANCED",
                    command_id="cmd-it-uow-2",
                    payload={"current_step_cursor": None},
                )
            repo = self._repo()
            step_latest = await repo.latest_snapshot(
                tenant_id=tenant_id, aggregate_type="step", aggregate_id="step-it-1"
            )
            cycle_latest = await repo.latest_snapshot(
                tenant_id=tenant_id, aggregate_type="cycle", aggregate_id="cycle-it-4"
            )
            assert step_latest is not None and step_latest.payload["status"] == "VERIFIED"
            assert cycle_latest is not None

        asyncio.run(scenario())
