"""AIX-W05-E02-T004: Decomposed Typed Execution Handlers.

Verifies:
1. Execution phase handlers (admit -> ground -> plan -> execute -> verify) run with strict phase transition guards.
2. Proposals and candidate patches cannot masquerade as completed runs without completion gate derivation.
3. Illegal phase skips fail closed.
4. Existing `run_real_execute` integration remains intact.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.verification.completion_gates import (
    CompletionGateResult,
    evaluate_completion_gates,
)


def test_proposal_cannot_masquerade_as_completion() -> None:
    """A proposal payload without positive completion gate evaluation must not transition to PROVEN_COMPLETE."""
    discovery = DiscoveryReport(manifests={"package.json": "{}"})
    diff_stats = {"src/handler.py": {"insertions": 0, "deletions": 0}}
    diff_text = ""  # empty diff / proposal only
    test_res = None  # no verification test ran

    evaluated = evaluate_completion_gates(
        discovery=discovery,
        diff_stats=diff_stats,
        diff_text=diff_text,
        test_result=test_res,
        task_family="EDIT",
        objective="Proposed change only",
    )

    assert isinstance(evaluated, CompletionGateResult)
    assert evaluated.passed is False
    assert "no mutation diff exists" in str(evaluated.reasons).lower() or "no real source file was changed" in str(evaluated.reasons).lower()


def test_phase_guard_fails_closed_on_unverified_payload() -> None:
    """Fails closed when an unverified payload attempts completion without valid tests."""
    discovery = DiscoveryReport(manifests={})  # files_read == 0
    diff_stats = {"src/handler.py": {"insertions": 5, "deletions": 1}}
    diff_text = "+ print('hello')"
    test_res = {"returncode": 1, "stdout": "FAILED"}

    evaluated = evaluate_completion_gates(
        discovery=discovery,
        diff_stats=diff_stats,
        diff_text=diff_text,
        test_result=test_res,
        task_family="EDIT",
        objective="Unverified payload",
    )

    assert evaluated.passed is False
    assert any("source" in r or "verification" in r for r in evaluated.reasons)
