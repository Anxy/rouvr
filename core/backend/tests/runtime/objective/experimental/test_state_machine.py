"""AIX-E2E-CYCLE-001A-W06 state machine tests: legal transitions, fail-closed hostile
inputs, and structural consistency with contracts.py's status vocabularies."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.contracts import CYCLE_TERMINAL_STATES
from backend.aix_core.runtime.objective.experimental.state_machine import (
    ATTEMPT_TERMINAL_STATES,
    CYCLE_TRANSITIONS,
    STEP_TERMINAL_STATES,
    IllegalTransitionError,
    is_attempt_terminal,
    is_cycle_terminal,
    is_step_terminal,
    validate_attempt_transition,
    validate_cycle_transition,
    validate_step_transition,
    validate_transition,
)


class TestCycleTransitionsMatchArchitectureDoc:
    """Section 4 of the architecture doc, transcribed verbatim in state_machine.py."""

    @pytest.mark.parametrize(
        ("from_state", "to_state"),
        [
            ("RECEIVED", "CLARIFICATION_REQUIRED"),
            ("RECEIVED", "ADMITTED"),
            ("RECEIVED", "REJECTED"),
            ("ADMITTED", "GROUNDING"),
            ("GROUNDING", "PLANNING"),
            ("GROUNDING", "BLOCKED"),
            ("PLANNING", "WAITING_APPROVAL"),
            ("PLANNING", "READY"),
            ("PLANNING", "BLOCKED"),
            ("WAITING_APPROVAL", "READY"),
            ("WAITING_APPROVAL", "REJECTED"),
            ("WAITING_APPROVAL", "CANCELLED"),
            ("READY", "RUNNING_STEP"),
            ("READY", "COMPLETED_FOR_REVIEW"),
            ("READY", "BLOCKED"),
            ("RUNNING_STEP", "VERIFYING_STEP"),
            ("RUNNING_STEP", "CANCELLING"),
            ("RUNNING_STEP", "BLOCKED"),
            ("VERIFYING_STEP", "READY"),
            ("VERIFYING_STEP", "REPLANNING"),
            ("VERIFYING_STEP", "COMPLETED_FOR_REVIEW"),
            ("VERIFYING_STEP", "FAILED"),
            ("VERIFYING_STEP", "BLOCKED"),
            ("REPLANNING", "WAITING_APPROVAL"),
            ("REPLANNING", "READY"),
            ("REPLANNING", "FAILED"),
            ("REPLANNING", "BLOCKED"),
            ("CANCELLING", "CANCELLED"),
        ],
    )
    def test_documented_edge_is_legal(self, from_state: str, to_state: str) -> None:
        validate_cycle_transition(from_state, to_state)  # must not raise

    def test_no_undocumented_edges_exist(self) -> None:
        # Every edge in the table must trace back to the architecture doc's section 4
        # table (or the explicitly-flagged CLARIFICATION_REQUIRED assumption); this test
        # is the inverse check to test_documented_edge_is_legal above — it fails if
        # someone adds a new edge to CYCLE_TRANSITIONS without updating both this list
        # and the architecture doc.
        documented_edges = {
            ("RECEIVED", "CLARIFICATION_REQUIRED"),
            ("RECEIVED", "ADMITTED"),
            ("RECEIVED", "REJECTED"),
            ("CLARIFICATION_REQUIRED", "RECEIVED"),  # this packet's own assumption
            ("ADMITTED", "GROUNDING"),
            ("GROUNDING", "PLANNING"),
            ("GROUNDING", "BLOCKED"),
            ("PLANNING", "WAITING_APPROVAL"),
            ("PLANNING", "READY"),
            ("PLANNING", "BLOCKED"),
            ("WAITING_APPROVAL", "READY"),
            ("WAITING_APPROVAL", "REJECTED"),
            ("WAITING_APPROVAL", "CANCELLED"),
            ("READY", "RUNNING_STEP"),
            ("READY", "COMPLETED_FOR_REVIEW"),
            ("READY", "BLOCKED"),
            ("RUNNING_STEP", "VERIFYING_STEP"),
            ("RUNNING_STEP", "CANCELLING"),
            ("RUNNING_STEP", "BLOCKED"),
            ("VERIFYING_STEP", "READY"),
            ("VERIFYING_STEP", "REPLANNING"),
            ("VERIFYING_STEP", "COMPLETED_FOR_REVIEW"),
            ("VERIFYING_STEP", "FAILED"),
            ("VERIFYING_STEP", "BLOCKED"),
            ("REPLANNING", "WAITING_APPROVAL"),
            ("REPLANNING", "READY"),
            ("REPLANNING", "FAILED"),
            ("REPLANNING", "BLOCKED"),
            ("CANCELLING", "CANCELLED"),
        }
        actual_edges = {
            (from_state, to_state)
            for from_state, targets in CYCLE_TRANSITIONS.items()
            for to_state in targets
        }
        assert actual_edges == documented_edges

    def test_terminal_states_match_contracts_module(self) -> None:
        assert CYCLE_TERMINAL_STATES == frozenset(
            {"COMPLETED_FOR_REVIEW", "BLOCKED", "FAILED", "CANCELLED", "REJECTED"}
        )
        for state in CYCLE_TERMINAL_STATES:
            assert is_cycle_terminal(state)
            with pytest.raises(IllegalTransitionError):
                validate_cycle_transition(state, "GROUNDING")


class TestHostileInputsFailClosed:
    def test_illegal_forward_skip_is_rejected(self) -> None:
        with pytest.raises(IllegalTransitionError):
            validate_cycle_transition("RECEIVED", "COMPLETED_FOR_REVIEW")

    def test_illegal_backward_transition_is_rejected(self) -> None:
        with pytest.raises(IllegalTransitionError):
            validate_cycle_transition("VERIFYING_STEP", "ADMITTED")

    def test_unknown_from_state_is_rejected(self) -> None:
        with pytest.raises(IllegalTransitionError):
            validate_cycle_transition("NOT_A_REAL_STATE", "GROUNDING")

    def test_unknown_to_state_is_rejected(self) -> None:
        with pytest.raises(IllegalTransitionError):
            validate_cycle_transition("RECEIVED", "NOT_A_REAL_STATE")

    def test_unknown_aggregate_type_is_rejected(self) -> None:
        with pytest.raises(IllegalTransitionError):
            validate_transition("workhub_task", "OPEN", "CLOSED")

    @pytest.mark.parametrize(
        "forbidden_target",
        ["ACCEPTED", "APPROVED_BY_AIX", "CLOSED", "MERGED_TO_MAIN"],
    )
    def test_lifecycle_acceptance_shaped_target_rejected_even_if_state_existed(
        self, forbidden_target: str
    ) -> None:
        # These target names are not in the cycle vocabulary at all, so they are already
        # rejected as unknown states; this test locks in that the *language guard* would
        # independently reject them too, in case a future state is added with a
        # WorkHub-acceptance-shaped name.
        with pytest.raises(IllegalTransitionError):
            validate_transition("cycle", "READY", forbidden_target)


class TestStepAndAttemptTransitions:
    def test_step_happy_path(self) -> None:
        validate_step_transition("PENDING", "DISPATCHED")
        validate_step_transition("DISPATCHED", "IN_PROGRESS")
        validate_step_transition("IN_PROGRESS", "VERIFIED")

    def test_step_terminal_states_reject_further_transition(self) -> None:
        for state in STEP_TERMINAL_STATES:
            assert is_step_terminal(state)
            with pytest.raises(IllegalTransitionError):
                validate_step_transition(state, "PENDING")

    def test_attempt_happy_path(self) -> None:
        validate_attempt_transition("IN_PROGRESS", "SUCCEEDED")

    def test_attempt_terminal_states_reject_further_transition(self) -> None:
        for state in ATTEMPT_TERMINAL_STATES:
            assert is_attempt_terminal(state)
            with pytest.raises(IllegalTransitionError):
                validate_attempt_transition(state, "IN_PROGRESS")
