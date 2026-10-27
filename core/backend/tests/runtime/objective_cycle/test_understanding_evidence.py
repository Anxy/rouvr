"""AIX Closed-Loop Execution Plan, Sprint W4: UnderstandingEvidenceV1 construction."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.aix_core.runtime.objective.contracts import UnderstandingEvidenceV1
from backend.aix_core.runtime.planner.execution_context import (
    ExecutionContext,
    FileReadRecord,
    SymbolRecord,
)
from backend.aix_core.runtime.objective.understanding_evidence import build_understanding_evidence


def _context_with_evidence() -> ExecutionContext:
    context = ExecutionContext(objective_id="obj-1")
    context.files_read.append(
        FileReadRecord(path="a.php", file_hash="h1", fingerprint="fp1", tool_call_id="tc1")
    )
    context.symbols_resolved.append(SymbolRecord(name="getDueFeeStudent", tool_call_id="tc2"))
    context.repositories_explored.append("visions")
    context.mark_understanding_complete()
    return context


def test_build_reshapes_existing_context_evidence_without_recomputing() -> None:
    context = _context_with_evidence()
    evidence = build_understanding_evidence(
        objective_id="obj-1", cycle_id="cyc-1", context=context, observed_problem="N+1 queries in the loop",
    )
    assert isinstance(evidence, UnderstandingEvidenceV1)
    assert evidence.objective_id == "obj-1"
    assert evidence.cycle_id == "cyc-1"
    assert evidence.files_read == ("a.php",)
    assert evidence.symbols_found == ("getDueFeeStudent",)
    assert evidence.dependencies == ("visions",)
    assert evidence.understanding_complete is True
    assert evidence.evidence_refs == ("toolcall://tc1",)


def test_observed_problem_is_required_non_empty() -> None:
    context = _context_with_evidence()
    with pytest.raises(ValidationError):
        build_understanding_evidence(
            objective_id="obj-1", cycle_id=None, context=context, observed_problem="",
        )


def test_frozen_and_versioned_like_the_rest_of_contracts() -> None:
    context = _context_with_evidence()
    evidence = build_understanding_evidence(
        objective_id="obj-1", cycle_id=None, context=context, observed_problem="x",
    )
    assert evidence.version == "v1"
    with pytest.raises(ValidationError):
        evidence.observed_problem = "mutated"  # type: ignore[misc]


def test_empty_context_produces_empty_but_valid_evidence() -> None:
    context = ExecutionContext(objective_id="obj-2")
    evidence = build_understanding_evidence(
        objective_id="obj-2", cycle_id=None, context=context, observed_problem="nothing explored yet",
    )
    assert evidence.files_read == ()
    assert evidence.understanding_complete is False
