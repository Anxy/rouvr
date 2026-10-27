"""AIX-E2E-CYCLE-001A-W06: legal state machine for cycle, step and attempt transitions.

Cycle transitions are copied verbatim from the architecture doc's section 4 table
(`docs/current/architecture/AIX-E2E-CYCLE-001-GOVERNED-OBJECTIVE-CYCLE.md`). Step and
attempt transitions are not specified there at that granularity and are this packet's own
bounded, fail-closed design — narrower additions, never a relaxation of the cycle table.

Every transition function fails closed: an unmapped `from_state`, an edge not listed, a
transition out of a terminal state, or a target state name that reads like a WorkHub
lifecycle-acceptance verb all raise `IllegalTransitionError`. Nothing here silently repairs
or defaults an unknown state.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.aix_core.runtime.objective.contracts import (
    CYCLE_TERMINAL_STATES,
    AttemptOutcome,
    CycleStatus,
    StepStatus,
)

_LIFECYCLE_ACCEPTANCE_SUBSTRINGS = ("ACCEPT", "APPROV", "CLOS", "MERG")
_LIFECYCLE_ACCEPTANCE_EXEMPT = frozenset({"WAITING_APPROVAL", "COMPLETED_FOR_REVIEW"})


class IllegalTransitionError(ValueError):
    """Raised for any transition this state machine does not explicitly permit."""


@dataclass(frozen=True)
class TransitionCheck:
    aggregate_type: str
    from_state: str
    to_state: str


# --- Cycle transitions: verbatim from architecture doc section 4 ---
CYCLE_TRANSITIONS: dict[str, frozenset[str]] = {
    "RECEIVED": frozenset({"CLARIFICATION_REQUIRED", "ADMITTED", "REJECTED"}),
    # Not specified in architecture doc section 4; the only sensible resumption after a
    # human answers a clarification question is back into intake. Recorded as an explicit
    # assumption (see DRIFT note in 001A evidence), not silently treated as canonical.
    "CLARIFICATION_REQUIRED": frozenset({"RECEIVED"}),
    "ADMITTED": frozenset({"GROUNDING"}),
    "GROUNDING": frozenset({"PLANNING", "BLOCKED"}),
    "PLANNING": frozenset({"WAITING_APPROVAL", "READY", "BLOCKED"}),
    "WAITING_APPROVAL": frozenset({"READY", "REJECTED", "CANCELLED"}),
    "READY": frozenset({"RUNNING_STEP", "COMPLETED_FOR_REVIEW", "BLOCKED"}),
    "RUNNING_STEP": frozenset({"VERIFYING_STEP", "CANCELLING", "BLOCKED"}),
    "VERIFYING_STEP": frozenset({"READY", "REPLANNING", "COMPLETED_FOR_REVIEW", "FAILED", "BLOCKED"}),
    "REPLANNING": frozenset({"WAITING_APPROVAL", "READY", "FAILED", "BLOCKED"}),
    "CANCELLING": frozenset({"CANCELLED"}),
    # Terminal states: no outgoing edges.
    "CANCELLED": frozenset(),
    "COMPLETED_FOR_REVIEW": frozenset(),
    "FAILED": frozenset(),
    "BLOCKED": frozenset(),
    "REJECTED": frozenset(),
}

assert set(CYCLE_TRANSITIONS) == set(CycleStatus.__args__), (  # type: ignore[attr-defined]
    "CYCLE_TRANSITIONS must cover exactly the CycleStatus vocabulary in contracts.py"
)
assert frozenset(state for state, edges in CYCLE_TRANSITIONS.items() if not edges) == CYCLE_TERMINAL_STATES, (
    "states with no outgoing edges must equal CYCLE_TERMINAL_STATES exactly"
)

# --- Step transitions: this packet's own bounded design ---
STEP_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"DISPATCHED", "BLOCKED", "SKIPPED"}),
    "DISPATCHED": frozenset({"IN_PROGRESS", "BLOCKED"}),
    "IN_PROGRESS": frozenset({"VERIFIED", "FAILED", "BLOCKED"}),
    "VERIFIED": frozenset(),
    "FAILED": frozenset(),
    "BLOCKED": frozenset(),
    "SKIPPED": frozenset(),
}

assert set(STEP_TRANSITIONS) == set(StepStatus.__args__), (  # type: ignore[attr-defined]
    "STEP_TRANSITIONS must cover exactly the StepStatus vocabulary in contracts.py"
)

STEP_TERMINAL_STATES: frozenset[str] = frozenset(
    state for state, edges in STEP_TRANSITIONS.items() if not edges
)

# --- Attempt transitions: this packet's own bounded design ---
ATTEMPT_TRANSITIONS: dict[str, frozenset[str]] = {
    "IN_PROGRESS": frozenset({"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"}),
    "SUCCEEDED": frozenset(),
    "FAILED": frozenset(),
    "TIMED_OUT": frozenset(),
    "CANCELLED": frozenset(),
}

assert set(ATTEMPT_TRANSITIONS) == set(AttemptOutcome.__args__), (  # type: ignore[attr-defined]
    "ATTEMPT_TRANSITIONS must cover exactly the AttemptOutcome vocabulary in contracts.py"
)

ATTEMPT_TERMINAL_STATES: frozenset[str] = frozenset(
    state for state, edges in ATTEMPT_TRANSITIONS.items() if not edges
)

_TABLES: dict[str, dict[str, frozenset[str]]] = {
    "cycle": CYCLE_TRANSITIONS,
    "step": STEP_TRANSITIONS,
    "attempt": ATTEMPT_TRANSITIONS,
}


def _reject_lifecycle_acceptance_language(aggregate_type: str, to_state: str) -> None:
    if to_state in _LIFECYCLE_ACCEPTANCE_EXEMPT:
        return
    upper = to_state.upper()
    if any(token in upper for token in _LIFECYCLE_ACCEPTANCE_SUBSTRINGS):
        raise IllegalTransitionError(
            f"{aggregate_type} transition target {to_state!r} reads as a WorkHub lifecycle-"
            "acceptance verb; AIX internal state must never claim acceptance/approval/closure"
        )


def validate_transition(aggregate_type: str, from_state: str, to_state: str) -> None:
    """Raise `IllegalTransitionError` unless `from_state` -> `to_state` is an explicitly
    permitted edge for `aggregate_type` ('cycle', 'step', or 'attempt')."""
    table = _TABLES.get(aggregate_type)
    if table is None:
        raise IllegalTransitionError(f"unknown aggregate_type: {aggregate_type!r}")

    _reject_lifecycle_acceptance_language(aggregate_type, to_state)

    if from_state not in table:
        raise IllegalTransitionError(f"unknown {aggregate_type} from_state: {from_state!r}")
    if to_state not in table:
        raise IllegalTransitionError(f"unknown {aggregate_type} to_state: {to_state!r}")

    allowed = table[from_state]
    if not allowed:
        raise IllegalTransitionError(
            f"{aggregate_type} state {from_state!r} is terminal; no transition to {to_state!r} is legal"
        )
    if to_state not in allowed:
        raise IllegalTransitionError(
            f"illegal {aggregate_type} transition {from_state!r} -> {to_state!r}; "
            f"allowed targets: {sorted(allowed)}"
        )


def validate_cycle_transition(from_state: str, to_state: str) -> None:
    validate_transition("cycle", from_state, to_state)


def validate_step_transition(from_state: str, to_state: str) -> None:
    validate_transition("step", from_state, to_state)


def validate_attempt_transition(from_state: str, to_state: str) -> None:
    validate_transition("attempt", from_state, to_state)


def is_cycle_terminal(state: str) -> bool:
    return state in CYCLE_TERMINAL_STATES


def is_step_terminal(state: str) -> bool:
    return state in STEP_TERMINAL_STATES


def is_attempt_terminal(state: str) -> bool:
    return state in ATTEMPT_TERMINAL_STATES


__all__ = [
    "IllegalTransitionError",
    "TransitionCheck",
    "CYCLE_TRANSITIONS",
    "STEP_TRANSITIONS",
    "ATTEMPT_TRANSITIONS",
    "STEP_TERMINAL_STATES",
    "ATTEMPT_TERMINAL_STATES",
    "validate_transition",
    "validate_cycle_transition",
    "validate_step_transition",
    "validate_attempt_transition",
    "is_cycle_terminal",
    "is_step_terminal",
    "is_attempt_terminal",
]
