"""EX2-S001: execution-kernel ledger unit tests (in-memory; no Postgres required)."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter import (
    AGGREGATE_TYPES,
    ConcurrencyConflictError,
    ExecutionKernelRepositoryError,
    InMemoryExecutionKernelRepository,
)


@pytest.mark.asyncio
async def test_append_and_latest_snapshot() -> None:
    repo = InMemoryExecutionKernelRepository()
    first = await repo.append_event(
        tenant_id="t1",
        aggregate_type="work_item",
        aggregate_id="wi_1",
        expected_sequence_number=1,
        event_type="WORK_ITEM_CREATED",
        command_id="cmd_1",
        payload={"status": "PENDING", "title": "Build ledger"},
    )
    assert first.sequence_number == 1
    assert first.replayed is False

    second = await repo.append_event(
        tenant_id="t1",
        aggregate_type="work_item",
        aggregate_id="wi_1",
        expected_sequence_number=2,
        event_type="WORK_ITEM_STATUS_CHANGED",
        command_id="cmd_2",
        payload={"status": "DISPATCHED", "title": "Build ledger"},
    )
    assert second.sequence_number == 2

    latest = await repo.latest_snapshot(tenant_id="t1", aggregate_type="work_item", aggregate_id="wi_1")
    assert latest is not None
    assert latest.payload["status"] == "DISPATCHED"
    assert latest.command_id == "cmd_2"


@pytest.mark.asyncio
async def test_idempotent_command_replay() -> None:
    repo = InMemoryExecutionKernelRepository()
    await repo.append_event(
        tenant_id="t1",
        aggregate_type="problem",
        aggregate_id="pr_1",
        expected_sequence_number=1,
        event_type="PROBLEM_CREATED",
        command_id="cmd_dup",
        payload={"status": "EXECUTING"},
    )
    replayed = await repo.append_event(
        tenant_id="t1",
        aggregate_type="problem",
        aggregate_id="pr_1",
        expected_sequence_number=1,
        event_type="PROBLEM_CREATED",
        command_id="cmd_dup",
        payload={"status": "EXECUTING"},
    )
    assert replayed.replayed is True
    events = await repo.load_events(tenant_id="t1", aggregate_type="problem", aggregate_id="pr_1")
    assert len(events) == 1


@pytest.mark.asyncio
async def test_concurrency_conflict() -> None:
    repo = InMemoryExecutionKernelRepository()
    await repo.append_event(
        tenant_id="t1",
        aggregate_type="project",
        aggregate_id="proj_1",
        expected_sequence_number=1,
        event_type="PROJECT_CREATED",
        command_id="cmd_a",
        payload={"name": "ERP"},
    )
    with pytest.raises(ConcurrencyConflictError):
        await repo.append_event(
            tenant_id="t1",
            aggregate_type="project",
            aggregate_id="proj_1",
            expected_sequence_number=1,
            event_type="PROJECT_CREATED",
            command_id="cmd_b",
            payload={"name": "ERP"},
        )


@pytest.mark.asyncio
async def test_fail_closed_without_tenant() -> None:
    repo = InMemoryExecutionKernelRepository()
    with pytest.raises(ExecutionKernelRepositoryError, match="tenant_id"):
        await repo.append_event(
            tenant_id="  ",
            aggregate_type="capability",
            aggregate_id="cap_1",
            expected_sequence_number=1,
            event_type="CAPABILITY_CREATED",
            command_id="cmd_x",
            payload={},
        )


@pytest.mark.asyncio
async def test_reject_invalid_aggregate_and_operation_absent() -> None:
    repo = InMemoryExecutionKernelRepository()
    assert "operation" not in AGGREGATE_TYPES
    with pytest.raises(ExecutionKernelRepositoryError, match="invalid aggregate_type"):
        await repo.append_event(
            tenant_id="t1",
            aggregate_type="operation",
            aggregate_id="op_1",
            expected_sequence_number=1,
            event_type="OP",
            command_id="cmd_op",
            payload={},
        )


@pytest.mark.asyncio
async def test_tenant_isolation() -> None:
    repo = InMemoryExecutionKernelRepository()
    await repo.append_event(
        tenant_id="t1",
        aggregate_type="decision",
        aggregate_id="d1",
        expected_sequence_number=1,
        event_type="DECISION_RECORDED",
        command_id="cmd_t1",
        payload={"verdict": "ACCEPT"},
    )
    events_t2 = await repo.load_events(tenant_id="t2", aggregate_type="decision", aggregate_id="d1")
    assert events_t2 == []
