"""001G-W01: Executor selection policy for objective-cycle steps.

Policy chooses the executor. The executor cannot choose itself or its fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SelectionDisposition = Literal["SELECTED", "DENIED"]

ALLOWED_EXECUTORS = frozenset(
    {
        "orchestrator_v2",
        "planner_coder_aider",
        "devexecutor",
    }
)


@dataclass(frozen=True)
class ExecutorSelectionRequestV1:
    step_id: str
    attempt_id: str
    role: str
    task_class: str
    preferred_executor_id: str | None = None
    executor_self_nomination: str | None = None
    requested_fallback_id: str | None = None


@dataclass(frozen=True)
class ExecutorSelectionDecisionV1:
    disposition: SelectionDisposition
    executor_id: str | None
    reason_codes: tuple[str, ...]
    move_token_required: bool = True


class CycleExecutorSelectionPolicy:
    """Map role/task_class to a bounded executor; reject self-selection/fallback."""

    ROLE_DEFAULTS = {
        "planner": "orchestrator_v2",
        "executor": "devexecutor",
        "verifier": "orchestrator_v2",
        "recovery": "planner_coder_aider",
        "reviewer": "orchestrator_v2",
    }

    def select(self, request: ExecutorSelectionRequestV1) -> ExecutorSelectionDecisionV1:
        if request.executor_self_nomination:
            return ExecutorSelectionDecisionV1(
                disposition="DENIED",
                executor_id=None,
                reason_codes=("EXECUTOR_SELF_SELECTION_FORBIDDEN",),
            )
        if request.requested_fallback_id:
            return ExecutorSelectionDecisionV1(
                disposition="DENIED",
                executor_id=None,
                reason_codes=("EXECUTOR_FALLBACK_SELF_CHOICE_FORBIDDEN",),
            )

        chosen = request.preferred_executor_id or self.ROLE_DEFAULTS.get(request.role)
        if request.task_class == "verification_only":
            chosen = "orchestrator_v2"
        if request.task_class == "single_file_helper" and request.role == "executor":
            chosen = request.preferred_executor_id or "planner_coder_aider"

        if not chosen or chosen not in ALLOWED_EXECUTORS:
            return ExecutorSelectionDecisionV1(
                disposition="DENIED",
                executor_id=None,
                reason_codes=("EXECUTOR_NOT_ALLOWLISTED",),
            )
        return ExecutorSelectionDecisionV1(
            disposition="SELECTED",
            executor_id=chosen,
            reason_codes=("POLICY_SELECTED",),
            move_token_required=True,
        )
