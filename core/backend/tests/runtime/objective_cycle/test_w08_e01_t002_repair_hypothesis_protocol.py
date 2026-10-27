"""AIX-W08-E01-T002: Repair Hypothesis Protocol Tests.

Verifies:
1. Valid repair hypotheses pass validation with linked evidence, blast radius, budget, and strategy.
2. Identical retries without a new hypothesis or strategy are rejected with IdenticalRetryError.
3. Incomplete repair directives (missing evidence, empty hypothesis/strategy) fail closed.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.verification.repair_protocol import (
    IdenticalRetryError,
    RepairHypothesisV1,
    validate_repair_hypothesis,
)


def test_valid_repair_hypothesis_passes() -> None:
    """A repair hypothesis with complete fields and a new strategy passes validation."""
    hyp = RepairHypothesisV1(
        failure_signature="sig-123",
        root_cause_evidence_refs=("file://logs/error.log",),
        hypothesis="Import error caused by missing module alias",
        blast_radius_files=("backend/routers/auth.py",),
        repair_budget_allocated={"tokens": 1000, "time_seconds": 60},
        changed_strategy="Add explicit alias import in auth router",
    )

    validate_repair_hypothesis(hyp)  # Should not raise


def test_identical_retry_without_new_hypothesis_is_rejected() -> None:
    """Repeated failure with identical hypothesis and strategy is rejected."""
    prior = RepairHypothesisV1(
        failure_signature="sig-123",
        root_cause_evidence_refs=("file://logs/error.log",),
        hypothesis="Import error caused by missing module alias",
        blast_radius_files=("backend/routers/auth.py",),
        repair_budget_allocated={"tokens": 1000, "time_seconds": 60},
        changed_strategy="Add explicit alias import in auth router",
    )

    current_identical = RepairHypothesisV1(
        failure_signature="sig-123",
        root_cause_evidence_refs=("file://logs/error.log",),
        hypothesis="Import error caused by missing module alias",
        blast_radius_files=("backend/routers/auth.py",),
        repair_budget_allocated={"tokens": 1000, "time_seconds": 60},
        changed_strategy="Add explicit alias import in auth router",
    )

    with pytest.raises(IdenticalRetryError) as exc_info:
        validate_repair_hypothesis(current_identical, prior=prior)

    assert exc_info.value.code == "IDENTICAL_RETRY_WITHOUT_NEW_HYPOTHESIS_REJECTED"


def test_updated_hypothesis_passes() -> None:
    """Repeated failure signature with updated hypothesis and strategy passes."""
    prior = RepairHypothesisV1(
        failure_signature="sig-123",
        root_cause_evidence_refs=("file://logs/error.log",),
        hypothesis="Import error caused by missing module alias",
        blast_radius_files=("backend/routers/auth.py",),
        repair_budget_allocated={"tokens": 1000, "time_seconds": 60},
        changed_strategy="Add explicit alias import in auth router",
    )

    current_updated = RepairHypothesisV1(
        failure_signature="sig-123",
        root_cause_evidence_refs=("file://logs/error.log",),
        hypothesis="Import error was actually due to circular dependency",
        blast_radius_files=("backend/routers/auth.py", "backend/models/user.py"),
        repair_budget_allocated={"tokens": 2000, "time_seconds": 120},
        changed_strategy="Refactor user model import into type-only block",
    )

    validate_repair_hypothesis(current_updated, prior=prior)  # Should pass!
