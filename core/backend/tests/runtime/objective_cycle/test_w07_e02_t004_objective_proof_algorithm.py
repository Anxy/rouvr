"""AIX-W07-E02-T004: Objective Proof Algorithm Tests.

Verifies:
1. PROVEN_COMPLETE is returned only when all mandatory criteria pass, snapshot is fresh, evidence is valid, and no regressions exist.
2. Failed/unknown criteria, stale snapshots, or regressions block PROVEN_COMPLETE.
3. COMPLETED_FOR_REVIEW remains non-terminal-success requiring review.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.verification.objective_proof import (
    ObjectiveProofEvaluation,
    evaluate_objective_proof,
)


def test_proven_complete_derivation() -> None:
    """All criteria pass, snapshot fresh, evidence valid, no regressions yields PROVEN_COMPLETE."""
    eval_result = evaluate_objective_proof(
        all_mandatory_criteria_passed=True,
        has_unresolved_regressions=False,
        is_snapshot_stale=False,
        has_invalid_evidence=False,
    )

    assert eval_result.status == "PROVEN_COMPLETE"
    assert eval_result.proven_complete is True
    assert len(eval_result.blocking_reasons) == 0


def test_stale_snapshot_blocks_proven_complete() -> None:
    """Stale snapshot prevents PROVEN_COMPLETE status."""
    eval_result = evaluate_objective_proof(
        all_mandatory_criteria_passed=True,
        has_unresolved_regressions=False,
        is_snapshot_stale=True,  # stale!
        has_invalid_evidence=False,
    )

    assert eval_result.proven_complete is False
    assert "REPOSITORY_SNAPSHOT_STALE" in eval_result.blocking_reasons


def test_unresolved_regressions_block_proven_complete() -> None:
    """Unresolved test regressions prevent PROVEN_COMPLETE status."""
    eval_result = evaluate_objective_proof(
        all_mandatory_criteria_passed=True,
        has_unresolved_regressions=True,  # regression!
        is_snapshot_stale=False,
        has_invalid_evidence=False,
    )

    assert eval_result.proven_complete is False
    assert "UNRESOLVED_REGRESSIONS_PRESENT" in eval_result.blocking_reasons


def test_completed_for_review_remains_non_proven() -> None:
    """Proposals requiring review evaluate to COMPLETED_FOR_REVIEW with proven_complete=False."""
    eval_result = evaluate_objective_proof(
        all_mandatory_criteria_passed=True,
        has_unresolved_regressions=False,
        is_snapshot_stale=False,
        has_invalid_evidence=False,
        is_human_review_only=True,
    )

    assert eval_result.status == "COMPLETED_FOR_REVIEW"
    assert eval_result.proven_complete is False
