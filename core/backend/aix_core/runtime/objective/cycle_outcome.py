"""Autonomous Closed-Loop Lane, Packet AL-11: construct `CycleOutcomeV1` (the plan's
Phase 8/11 terminal evidence contract) once a cycle actually reaches a genuinely terminal
state. This module builds the contract from already-recorded evidence
(`StepAttemptV1`/`VerificationVerdict` caches) -- it never invents new evidence, only
reshapes what W8/W9 already recorded during the cycle's run.

"Genuinely terminal" is narrower than `WorkflowPhase`'s own `TERMINAL` frozenset (see
`cycle_status_mapping.py`'s docstring: `WorkflowPhase.BLOCKED` is *not* strictly terminal --
it has three real outgoing edges). This module's `is_genuinely_terminal_phase()` is the
one place that reconciles the two: `BLOCKED` only counts as terminal here when the cycle's
own `terminal_reason` is one `durable_api_backend.py`'s `_TERMINAL_ONLY_REASONS` already
classifies as never-retry-eligible -- reusing that existing classification rather than
building a second, parallel notion of "really blocked."
"""

from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import CycleOutcomeV1, StepAttemptV1, UsageV1

# Mirrors `_TERMINAL_ONLY_REASONS` in durable_api_backend.py (the never-retry-eligible set)
# by name rather than import, to avoid a circular import (durable_api_backend.py is this
# module's only caller). `test_al11_cycle_outcome.py` and `test_cl2_verifier_hard_gate.py`
# both assert the two sets stay in lockstep so drift here is caught immediately, the same
# discipline `contracts.py`'s `FORBIDDEN_EFFECTS` uses against `policy._FORBIDDEN_EFFECTS`.
TERMINAL_ONLY_REASONS: frozenset[str] = frozenset(
    {
        "CAPABILITY_NOT_REGISTERED", "WORKSPACE_NOT_BOUND", "NO_EXECUTABLE_BRANCH_MATCHED",
        "INTENT_UNCLEAR", "EXPLORATION_REQUIRED", "NO_APPLICABLE_SOURCE_FILE",
        "SYMBOL_NOT_FOUND", "SYMBOL_AMBIGUOUS", "FILE_NOT_IN_APPROVED_SCOPE",
        "PLAN_CHANGED_SINCE_APPROVAL", "EXECUTION_LOOP_DETECTED", "UNCAUGHT_CONTROL_ERROR",
        "VERIFICATION_HALT",
        "AUTHORIZATION_REQUIRED",
    }
)

# 001P-W13 / AIX-CH-001: WorkHub close-path failures after verified execute are
# never-retry. Kept separate from TERMINAL_ONLY_REASONS so the lockstep assert
# against durable_api_backend._TERMINAL_ONLY_REASONS stays a pure copy check.
MARK_DONE_FAIL_CLOSE_REASONS: frozenset[str] = frozenset(
    {
        "DONE_TRANSITION_FAILED",
        "DONE_STATUS_MISMATCH",
        "WORKHUB_MARK_DONE_UNEXPECTED",
        "WORKHUB_TASK_LOAD_FAILED",
        "WORKHUB_TASK_NOT_FOUND",
        "TRANSITION_FAILED",
        "EVIDENCE_ATTACH_FAILED",
        "VERIFICATION_FAILED",
        "NOT_ELIGIBLE_FOR_DONE",
        "UNSUPPORTED_STATUS",
        "WORKHUB_TASK_CONTEXT_MISSING",
    }
)

_DIRECTLY_TERMINAL_PHASES = frozenset({"COMPLETED_FOR_REVIEW", "FAILED", "CANCELLED"})

# `CycleOutcomeV1.terminal_status` (`contracts.CycleStatus`) has no `WorkflowPhase.BLOCKED`
# counterpart -- `contracts.CycleStatus.BLOCKED` is the strictly-terminal one, so a
# genuinely-terminal `WorkflowPhase.BLOCKED` maps onto it directly.
_PHASE_TO_TERMINAL_STATUS: dict[str, str] = {
    "COMPLETED_FOR_REVIEW": "COMPLETED_FOR_REVIEW",
    "FAILED": "FAILED",
    "CANCELLED": "CANCELLED",
    "BLOCKED": "BLOCKED",
}


