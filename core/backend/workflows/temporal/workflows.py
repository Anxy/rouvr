from __future__ import annotations

# ruff: noqa: E402, I001

from dataclasses import asdict, dataclass
from typing import Any

from backend.shared.monorepo_imports import ensure_hyeve_core_import_path

ensure_hyeve_core_import_path()

from hyeve_core.task_lifecycle_registry import (
    LEGACY_REVIEW_PENDING_STATUS,
    REVIEW_PENDING_TASK_STATUSES,
    TERMINAL_TASK_STATUS,
)

TEMPORAL_EXECUTION_STATES: tuple[str, ...] = (
    "queued",
    "planning",
    "ready_for_review",
    "running",
    "verifying",
    "escalated",
    "failed",
)


def temporal_registry_linked_states() -> tuple[str, ...]:
    linked = {"waiting_on_human", TERMINAL_TASK_STATUS, *REVIEW_PENDING_TASK_STATUSES}
    ordered = [
        state for state in ("waiting_on_human", LEGACY_REVIEW_PENDING_STATUS, "needs_review", "done") if state in linked
    ]
    extras = sorted(linked.difference(ordered))
    return tuple(ordered + extras)


def _build_allowed_workflow_transitions() -> dict[str, set[str]]:
    review_pending_states = set(REVIEW_PENDING_TASK_STATUSES)
    done_state = TERMINAL_TASK_STATUS
    return {
        "queued": {"planning", "running"},
        "planning": {"ready_for_review", "failed", "waiting_on_human"},
        "ready_for_review": {"running", "failed"},
        "running": {"verifying", "failed", "waiting_on_human"},
        "verifying": {"failed", "escalated", "waiting_on_human", done_state, *review_pending_states},
        "waiting_on_human": {"running", "failed", *review_pending_states},
        "escalated": {"running", "failed"},
        **{state: {"running", done_state, "failed"} for state in review_pending_states},
        done_state: set(),
        "failed": set(),
    }


ALLOWED_WORKFLOW_TRANSITIONS: dict[str, set[str]] = _build_allowed_workflow_transitions()


class TemporalWorkflowTransitionError(ValueError):
    pass


@dataclass(frozen=True)
class WorkflowTransitionEvent:
    sequence: int
    from_state: str
    to_state: str
    reason_code: str


class TemporalWorkflowStateMachine:
    def __init__(self, initial_state: str = "queued") -> None:
        self.state = initial_state
        self.events: list[WorkflowTransitionEvent] = []

    def transition(self, *, to_state: str, reason_code: str) -> WorkflowTransitionEvent:
        allowed = ALLOWED_WORKFLOW_TRANSITIONS.get(self.state, set())
        if to_state not in allowed:
            raise TemporalWorkflowTransitionError(
                f"TEMPORAL_WORKFLOW_ILLEGAL_TRANSITION from={self.state} to={to_state} reason={reason_code}"
            )
        event = WorkflowTransitionEvent(
            sequence=len(self.events) + 1,
            from_state=self.state,
            to_state=to_state,
            reason_code=reason_code,
        )
        self.events.append(event)
        self.state = to_state
        return event

    def replay(self, events: list[WorkflowTransitionEvent]) -> str:
        replay_state = "queued"
        for event in sorted(events, key=lambda item: item.sequence):
            allowed = ALLOWED_WORKFLOW_TRANSITIONS.get(replay_state, set())
            if event.from_state != replay_state:
                raise TemporalWorkflowTransitionError(
                    f"TEMPORAL_WORKFLOW_REPLAY_MISMATCH expected={replay_state} got={event.from_state}"
                )
            if event.to_state not in allowed:
                raise TemporalWorkflowTransitionError(
                    f"TEMPORAL_WORKFLOW_REPLAY_ILLEGAL from={event.from_state} to={event.to_state}"
                )
            replay_state = event.to_state
        return replay_state

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "events": [asdict(item) for item in self.events],
        }
