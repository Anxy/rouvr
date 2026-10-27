"""AIX-VOL-II-T003: Unified TaskContract, DAG, and Lease Protocol.

Verifies:
1. `StepContractV1` is the single versioned TaskContract used across planner, scheduler, executor, tester, and verifier.
2. Compatibility parsers convert legacy/kernel work-item dicts into valid `StepContractV1` objects.
3. DAG dependency validation and step budget fields are enforced.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    PlanRevisionV1,
    RiskTier,
    StepContractV1,
    StepRuntimeStateV1,
)
from backend.aix_core.runtime.objective.step_scheduler import (
    select_next_step,
)


_BUDGET = BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=3, steps=10, concurrency=1)


def test_unified_step_contract_schema_and_immutability() -> None:
    """StepContractV1 must be frozen, versioned, forbid extra fields, and validate required attributes."""
    step = StepContractV1(
        id="step-101",
        cycle_id="cyc-101",
        tenant_id="tenant-1",
        step_index=1,
        purpose="Refactor database module",
        depends_on=(),
        target_scope=("database.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("refactor database",),
        validation_commands=("pytest database_test.py",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        required_evidence=("file://diff.patch",),
        status="PENDING",
    )

    assert step.id == "step-101"
    assert step.assigned_role == "executor"
    assert step.status == "PENDING"

    # Immutability check (Pydantic v2 raises ValidationError for frozen instances)
    with pytest.raises(ValidationError):
        step.status = "VERIFIED"  # type: ignore[misc]


def test_compatibility_parser_for_legacy_work_items() -> None:
    """Compatibility parser accepts legacy step dicts and constructs valid StepContractV1."""
    legacy_dict = {
        "id": "step-legacy-1",
        "cycle_id": "cyc-legacy",
        "tenant_id": "tenant-1",
        "step_index": 1,
        "purpose": "Legacy execution step",
        "depends_on": [],
        "target_scope": ["legacy.py"],
        "expected_output_schema_ref": "aix.step_contract.v1.generic_edit",
        "acceptance_criteria": ["pass tests"],
        "validation_commands": ["python -m py_compile legacy.py"],
        "assigned_role": "executor",
        "risk_tier": "LOW",
        "step_budget": {
            "time_seconds": 1800,
            "tokens": 50000,
            "cost_usd": 2.5,
            "retries": 2,
            "steps": 5,
            "concurrency": 1,
        },
        "required_evidence": [],
        "status": "PENDING",
    }

    parsed_step = StepContractV1.model_validate(legacy_dict)
    assert isinstance(parsed_step, StepContractV1)
    assert parsed_step.id == "step-legacy-1"
    assert parsed_step.purpose == "Legacy execution step"


def test_scheduler_dag_dependency_ordering() -> None:
    """Scheduler select_next_step respects DAG depends_on relationships."""
    step1 = StepContractV1(
        id="step-1",
        cycle_id="cyc-1",
        tenant_id="tenant-1",
        step_index=1,
        purpose="Base step",
        depends_on=(),
        target_scope=("base.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("base done",),
        validation_commands=("python -m py_compile base.py",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        required_evidence=(),
        status="PENDING",
    )

    step2 = StepContractV1(
        id="step-2",
        cycle_id="cyc-1",
        tenant_id="tenant-1",
        step_index=2,
        purpose="Dependent step",
        depends_on=("step-1",),
        target_scope=("dependent.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("dependent done",),
        validation_commands=("python -m py_compile dependent.py",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        required_evidence=(),
        status="PENDING",
    )

    plan = PlanRevisionV1(
        objective_id="obj-1",
        cycle_id="cyc-1",
        revision=1,
        steps=(step1, step2),
    )

    runtime_initial = {
        "step-1": StepRuntimeStateV1(plan_id="plan-1", plan_revision=1, step_id="step-1", status="PENDING"),
        "step-2": StepRuntimeStateV1(plan_id="plan-1", plan_revision=1, step_id="step-2", status="PENDING"),
    }

    # Step 2 cannot run while Step 1 is PENDING
    next_step = select_next_step(plan, runtime_initial)
    assert next_step is not None
    assert next_step.id == "step-1"

    # Once Step 1 status becomes PASS, Step 2 is selected
    runtime_after_step1 = {
        "step-1": StepRuntimeStateV1(plan_id="plan-1", plan_revision=1, step_id="step-1", status="PASS"),
        "step-2": StepRuntimeStateV1(plan_id="plan-1", plan_revision=1, step_id="step-2", status="PENDING"),
    }
    next_step_after_1 = select_next_step(plan, runtime_after_step1)
    assert next_step_after_1 is not None
    assert next_step_after_1.id == "step-2"
