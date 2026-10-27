from __future__ import annotations

"""Canonical durable execution phase contract.

This module defines the authoritative named execution phases, their typed
record shape, transition rules, timeout defaults, and resume invariants.
It is intentionally strict so later AIX/WorkHub runtime code can consume a
single shared contract instead of inventing local phase semantics.
"""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

ExecutionPhaseName = Literal[
    "plan",
    "slice_validation",
    "dispatch",
    "mutation",
    "verification",
    "closeout",
]

ExecutionPhaseStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "timed_out",
    "cancelled",
]

ExecutionPhaseRetryability = Literal["never", "safe", "requires_review"]

PHASE_SEQUENCE: tuple[ExecutionPhaseName, ...] = (
    "plan",
    "slice_validation",
    "dispatch",
    "mutation",
    "verification",
    "closeout",
)

PHASE_TIMEOUT_SECONDS: dict[ExecutionPhaseName, int] = {
    "plan": 240,
    "slice_validation": 60,
    "dispatch": 60,
    "mutation": 180,
    "verification": 120,
    "closeout": 30,
}

TERMINAL_PHASE_STATUSES: set[ExecutionPhaseStatus] = {
    "completed",
    "failed",
    "timed_out",
    "cancelled",
}

PHASE_TIMEOUT_REASON_CODE = "PHASE_TIMEOUT"
PHASE_RESUME_FINGERPRINT_MISMATCH = "PHASE_RESUME_FINGERPRINT_MISMATCH"
PHASE_TRANSITION_INVALID = "PHASE_TRANSITION_INVALID"


class ExecutionPhaseOutputRef(BaseModel):
    kind: str
    ref: str
    label: str | None = None


class ExecutionPhaseFailure(BaseModel):
    code: str
    summary: str
    retryability: ExecutionPhaseRetryability = "never"
    details: dict[str, Any] = Field(default_factory=dict)


class ExecutionPhaseRecord(BaseModel):
    schema_version: Literal["aix.execution_phase.current"] = "aix.execution_phase.current"
    phase_name: ExecutionPhaseName
    attempt: int = 1
    status: ExecutionPhaseStatus
    timeout_seconds: int
    idempotency_key: str
    packet_fingerprint: str
    workspace_fingerprint: str
    runtime_fingerprint: str
    started_at_utc: str | None = None
    completed_at_utc: str | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    output_refs: list[ExecutionPhaseOutputRef] = Field(default_factory=list)
    reason_code: str | None = None
    reason_summary: str | None = None
    failure: ExecutionPhaseFailure | None = None


@dataclass(frozen=True, slots=True)
class PhaseTransitionDecision:
    allowed: bool
    reason_code: str | None = None
    summary: str | None = None


def normalize_execution_phase_name(value: Any) -> ExecutionPhaseName | None:
    text = str(value or "").strip().lower()
    if text in PHASE_SEQUENCE:
        return text  # type: ignore[return-value]
    return None


def normalize_execution_phase_status(value: Any) -> ExecutionPhaseStatus | None:
    text = str(value or "").strip().lower()
    if text in {
        "pending",
        "running",
        "completed",
        "failed",
        "timed_out",
        "cancelled",
    }:
        return text  # type: ignore[return-value]
    return None


def phase_timeout_seconds(phase_name: ExecutionPhaseName) -> int:
    return int(PHASE_TIMEOUT_SECONDS[phase_name])


def previous_phase_name(phase_name: ExecutionPhaseName) -> ExecutionPhaseName | None:
    index = PHASE_SEQUENCE.index(phase_name)
    if index == 0:
        return None
    return PHASE_SEQUENCE[index - 1]


def next_phase_name(phase_name: ExecutionPhaseName) -> ExecutionPhaseName | None:
    index = PHASE_SEQUENCE.index(phase_name)
    if index >= len(PHASE_SEQUENCE) - 1:
        return None
    return PHASE_SEQUENCE[index + 1]


