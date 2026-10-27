"""AIX Framework Recovery Taxonomy & Action Types.

Sprint: F6 Recovery Framework (F6-P1)
Grounded in: retry_repair.py, repair_directive.py, replan.py, loop_budget.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RecoveryMechanism(str, Enum):
    RETRY = "RETRY"
    REPAIR = "REPAIR"
    REPLAN = "REPLAN"
    CHECKPOINT = "CHECKPOINT"
    RESUME = "RESUME"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True)
class Retry:
    """Action record authorizing a new attempt on the same step."""

    failure_ref: str
    step_id: str
    next_attempt: int
    budget_after: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism": RecoveryMechanism.RETRY.value,
            "failure_ref": self.failure_ref,
            "step_id": self.step_id,
            "next_attempt": self.next_attempt,
            "budget_after": self.budget_after,
        }


@dataclass(frozen=True)
class Repair:
    """Action record combining a structured repair directive with an authorized retry."""

    failure_ref: str
    directive: Any
    retry: Retry

    def to_dict(self) -> dict[str, Any]:
        directive_dict = getattr(self.directive, "model_dump", None) or getattr(self.directive, "to_dict", None)
        d_val = directive_dict() if callable(directive_dict) else str(self.directive)
        return {
            "mechanism": RecoveryMechanism.REPAIR.value,
            "failure_ref": self.failure_ref,
            "directive": d_val,
            "retry": self.retry.to_dict(),
        }


@dataclass(frozen=True)
class Replan:
    """Action record authorizing an incremental or bounded plan revision."""

    decision: Any
    budget_after: int

    def to_dict(self) -> dict[str, Any]:
        decision_dict = getattr(self.decision, "model_dump", None) or getattr(self.decision, "to_dict", None)
        d_val = decision_dict() if callable(decision_dict) else str(self.decision)
        return {
            "mechanism": RecoveryMechanism.REPLAN.value,
            "decision": d_val,
            "budget_after": self.budget_after,
        }


@dataclass(frozen=True)
class Checkpoint:
    """Action record capturing a durable recovery boundary."""

    checkpoint_id: str
    event_sequence: int
    state_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism": RecoveryMechanism.CHECKPOINT.value,
            "checkpoint_id": self.checkpoint_id,
            "event_sequence": self.event_sequence,
            "state_hash": self.state_hash,
        }


@dataclass(frozen=True)
class Resume:
    """Action record representing idempotent continuation of an objective cycle."""

    resume_key: str
    checkpoint_id: str
    disposition: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism": RecoveryMechanism.RESUME.value,
            "resume_key": self.resume_key,
            "checkpoint_id": self.checkpoint_id,
            "disposition": self.disposition,
        }


@dataclass(frozen=True)
class Escalate:
    """Action record capturing terminal escalation to an operator or external system."""

    escalation_id: str
    reason_code: str
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism": RecoveryMechanism.ESCALATE.value,
            "escalation_id": self.escalation_id,
            "reason_code": self.reason_code,
            "evidence_refs": list(self.evidence_refs),
        }
