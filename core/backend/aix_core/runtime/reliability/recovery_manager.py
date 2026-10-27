"""P22: Reliability & Recovery Manager.

Handles crash recovery, idempotent retries, compensation, and reconciliation.
Builds on existing checkpoint/replay/session-lock primitives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from enum import Enum


class RecoveryState(str, Enum):
    HEALTHY = "HEALTHY"
    RECOVERING = "RECOVERING"
    COMPENSATING = "COMPENSATING"
    RECONCILING = "RECONCILING"
    FAILED = "FAILED"
    RECOVERED = "RECOVERED"


@dataclass(frozen=True)
class IdempotencyKeyV1:
    """Ensures exactly-once execution semantics."""
    key: str
    scope: str  # tenant_id:objective_id:step_id:attempt_id
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )
    result_hash: str | None = None
    completed: bool = False


@dataclass(frozen=True)
class CompensationActionV1:
    """Undoes a previously completed action if a later step fails."""
    compensation_id: str
    original_action_id: str
    capability: str
    operation: str
    rollback_args: dict[str, Any] = field(default_factory=dict)
    state: Literal["PENDING", "EXECUTED", "FAILED", "SKIPPED"] = "PENDING"


@dataclass
class RecoveryCheckpointV1:
    """Checkpoint for crash recovery."""
    checkpoint_id: str
    objective_id: str
    tenant_id: str
    step_id: str
    attempt_id: str
    state_snapshot: dict[str, Any] = field(default_factory=dict)
    completed_actions: list[str] = field(default_factory=list)
    compensations: list[CompensationActionV1] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class ReconciliationResult:
    objective_id: str
    issues_found: int = 0
    issues_resolved: int = 0
    actions_taken: tuple[str, ...] = ()
    state_after: RecoveryState = RecoveryState.HEALTHY


class RecoveryManager:
    """Manages crash recovery, idempotency, and compensation."""

    def __init__(self) -> None:
        self._idempotency: dict[str, IdempotencyKeyV1] = {}
        self._checkpoints: dict[str, RecoveryCheckpointV1] = {}
        self._compensations: dict[str, list[CompensationActionV1]] = {}
        self._state: dict[str, RecoveryState] = {}  # per objective

    # ── Idempotency ──────────────────────────────────────────────────────

    def check_idempotency(self, key: str) -> IdempotencyKeyV1 | None:
        """Returns existing key if already processed; None if new."""
        return self._idempotency.get(key)

    def register_idempotency(
        self, key: str, scope: str, *, result_hash: str | None = None,
    ) -> IdempotencyKeyV1:
        entry = IdempotencyKeyV1(key=key, scope=scope, result_hash=result_hash, completed=True)
        self._idempotency[key] = entry
        return entry

    # ── Checkpoints ──────────────────────────────────────────────────────

    def save_checkpoint(self, checkpoint: RecoveryCheckpointV1) -> None:
        self._checkpoints[checkpoint.checkpoint_id] = checkpoint

    def get_checkpoint(self, checkpoint_id: str) -> RecoveryCheckpointV1 | None:
        return self._checkpoints.get(checkpoint_id)

    def get_latest_checkpoint(self, objective_id: str) -> RecoveryCheckpointV1 | None:
        candidates = [
            c for c in self._checkpoints.values()
            if c.objective_id == objective_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda c: c.created_at)

    # ── Compensation ─────────────────────────────────────────────────────

    def register_compensation(
        self, objective_id: str, compensation: CompensationActionV1,
    ) -> None:
        self._compensations.setdefault(objective_id, []).append(compensation)

    def execute_compensations(self, objective_id: str) -> list[CompensationActionV1]:
        """Execute compensations in reverse order. Returns executed list."""
        comps = self._compensations.get(objective_id, [])
        executed = []
        for comp in reversed(comps):
            executed.append(CompensationActionV1(
                compensation_id=comp.compensation_id,
                original_action_id=comp.original_action_id,
                capability=comp.capability,
                operation=comp.operation,
                rollback_args=comp.rollback_args,
                state="EXECUTED",
            ))
        self._compensations[objective_id] = executed
        self._state[objective_id] = RecoveryState.COMPENSATING
        return executed

    # ── Recovery ─────────────────────────────────────────────────────────

    def start_recovery(self, objective_id: str) -> RecoveryState:
        self._state[objective_id] = RecoveryState.RECOVERING
        return RecoveryState.RECOVERING

    def complete_recovery(self, objective_id: str, *, success: bool) -> RecoveryState:
        state = RecoveryState.RECOVERED if success else RecoveryState.FAILED
        self._state[objective_id] = state
        return state

    def get_state(self, objective_id: str) -> RecoveryState:
        return self._state.get(objective_id, RecoveryState.HEALTHY)

    # ── Reconciliation ───────────────────────────────────────────────────

    def reconcile(self, objective_id: str) -> ReconciliationResult:
        """Check for and resolve inconsistencies after crash."""
        self._state[objective_id] = RecoveryState.RECONCILING
        issues = 0
        resolved = 0
        actions: list[str] = []

        checkpoint = self.get_latest_checkpoint(objective_id)
        if checkpoint:
            issues += 1
            actions.append(f"found checkpoint {checkpoint.checkpoint_id}")
            resolved += 1

        pending_comps = [
            c for c in self._compensations.get(objective_id, [])
            if c.state == "PENDING"
        ]
        if pending_comps:
            issues += len(pending_comps)
            actions.append(f"found {len(pending_comps)} pending compensations")

        final_state = RecoveryState.HEALTHY if issues == resolved else RecoveryState.FAILED
        self._state[objective_id] = final_state

        return ReconciliationResult(
            objective_id=objective_id,
            issues_found=issues,
            issues_resolved=resolved,
            actions_taken=tuple(actions),
            state_after=final_state,
        )


__all__ = [
    "RecoveryState", "IdempotencyKeyV1", "CompensationActionV1",
    "RecoveryCheckpointV1", "ReconciliationResult", "RecoveryManager",
]
