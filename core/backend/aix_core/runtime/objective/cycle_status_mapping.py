"""AIX Closed-Loop Execution Plan, Sprint W3: map `objective_cycle_workflow.py`'s live,
reviewed-authoritative `WorkflowPhase` vocabulary onto `contracts.py`'s `CycleStatus`
vocabulary, for anything that needs the contracts-level state (e.g. a future persisted
`ExecutionCycleV1`).

`_TRANSITIONS`' own comment in `objective_cycle_workflow.py` states this vocabulary choice
was reviewed and made deliberately: `state_machine.py`'s `CycleStatus`-based table was
retired "in favor of actually validating the vocabulary the engine driving production
cycles really uses." This module does not re-litigate that decision or try to make
`WorkflowPhase` conform to `CycleStatus` -- it is a one-way, conservative, fail-closed
translation, same discipline as `task_class_mapping.py` (Sprint W1): only phases with a
genuinely defensible `CycleStatus` fit are mapped; everything else returns `None`, not a
guess.

Phases deliberately left unmapped, and why (do not "fix" these by picking the closest
same-sounding name -- each was checked, not skipped for convenience):
    WAITING_BLOCKER    -- no `CycleStatus` equivalent; a mid-flight "paused for a signal"
                          concept `CycleStatus` doesn't model at this granularity.
    BUILDING_REVIEW    -- no `CycleStatus` equivalent; review-packet assembly between the
                          last verified step and terminal completion.
    BLOCKED            -- NAME COLLISION, DIFFERENT SEMANTICS. `WorkflowPhase.BLOCKED` is
                          *not* strictly terminal -- `_TRANSITIONS["BLOCKED"]` has three
                          real outgoing edges (`RUNNING_STEP`/`RESOLUTION_PENDING`/
                          `WAITING_APPROVAL`), even though it also appears in this same
                          module's own `TERMINAL` frozenset (a real, separate inconsistency
                          in `objective_cycle_workflow.py`, not fixed here -- out of this
                          sprint's scope, flagged for whoever owns that file next).
                          `CycleStatus.BLOCKED` (`state_machine.py`/`contracts.py`) *is*
                          strictly terminal -- zero outgoing edges in `CYCLE_TRANSITIONS`.
                          Mapping same-name-to-same-name here would silently claim a
                          resumable phase is terminal. Left unmapped on purpose.
    RESOLUTION_PENDING -- retry-lifecycle-specific; no `CycleStatus` equivalent.
    READY_TO_RETRY     -- semantically distinct from `CycleStatus.READY` (first dispatch,
                          not "ready to retry after a block") -- mapping them together would
                          lose the retry-attempt distinction the retry lifecycle depends on.
    RETRYING           -- semantically distinct from `CycleStatus.RUNNING_STEP` for the same
                          reason as `READY_TO_RETRY`.

Net finding worth carrying forward (not resolved here): 6 of `WorkflowPhase`'s 15 states
have no defensible `CycleStatus` fit -- more than a third. If `CycleStatus` is ever meant to
become the actual persisted source of truth for cycle state (rather than a design that lost
to `WorkflowPhase`, per the note above), it likely needs new states for at least the retry
lifecycle (`RESOLUTION_PENDING`/`READY_TO_RETRY`/`RETRYING`) and a `BLOCKED` split (terminal
vs. resumable) -- a real schema-evolution decision on the frozen, shared `contracts.py`,
out of scope for a mapping-layer module to decide unilaterally.
"""

from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import CycleStatus
from backend.workflows.temporal.objective_cycle_workflow import WorkflowPhase

_WORKFLOW_PHASE_TO_CYCLE_STATUS: dict[str, str] = {
    "STARTING": "ADMITTED",
    "GROUNDING": "GROUNDING",
    "PLANNING": "PLANNING",
    "WAITING_APPROVAL": "WAITING_APPROVAL",
    "RUNNING_STEP": "RUNNING_STEP",
    "VERIFYING_STEP": "VERIFYING_STEP",
    "COMPLETED_FOR_REVIEW": "COMPLETED_FOR_REVIEW",
    "FAILED": "FAILED",
    "CANCELLED": "CANCELLED",
}

_CYCLE_STATUS_VALUES = frozenset(CycleStatus.__args__)  # type: ignore[attr-defined]
_WORKFLOW_PHASE_VALUES = frozenset(WorkflowPhase.__args__)  # type: ignore[attr-defined]

assert set(_WORKFLOW_PHASE_TO_CYCLE_STATUS) <= _WORKFLOW_PHASE_VALUES, (
    "cycle_status_mapping.py references a WorkflowPhase value that no longer exists"
)
assert set(_WORKFLOW_PHASE_TO_CYCLE_STATUS.values()) <= _CYCLE_STATUS_VALUES, (
    "cycle_status_mapping.py must only ever map onto a real CycleStatus value"
)

UNMAPPED_WORKFLOW_PHASES: frozenset[str] = _WORKFLOW_PHASE_VALUES - set(_WORKFLOW_PHASE_TO_CYCLE_STATUS)


def workflow_phase_to_cycle_status(phase: str | None) -> str | None:
    """Conservative, fail-closed translation from a live `WorkflowPhase` to a
    `contracts.py` `CycleStatus`. Returns `None` (not a guess) for any phase without a
    genuinely defensible fit -- see the module docstring for exactly which phases and why.
    """
    if phase is None:
        return None
    return _WORKFLOW_PHASE_TO_CYCLE_STATUS.get(str(phase).strip().upper())


__all__ = [
    "UNMAPPED_WORKFLOW_PHASES",
    "workflow_phase_to_cycle_status",
]
