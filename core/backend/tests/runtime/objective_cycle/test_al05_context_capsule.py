"""Autonomous Closed-Loop Lane, Packet AL-05: StepContextCapsuleV1 -- the bounded,
step-specific slice of UnderstandingEvidenceV1, not the complete exploration history.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import StepContextCapsuleV1, UnderstandingEvidenceV1
from backend.aix_core.intelligence.context.step_context_capsule import build_step_context_capsule


def _understanding(**overrides: Any) -> UnderstandingEvidenceV1:
    base = dict(
        objective_id="obj-1", cycle_id="cyc-1",
        files_read=("FeeDue.php", "Helper.php", "Other.php"),
        symbols_found=("getDueFeeStudent", "helperFn"),
        dependencies=("visions",),
        observed_problem="x",
        evidence_refs=("toolcall://tc1",),
        understanding_complete=True,
    )
    base.update(overrides)
    return UnderstandingEvidenceV1(**base)  # type: ignore[arg-type]


def test_narrows_to_target_scope_when_given() -> None:
    capsule = build_step_context_capsule(
        step_id="step-1", understanding=_understanding(), target_scope=("FeeDue.php", "getDueFeeStudent"),
    )
    assert isinstance(capsule, StepContextCapsuleV1)
    assert capsule.relevant_files == ("FeeDue.php",)
    assert capsule.relevant_symbols == ("getDueFeeStudent",)
    # Not narrowed by target_scope -- these carry through in full regardless.
    assert capsule.relevant_evidence_refs == ("toolcall://tc1",)
    assert capsule.dependencies == ("visions",)


def test_keeps_full_bounded_set_when_no_target_scope_given() -> None:
    capsule = build_step_context_capsule(step_id="step-1", understanding=_understanding(), target_scope=())
    assert capsule.relevant_files == ("FeeDue.php", "Helper.php", "Other.php")


def test_provenance_links_back_to_understanding_evidence() -> None:
    understanding = _understanding()
    capsule = build_step_context_capsule(step_id="step-1", understanding=understanding)
    assert capsule.understanding_evidence_id == understanding.id


def test_no_understanding_yet_produces_a_legitimate_empty_capsule() -> None:
    capsule = build_step_context_capsule(step_id="step-1", understanding=None, invariants=("do not change return shape",))
    assert capsule.understanding_evidence_id is None
    assert capsule.relevant_files == ()
    assert capsule.invariants == ("do not change return shape",)


def test_relevant_previous_failure_is_a_single_field_not_a_list() -> None:
    capsule = build_step_context_capsule(
        step_id="step-1", understanding=_understanding(),
        relevant_previous_failure="installmentCountByFsmId declared but never used",
    )
    assert capsule.relevant_previous_failure == "installmentCountByFsmId declared but never used"
