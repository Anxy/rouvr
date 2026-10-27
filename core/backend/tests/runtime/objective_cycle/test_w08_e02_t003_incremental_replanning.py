"""AIX-W08-E02-T003: Incremental Replanning Tests.

Verifies:
1. Verified independent steps are preserved when invalid assumptions occur downstream.
2. Only dependent future steps and the failed step are superseded.
3. New replacement steps are cleanly integrated.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    RiskTier,
    StepContractV1,
)
from backend.aix_core.runtime.planner.incremental_replan import (
    perform_incremental_replan,
)

_BUDGET = BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=3, steps=10, concurrency=1)


def _make_step(step_id: str, depends_on: tuple[str, ...], status: str) -> StepContractV1:
    return StepContractV1(
        id=step_id,
        cycle_id="cyc-1",
        tenant_id="tenant-1",
        step_index=int(step_id.split("-")[-1]),
        purpose=f"Step {step_id}",
        depends_on=depends_on,
        target_scope=("backend/utils.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("done",),
        validation_commands=("pytest",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        required_evidence=("file://diff.patch",),
        status=status,
    )


def test_incremental_replan_preserves_verified_steps() -> None:
    """Replanning a failed step supersedes only downstream dependents while preserving verified steps."""
    # Graph:
    # step-1 (VERIFIED)
    # step-2 (VERIFIED, independent)
    # step-3 (FAILED, depends on step-1)
    # step-4 (PENDING, depends on step-3)
    s1 = _make_step("step-1", (), "VERIFIED")
    s2 = _make_step("step-2", (), "VERIFIED")
    s3 = _make_step("step-3", ("step-1",), "FAILED")
    s4 = _make_step("step-4", ("step-3",), "PENDING")

    r5 = _make_step("step-5", ("step-1",), "PENDING")

    res = perform_incremental_replan(
        failed_step_id="step-3",
        existing_steps=(s1, s2, s3, s4),
        replacement_steps=(r5,),
    )

    assert "step-1" in res.preserved_verified_step_ids
    assert "step-2" in res.preserved_verified_step_ids
    assert res.superseded_step_ids == ("step-3", "step-4")
    assert res.inserted_step_ids == ("step-5",)

    # Check updated steps list
    updated_map = {s.id: s.status for s in res.updated_steps}
    assert updated_map["step-1"] == "VERIFIED"
    assert updated_map["step-2"] == "VERIFIED"
    assert updated_map["step-3"] == "SUPERSEDED"
    assert updated_map["step-4"] == "SUPERSEDED"
    assert updated_map["step-5"] == "PENDING"
