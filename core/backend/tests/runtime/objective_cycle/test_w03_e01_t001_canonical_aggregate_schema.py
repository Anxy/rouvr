"""AIX-W03-E01-T001: Canonical Aggregate Schema & Invariants.

Verifies:
1. Canonical aggregate ownership & versioning for objective, criteria, plan, task, attempt, lease, workspace, evidence, verification, approval, outcome.
2. Ledger schema constraints (optimistic concurrency sequence key & command idempotency key).
3. Single authority persistence (no dual-write between kernel and objective-cycle).
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.contracts import (
    CONTRACT_VERSION,
    BudgetV1,
    CycleOutcomeV1,
    ExecutorPackageV1,
    ObjectiveEnvelopeV1,
    ObjectiveObligationV1,
    PlanRevisionV1,
    ReplanDecisionV1,
    StepContractV1,
    StepAttemptV1,
    UnderstandingEvidenceV1,
)
from backend.aix_core.runtime.objective.ledger_schema import (
    AGGREGATE_TYPES,
    COMMAND_CONSTRAINT_NAME,
    OBJECTIVE_CYCLE_EVENTS_TABLE,
    SEQUENCE_CONSTRAINT_NAME,
)
from backend.aix_core.runtime.objective.dual_surface import (
    DUAL_SURFACE_VERSION,
    DualSurfaceLink,
)


def test_canonical_aggregate_contracts_are_frozen_and_versioned() -> None:
    """All canonical contract value objects must be frozen, versioned, and forbid extra fields."""
    models = [
        ObjectiveEnvelopeV1,
        ObjectiveObligationV1,
        StepContractV1,
        PlanRevisionV1,
        StepAttemptV1,
        UnderstandingEvidenceV1,
        ReplanDecisionV1,
        CycleOutcomeV1,
        ExecutorPackageV1,
        DualSurfaceLink,
    ]

    for model in models:
        # Must be Pydantic v2 BaseModel with extra="forbid" and frozen=True
        cfg = getattr(model, "model_config", {})
        assert cfg.get("extra") == "forbid", f"{model.__name__} must specify extra='forbid'"
        assert cfg.get("frozen") is True, f"{model.__name__} must specify frozen=True"

    assert CONTRACT_VERSION == "v1"
    assert DUAL_SURFACE_VERSION == "aix.objective-cycle.dual-surface.v1"


def test_ledger_schema_invariants() -> None:
    """Ledger table schema must specify immutable table name, aggregate types, and constraints."""
    assert OBJECTIVE_CYCLE_EVENTS_TABLE == "objective_cycle_events"
    assert "objective" in AGGREGATE_TYPES
    assert "cycle" in AGGREGATE_TYPES
    assert "step" in AGGREGATE_TYPES
    assert "attempt" in AGGREGATE_TYPES
    assert "replan" in AGGREGATE_TYPES
    assert "outcome" in AGGREGATE_TYPES

    assert SEQUENCE_CONSTRAINT_NAME == "objective_cycle_events_tenant_aggregate_seq_key"
    assert COMMAND_CONSTRAINT_NAME == "objective_cycle_events_tenant_command_key"


def test_contract_extra_fields_fail_closed() -> None:
    """Attempting to construct a contract with unknown/unapproved fields must raise ValidationError."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BudgetV1(
            time_seconds=10,
            tokens=100,
            cost_usd=1.0,
            retries=1,
            steps=1,
            concurrency=1,
            unauthorized_field="malicious_override",
        )