def validate_phase_transition(
    *,
    previous_phase: ExecutionPhaseName | None,
    next_phase: ExecutionPhaseName,
    allow_same_phase_retry: bool = False,
) -> PhaseTransitionDecision:
    if previous_phase is None:
        return (
            PhaseTransitionDecision(True)
            if next_phase == "plan"
            else PhaseTransitionDecision(
                False,
                PHASE_TRANSITION_INVALID,
                f"first durable phase must be plan, got {next_phase}",
            )
        )

    if previous_phase == next_phase:
        return (
            PhaseTransitionDecision(True)
            if allow_same_phase_retry
            else PhaseTransitionDecision(
                False,
                PHASE_TRANSITION_INVALID,
                f"phase {next_phase} cannot repeat without explicit retry allowance",
            )
        )

    expected = next_phase_name(previous_phase)
    if expected == next_phase:
        return PhaseTransitionDecision(True)
    return PhaseTransitionDecision(
        False,
        PHASE_TRANSITION_INVALID,
        f"phase transition {previous_phase} -> {next_phase} is invalid; expected {expected}",
    )


def timeout_failure_for_phase(phase_name: ExecutionPhaseName, *, elapsed_seconds: float) -> ExecutionPhaseFailure:
    retryability: ExecutionPhaseRetryability = "safe"
    if phase_name in {"mutation", "verification"}:
        retryability = "requires_review"
    return ExecutionPhaseFailure(
        code=PHASE_TIMEOUT_REASON_CODE,
        summary=f"phase {phase_name} exceeded timeout after {elapsed_seconds:.1f}s",
        retryability=retryability,
        details={
            "phase_name": phase_name,
            "elapsed_seconds": float(elapsed_seconds),
            "timeout_seconds": phase_timeout_seconds(phase_name),
        },
    )


def validate_resume_fingerprints(
    phase_record: ExecutionPhaseRecord,
    *,
    packet_fingerprint: str,
    workspace_fingerprint: str,
    runtime_fingerprint: str,
) -> PhaseTransitionDecision:
    if phase_record.packet_fingerprint != packet_fingerprint:
        return PhaseTransitionDecision(
            False,
            PHASE_RESUME_FINGERPRINT_MISMATCH,
            "packet fingerprint changed; durable phase output is stale",
        )
    if phase_record.workspace_fingerprint != workspace_fingerprint:
        return PhaseTransitionDecision(
            False,
            PHASE_RESUME_FINGERPRINT_MISMATCH,
            "workspace fingerprint changed; durable phase output is stale",
        )
    if phase_record.runtime_fingerprint != runtime_fingerprint:
        return PhaseTransitionDecision(
            False,
            PHASE_RESUME_FINGERPRINT_MISMATCH,
            "runtime fingerprint changed; durable phase output is stale",
        )
    return PhaseTransitionDecision(True)


def can_resume_from_phase(
    phase_record: ExecutionPhaseRecord,
    *,
    packet_fingerprint: str,
    workspace_fingerprint: str,
    runtime_fingerprint: str,
) -> PhaseTransitionDecision:
    if phase_record.status != "completed":
        return PhaseTransitionDecision(
            False,
            PHASE_TRANSITION_INVALID,
            f"phase {phase_record.phase_name} with status {phase_record.status} is not resumable",
        )
    return validate_resume_fingerprints(
        phase_record,
        packet_fingerprint=packet_fingerprint,
        workspace_fingerprint=workspace_fingerprint,
        runtime_fingerprint=runtime_fingerprint,
    )


__all__ = [
    "ExecutionPhaseFailure",
    "ExecutionPhaseName",
    "ExecutionPhaseOutputRef",
    "ExecutionPhaseRecord",
    "ExecutionPhaseRetryability",
    "ExecutionPhaseStatus",
    "PHASE_RESUME_FINGERPRINT_MISMATCH",
    "PHASE_SEQUENCE",
    "PHASE_TIMEOUT_REASON_CODE",
    "PHASE_TIMEOUT_SECONDS",
    "PHASE_TRANSITION_INVALID",
    "PhaseTransitionDecision",
    "TERMINAL_PHASE_STATUSES",
    "can_resume_from_phase",
    "next_phase_name",
    "normalize_execution_phase_name",
    "normalize_execution_phase_status",
    "phase_timeout_seconds",
    "previous_phase_name",
    "timeout_failure_for_phase",
    "validate_phase_transition",
    "validate_resume_fingerprints",
]
