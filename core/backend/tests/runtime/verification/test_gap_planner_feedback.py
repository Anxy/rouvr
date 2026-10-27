"""AIX Feature 12 (Packet 05) — Unit tests for Gap -> Planner Feedback."""

import pytest
from backend.aix_core.runtime.objective.contracts import (
    AgentRole,
    BudgetV1,
    RequiredEffectV1,
    StepContractV1,
)
from backend.aix_core.runtime.planner.incremental_replan import (
    GapReport,
    build_repair_step_from_gap,
    perform_incremental_replan,
)
from backend.aix_core.runtime.verification.gap import GapV1


def _make_step(step_id: str, index: int, purpose: str, scope: tuple[str, ...], status: str = "PENDING", depends_on: tuple[str, ...] = ()) -> StepContractV1:
    return StepContractV1(
        id=step_id,
        cycle_id="cyc-test",
        tenant_id="tenant-test",
        step_index=index,
        purpose=purpose,
        target_scope=scope,
        acceptance_criteria=(f"Satisfy {purpose}",),
        validation_commands=("pytest",),
        expected_output_schema_ref="schema://test",
        assigned_role="executor",
        step_budget=BudgetV1(
            time_seconds=60,
            tokens=10000,
            cost_usd=0.1,
            retries=1,
            steps=1,
            concurrency=1,
        ),
        depends_on=depends_on,
        status=status,
    )


def test_gap_report_generates_bounded_repair_step():
    gap = GapV1(
        gap_id="gap-102",
        objective_id="obj-exp",
        verification_id="v-1",
        ov_id="OV-002",
        category="OBJECTIVE_DEVIATION",
        expected="SQLite persistence authority",
        observed="PostgreSQL detected without SQLite authority",
        severity="BLOCKER",
        status="OPEN",
    )

    repair_step = build_repair_step_from_gap(
        gap=gap,
        step_id="repair-step-002",
        step_index=3,
        depends_on=("step-1",),
        fallback_scope=("backend/database.py",),
    )

    assert repair_step.id == "repair-step-002"
    assert "sqlite" in repair_step.purpose.lower() or "ov-002" in repair_step.purpose.lower()
    assert repair_step.target_scope == ("backend/database.py",)
    assert len(repair_step.target_scope) <= 3  # Localized, bounded scope
    assert len(repair_step.acceptance_criteria) >= 2
    assert repair_step.required_effects == (RequiredEffectV1(kind="LITERAL_REPLACE"),)
    assert "test_phase1_terminalization_delegation" in repair_step.validation_commands[0]


def test_incremental_replan_preserves_verified_and_inserts_repair():
    step1 = _make_step("step-1", 1, "Setup basic structure", ("backend/app.py",), status="VERIFIED")
    step2 = _make_step("step-2", 2, "Setup database", ("backend/database.py",), status="FAILED", depends_on=("step-1",))
    step3 = _make_step("step-3", 3, "Build API endpoints", ("backend/api.py",), status="PENDING", depends_on=("step-2",))

    repair_step = _make_step("repair-step-2", 4, "Fix SQLite database setup", ("backend/database.py",), status="PENDING", depends_on=("step-1",))

    result = perform_incremental_replan(
        failed_step_id="step-2",
        existing_steps=(step1, step2, step3),
        replacement_steps=(repair_step,),
    )

    assert "step-1" in result.preserved_verified_step_ids
    assert "step-2" in result.superseded_step_ids
    assert "step-3" in result.superseded_step_ids
    assert "repair-step-2" in result.inserted_step_ids

    statuses = {s.id: s.status for s in result.updated_steps}
    assert statuses["step-1"] == "VERIFIED"
    assert statuses["step-2"] == "FAILED"
    assert statuses["repair-step-2"] == "PENDING"
