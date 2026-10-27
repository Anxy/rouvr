"""AIX Feature 12 (Packet 06) — Unit tests for Autonomous Repair Loop & Safety."""

import pytest
from backend.aix_core.runtime.recovery.repair_loop import (
    AutonomousRepairCoordinator,
)
from backend.aix_core.runtime.verification.gap import (
    GapStore,
    GapV1,
)


def test_autonomous_repair_plans_steps_and_transitions_gaps():
    store = GapStore()
    gap = GapV1(
        gap_id="gap-repair-01",
        objective_id="obj-repair-1",
        verification_id="v-1",
        ov_id="OV-002",
        category="OBJECTIVE_DEVIATION",
        expected="SQLite persistence authority",
        observed="PostgreSQL detected without SQLite authority",
        severity="BLOCKER",
        status="OPEN",
    )
    store.put_gap(gap)

    coordinator = AutonomousRepairCoordinator(max_repair_attempts=3)
    result = coordinator.plan_repair_for_open_gaps(
        cycle_id="cyc-1",
        tenant_id="tenant-1",
        objective_id="obj-repair-1",
        gap_store=store,
        existing_steps=(),
        current_attempt=1,
    )

    assert result.status == "REPAIR_PLANNED"
    assert len(result.repair_steps) == 1
    assert "repair_cyc-1_a1_s1" == result.repair_steps[0].id
    assert gap.status == "IN_REPAIR"
    assert gap.repair_attempt_count == 1


def test_autonomous_repair_escalates_when_max_attempts_exceeded():
    store = GapStore()
    gap = GapV1(
        gap_id="gap-repair-02",
        objective_id="obj-repair-2",
        verification_id="v-1",
        ov_id="OV-002",
        category="OBJECTIVE_DEVIATION",
        expected="SQLite persistence authority",
        observed="PostgreSQL detected without SQLite authority",
        severity="BLOCKER",
        status="OPEN",
    )
    store.put_gap(gap)

    coordinator = AutonomousRepairCoordinator(max_repair_attempts=3)
    result = coordinator.plan_repair_for_open_gaps(
        cycle_id="cyc-2",
        tenant_id="tenant-1",
        objective_id="obj-repair-2",
        gap_store=store,
        existing_steps=(),
        current_attempt=4,  # Exceeded limit
    )

    assert result.status == "ESCALATED"
    assert len(result.repair_steps) == 0
    assert gap.status == "ESCALATED"
    assert "Max repair attempts (3) exceeded" in result.escalation_reason


def test_reverification_closes_resolved_gaps():
    store = GapStore()
    gap1 = GapV1(
        gap_id="gap-repair-03",
        objective_id="obj-repair-3",
        verification_id="v-1",
        ov_id="OV-002",
        category="OBJECTIVE_DEVIATION",
        expected="SQLite persistence authority",
        observed="PostgreSQL detected without SQLite authority",
        severity="BLOCKER",
        status="IN_REPAIR",
    )
    gap2 = GapV1(
        gap_id="gap-repair-04",
        objective_id="obj-repair-3",
        verification_id="v-1",
        ov_id="OV-003",
        category="RUNTIME_FAILURE",
        expected="Uvicorn health check 200",
        observed="Uvicorn crashed",
        severity="BLOCKER",
        status="IN_REPAIR",
    )
    store.put_gap(gap1)
    store.put_gap(gap2)

    coordinator = AutonomousRepairCoordinator()
    closed, remaining = coordinator.evaluate_reverification(
        verification_id="v-reverify-1",
        gap_store=store,
        objective_id="obj-repair-3",
        reverified_ov_ids={"OV-002"},  # Only OV-002 fixed
    )

    assert len(closed) == 1
    assert closed[0].gap_id == "gap-repair-03"
    assert closed[0].status == "CLOSED"
    assert closed[0].provenance.get("closed_by_verification_id") == "v-reverify-1"

    assert len(remaining) == 1
    assert remaining[0].gap_id == "gap-repair-04"
    assert remaining[0].status == "IN_REPAIR"