def is_genuinely_terminal_phase(*, phase: str | None, terminal_reason: str | None) -> bool:
    p = str(phase or "").strip().upper()
    if p in _DIRECTLY_TERMINAL_PHASES:
        return True
    if p == "BLOCKED":
        reason = str(terminal_reason or "").strip().upper()
        return reason in TERMINAL_ONLY_REASONS or reason in MARK_DONE_FAIL_CLOSE_REASONS
    return False


def build_cycle_outcome(
    *,
    cycle_id: str,
    tenant_id: str,
    phase: str,
    terminal_reason: str | None,
    step_attempts: tuple[StepAttemptV1, ...] = (),
    evidence_bundle_ref: str | None = None,
) -> CycleOutcomeV1:
    """Build a `CycleOutcomeV1` from a cycle's already-recorded `StepAttemptV1` history.
    Caller must have already confirmed `is_genuinely_terminal_phase()` -- this function
    does not re-check it, matching `replan.py::BoundedReplanPolicy.propose()`'s own
    "authorize first, build second" split.
    """
    terminal_status = _PHASE_TO_TERMINAL_STATUS.get(str(phase).strip().upper())
    if terminal_status is None:
        raise ValueError(
            f"build_cycle_outcome() called with a non-terminal phase {phase!r} -- "
            "call is_genuinely_terminal_phase() first"
        )

    completed_step_ids: list[str] = []
    failed_step_ids: list[str] = []
    blocked_step_ids: list[str] = []
    total = UsageV1()
    for attempt in step_attempts:
        if attempt.outcome == "SUCCEEDED":
            completed_step_ids.append(attempt.step_id)
        elif attempt.outcome in ("FAILED", "TIMED_OUT"):
            failed_step_ids.append(attempt.step_id)
        elif attempt.outcome == "CANCELLED":
            blocked_step_ids.append(attempt.step_id)
        total = UsageV1(
            tokens_in=total.tokens_in + attempt.usage.tokens_in,
            tokens_out=total.tokens_out + attempt.usage.tokens_out,
            cost_usd=total.cost_usd + attempt.usage.cost_usd,
            tool_calls=total.tool_calls + attempt.usage.tool_calls,
            time_seconds=total.time_seconds + attempt.usage.time_seconds,
        )

    # Blocked-terminal with no attempt ever recorded (e.g. blocked before the first
    # dispatch -- WORKSPACE_NOT_BOUND, CAPABILITY_NOT_REGISTERED) has no step_id to
    # attribute the block to; record the reason itself so the outcome is never silently
    # empty about why nothing ran.
    if terminal_status == "BLOCKED" and not (completed_step_ids or failed_step_ids or blocked_step_ids):
        blocked_step_ids.append(f"cycle:{terminal_reason or 'UNKNOWN_REASON'}")

    unresolved_risks: tuple[str, ...] = () if terminal_status == "COMPLETED_FOR_REVIEW" else (
        (terminal_reason,) if terminal_reason else ()
    )

    review_recommendation = {
        "COMPLETED_FOR_REVIEW": "Ready for human review.",
        "FAILED": f"Execution failed ({terminal_reason or 'unspecified reason'}); human review required before retry.",
        "CANCELLED": "Cancelled before completion; no outstanding recommendation.",
        "BLOCKED": f"Blocked, not retry-eligible ({terminal_reason or 'unspecified reason'}); needs human intervention.",
    }[terminal_status]

    return CycleOutcomeV1(
        cycle_id=cycle_id,
        tenant_id=tenant_id,
        terminal_status=terminal_status,
        completed_step_ids=tuple(completed_step_ids),
        failed_step_ids=tuple(failed_step_ids),
        blocked_step_ids=tuple(blocked_step_ids),
        evidence_bundle_ref=evidence_bundle_ref,
        unresolved_risks=unresolved_risks,
        total_usage=total,
        review_recommendation=review_recommendation,
    )


__all__ = [
    "TERMINAL_ONLY_REASONS",
    "MARK_DONE_FAIL_CLOSE_REASONS",
    "is_genuinely_terminal_phase",
    "build_cycle_outcome",
]
