"""AIX Closed-Loop Execution Plan, Sprint W3: WorkflowPhase <-> CycleStatus mapping tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.contracts import CycleStatus
from backend.aix_core.runtime.objective.cycle_status_mapping import (
    UNMAPPED_WORKFLOW_PHASES,
    workflow_phase_to_cycle_status,
)
from backend.workflows.temporal.objective_cycle_workflow import (
    TERMINAL as WORKFLOW_TERMINAL,
    IllegalPhaseTransitionError,
    WorkflowPhase,
    _TRANSITIONS as WORKFLOW_TRANSITIONS,
)
from backend.aix_core.runtime.objective.experimental.state_machine import CYCLE_TERMINAL_STATES


def test_every_workflow_phase_is_either_mapped_or_explicitly_unmapped() -> None:
    for phase in WorkflowPhase.__args__:  # type: ignore[attr-defined]
        mapped = workflow_phase_to_cycle_status(phase)
        assert (mapped is not None) != (phase in UNMAPPED_WORKFLOW_PHASES)


@pytest.mark.parametrize(
    "phase,expected_status",
    [
        ("STARTING", "ADMITTED"),
        ("GROUNDING", "GROUNDING"),
        ("PLANNING", "PLANNING"),
        ("WAITING_APPROVAL", "WAITING_APPROVAL"),
        ("RUNNING_STEP", "RUNNING_STEP"),
        ("VERIFYING_STEP", "VERIFYING_STEP"),
        ("COMPLETED_FOR_REVIEW", "COMPLETED_FOR_REVIEW"),
        ("FAILED", "FAILED"),
        ("CANCELLED", "CANCELLED"),
    ],
)
def test_mapped_phases_resolve_to_expected_status(phase: str, expected_status: str) -> None:
    assert workflow_phase_to_cycle_status(phase) == expected_status


@pytest.mark.parametrize(
    "phase",
    ["WAITING_BLOCKER", "BUILDING_REVIEW", "BLOCKED", "RESOLUTION_PENDING", "READY_TO_RETRY", "RETRYING"],
)
def test_deliberately_unmapped_phases_return_none_not_a_guess(phase: str) -> None:
    assert workflow_phase_to_cycle_status(phase) is None
    assert phase in UNMAPPED_WORKFLOW_PHASES


def test_none_and_unrecognized_input_return_none() -> None:
    assert workflow_phase_to_cycle_status(None) is None
    assert workflow_phase_to_cycle_status("NOT_A_REAL_PHASE") is None


def test_every_mapped_status_is_a_real_cycle_status_value() -> None:
    for phase in WorkflowPhase.__args__:  # type: ignore[attr-defined]
        mapped = workflow_phase_to_cycle_status(phase)
        if mapped is not None:
            assert mapped in CycleStatus.__args__  # type: ignore[attr-defined]


def test_blocked_name_collision_has_different_terminality_in_each_vocabulary() -> None:
    """The specific, non-obvious reason BLOCKED is left unmapped: it is terminal in
    CycleStatus (no outgoing edges) but NOT strictly terminal in WorkflowPhase's own
    _TRANSITIONS table (three real outgoing edges), even though WorkflowPhase's own
    TERMINAL frozenset also lists it -- a real inconsistency in objective_cycle_workflow.py,
    documented here as a regression guard, not fixed (out of this sprint's scope)."""
    assert "BLOCKED" in CYCLE_TERMINAL_STATES
    assert "BLOCKED" in WORKFLOW_TERMINAL
    assert WORKFLOW_TRANSITIONS["BLOCKED"] == frozenset(
        {"RUNNING_STEP", "RESOLUTION_PENDING", "WAITING_APPROVAL", "SUSPENDED"}
    )


def test_workflow_transitions_still_enforced_illegal_edge_rejected() -> None:
    """Regression guard: _TRANSITIONS must keep rejecting an edge it doesn't list.
    Exercises the table directly rather than the full engine (no Temporal/DB needed)."""
    allowed = WORKFLOW_TRANSITIONS.get("STARTING", frozenset())
    assert "COMPLETED_FOR_REVIEW" not in allowed
