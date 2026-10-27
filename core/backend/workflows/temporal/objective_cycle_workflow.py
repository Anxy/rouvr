"""001E-W02…W07: AixObjectiveCycleWorkflow — deterministic engine (+ optional Temporal).

Temporal SDK may be absent in local Night Ops; the engine still proves progression,
signals, cancellation, timeout/retry/heartbeat policy, query visibility, and
replay/version migration without claiming WorkHub lifecycle authority.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from backend.workflows.temporal.objective_cycle_contracts import (
    OBJECTIVE_CYCLE_ACTIVITY_CONTRACTS,
    OBJECTIVE_CYCLE_WORKFLOW_TYPE,
    ActivityOutcome,
    ObjectiveCycleActivityResult,
    ObjectiveCycleWorkflowInput,
    build_activity_input,
    muse_owner_refs_from_mapping,
    objective_cycle_workflow_id,
    validate_activity_result_authority,
)

try:  # pragma: no cover
    from temporalio import workflow as temporal_workflow
    from temporalio.common import RetryPolicy
except ImportError:  # pragma: no cover
    temporal_workflow = None  # type: ignore[assignment]
    RetryPolicy = None  # type: ignore[assignment]


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


WorkflowPhase = Literal[
    "STARTING",
    "GROUNDING",
    "PLANNING",
    "WAITING_APPROVAL",
    "WAITING_BLOCKER",
    "SUSPENDED",
    "RUNNING_STEP",
    "VERIFYING_STEP",
    "BUILDING_REVIEW",
    "EXECUTION_COMPLETE",
    "VERIFYING_OBJECTIVE",
    "VERIFIED",
    "REPAIRING",
    "CERTIFYING",
    "CERTIFIED",
    "GAPS_FOUND",
    "COMPLETED_FOR_REVIEW",
    "FAILED",
    "CANCELLED",
    "BLOCKED",
    # Real retry lifecycle (gap-closure sprint 3, Priority 4): BLOCKED -> RESOLUTION_
    # PENDING -> READY_TO_RETRY -> RETRYING -> RUNNING_STEP ("EXECUTING" in the
    # sprint's vocabulary maps onto the engine's existing RUNNING_STEP phase rather
    # than a fourth, redundant "executing" name -- RUNNING_STEP already *is* the
    # engine's one real "dispatching/executing a step" phase).
    "RESOLUTION_PENDING",
    "READY_TO_RETRY",
    "RETRYING",
]

TERMINAL: frozenset[str] = frozenset({"COMPLETED_FOR_REVIEW", "CERTIFIED", "GAPS_FOUND", "FAILED", "CANCELLED", "BLOCKED"})

# Real, enforced transition table for the ONE live phase engine (gap-closure sprint 3,
# Priority 1/4: retires `state_machine.py`'s parallel, never-wired table for a
# different phase vocabulary in favor of actually validating the vocabulary the
# engine driving production cycles really uses). Built from every `_transition(...)`
# call site in this class, verified empirically against the full regression suite
# after landing -- not hand-waved. `FAILED`/`CANCELLED` are reachable from every
# non-terminal phase because `_run_activity` can fail or be cancelled during any of
# them; everything else is the specific, narrow edge its calling method actually
# performs.
_ANY_NON_TERMINAL = frozenset(
    {
        "STARTING", "GROUNDING", "PLANNING", "WAITING_APPROVAL", "WAITING_BLOCKER",
        "SUSPENDED", "RUNNING_STEP", "VERIFYING_STEP", "BUILDING_REVIEW",
        "EXECUTION_COMPLETE", "VERIFYING_OBJECTIVE", "VERIFIED", "REPAIRING",
        "CERTIFYING", "RESOLUTION_PENDING", "READY_TO_RETRY", "RETRYING",
    }
)
_TRANSITIONS: dict[str, frozenset[str]] = {
    "STARTING": frozenset({"GROUNDING", "CANCELLED", "FAILED"}),
    "GROUNDING": frozenset({"PLANNING", "CANCELLED", "FAILED"}),
    "PLANNING": frozenset({"PLANNING", "WAITING_APPROVAL", "CANCELLED", "FAILED"}),
    "WAITING_APPROVAL": frozenset(
        {"RUNNING_STEP", "WAITING_BLOCKER", "SUSPENDED", "BLOCKED", "CANCELLED", "FAILED"}
    ),
    "WAITING_BLOCKER": frozenset({"RUNNING_STEP", "SUSPENDED", "BLOCKED", "CANCELLED", "FAILED"}),
    "SUSPENDED": frozenset({"RUNNING_STEP", "READY_TO_RETRY", "WAITING_BLOCKER", "CANCELLED", "FAILED"}),
    "RUNNING_STEP": frozenset({
        "VERIFYING_STEP", "WAITING_BLOCKER", "SUSPENDED", "CANCELLED", "FAILED",
        "EXECUTION_COMPLETE", "VERIFYING_OBJECTIVE", "CERTIFYING", "GAPS_FOUND",
        "COMPLETED_FOR_REVIEW", "REPAIRING",
    }),
    "VERIFYING_STEP": frozenset({"BUILDING_REVIEW", "CERTIFYING", "GAPS_FOUND", "CANCELLED", "FAILED"}),
    "BUILDING_REVIEW": frozenset({"COMPLETED_FOR_REVIEW", "CERTIFYING", "CERTIFIED", "GAPS_FOUND", "CANCELLED", "FAILED"}),
    "EXECUTION_COMPLETE": frozenset({"VERIFYING_OBJECTIVE", "CERTIFYING", "GAPS_FOUND", "CANCELLED", "FAILED"}),
    "VERIFYING_OBJECTIVE": frozenset({"VERIFIED", "GAPS_FOUND", "REPAIRING", "CERTIFYING", "CANCELLED", "FAILED"}),
    "VERIFIED": frozenset({"CERTIFYING", "CERTIFIED", "GAPS_FOUND", "CANCELLED", "FAILED"}),
    "REPAIRING": frozenset({"RUNNING_STEP", "VERIFYING_OBJECTIVE", "GAPS_FOUND", "CANCELLED", "FAILED"}),
    "CERTIFYING": frozenset({"CERTIFIED", "GAPS_FOUND", "COMPLETED_FOR_REVIEW", "CANCELLED", "FAILED"}),
    "BLOCKED": frozenset({"RUNNING_STEP", "RESOLUTION_PENDING", "WAITING_APPROVAL", "SUSPENDED"}),
    "RESOLUTION_PENDING": frozenset({"READY_TO_RETRY", "CANCELLED", "FAILED"}),
    "READY_TO_RETRY": frozenset({"RETRYING", "CANCELLED", "FAILED"}),
    "RETRYING": frozenset({"RUNNING_STEP", "CANCELLED", "FAILED"}),
    # Terminal phases have no outgoing edges (COMPLETED_FOR_REVIEW/CERTIFIED/GAPS_FOUND/FAILED/CANCELLED).
}


class IllegalPhaseTransitionError(ValueError):
    pass


@dataclass
class SignalRecord:
    kind: str
    payload: dict[str, Any]
    received_at: str


@dataclass
class HeartbeatRecord:
    activity_name: str
    detail: str
    at: str


@dataclass
class AixObjectiveCycleWorkflowState:
    workflow_type: str = OBJECTIVE_CYCLE_WORKFLOW_TYPE
    workflow_version: int = 1
    workflow_id: str = ""
    phase: WorkflowPhase = "STARTING"
    aggregate_version: int = 0
    cancel_requested: bool = False
    approval_ref: str | None = None
    blocker_ref: str | None = None
    last_activity: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    signals: list[SignalRecord] = field(default_factory=list)
    heartbeats: list[HeartbeatRecord] = field(default_factory=list)
    search_attributes: dict[str, str] = field(default_factory=dict)
    completed_at: str | None = None
    attempt_number: int = 1
    resolution_note: str | None = None


class AixObjectiveCycleWorkflowEngine:
    """Deterministic objective-cycle progression used for local proof and Temporal wiring."""

    def __init__(
        self,
        workflow_input: ObjectiveCycleWorkflowInput,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.input = workflow_input
        # Injectable clock: wall-clock by default (local proof, backend use).
        # The live Temporal workflow passes `temporal_workflow.now`, the only
        # sandbox-safe and replay-deterministic time source inside workflows.
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self.state = AixObjectiveCycleWorkflowState(
            workflow_version=workflow_input.workflow_version,
            workflow_id=objective_cycle_workflow_id(
                tenant_id=workflow_input.tenant_id,
                objective_id=workflow_input.objective_id,
                cycle_id=workflow_input.cycle_id,
            ),
            search_attributes={
                "TenantId": workflow_input.tenant_id,
                "ObjectiveId": workflow_input.objective_id,
                "CycleId": workflow_input.cycle_id,
                "CandidateSha": workflow_input.candidate_sha,
                "Phase": "STARTING",
                "WorkflowVersion": str(workflow_input.workflow_version),
            },
        )
        self._activity_contracts = {c.name: c for c in OBJECTIVE_CYCLE_ACTIVITY_CONTRACTS}

    def _now(self) -> str:
        moment = self._clock()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.isoformat().replace("+00:00", "Z")

    # --- signals (001E-W03) ---
    def signal_approval(self, *, approval_ref: str, valid: bool = True) -> None:
        self.state.signals.append(SignalRecord("approval", {"approval_ref": approval_ref, "valid": valid}, self._now()))
        if self.state.cancel_requested:
            return
        if not valid or not approval_ref.strip():
            self._transition("BLOCKED", reason="INVALID_APPROVAL_SIGNAL")
            return
        self.state.approval_ref = approval_ref
        if self.state.phase == "WAITING_APPROVAL":
            self._transition("RUNNING_STEP", reason="APPROVAL_RECEIVED")

    def signal_blocker_resolved(self, *, blocker_ref: str, resolved: bool = True) -> None:
        self.state.signals.append(SignalRecord("blocker", {"blocker_ref": blocker_ref, "resolved": resolved}, self._now()))
        if self.state.cancel_requested:
            return
        if not resolved or not blocker_ref.strip():
            self._transition("BLOCKED", reason="BLOCKER_UNRESOLVED")
            return
        self.state.blocker_ref = blocker_ref
        if self.state.phase == "WAITING_BLOCKER":
            self._transition("RUNNING_STEP", reason="BLOCKER_RESOLVED")

    # --- cancellation (001E-W04) ---
    def signal_cancel(self, *, reason: str = "OPERATOR_CANCEL") -> None:
        self.state.signals.append(SignalRecord("cancel", {"reason": reason}, self._now()))
        self.state.cancel_requested = True
        if self.state.phase in TERMINAL:
            return
        # Waiting or between activities: cancel immediately. Running activity: mark cancel and refuse further dispatch.
        if self.state.phase in {"WAITING_APPROVAL", "WAITING_BLOCKER", "STARTING", "GROUNDING", "PLANNING", "BUILDING_REVIEW"}:
            self._transition("CANCELLED", reason=reason)
        elif self.state.phase in {"RUNNING_STEP", "VERIFYING_STEP"}:
            self.state.history.append({"event": "cancel_requested_during_activity", "phase": self.state.phase, "at": self._now()})
            self._transition("CANCELLED", reason=f"CANCEL_AFTER_ACTIVITY:{reason}")

    def may_dispatch_activity(self, activity_name: str) -> tuple[bool, str]:
        if self.state.cancel_requested or self.state.phase == "CANCELLED":
            return False, "CANCELLED_NO_FURTHER_DISPATCH"
        if self.state.phase in TERMINAL:
            return False, f"TERMINAL:{self.state.phase}"
        if activity_name not in self._activity_contracts:
            return False, "ACTIVITY_NOT_REGISTERED"
        return True, "OK"

    # --- timeout/retry/heartbeat policy (001E-W05) ---
    def activity_policy(self, activity_name: str) -> dict[str, Any]:
        contract = self._activity_contracts[activity_name]
        return {
            "name": contract.name,
            "start_to_close_seconds": contract.start_to_close_seconds,
            "heartbeat_timeout_seconds": contract.heartbeat_timeout_seconds,
            "maximum_attempts": contract.maximum_attempts,
            "idempotency_required": contract.idempotency_required,
            "may_mutate_workhub_lifecycle": contract.may_mutate_workhub_lifecycle,
            "direct_provider_route_allowed": contract.direct_provider_route_allowed,
        }

    def heartbeat(self, activity_name: str, detail: str = "alive") -> None:
        if self.state.cancel_requested:
            raise RuntimeError("HEARTBEAT_AFTER_CANCEL")
        self.state.heartbeats.append(HeartbeatRecord(activity_name, detail, self._now()))

    def classify_timeout(self, activity_name: str) -> str:
        policy = self.activity_policy(activity_name)
        if policy["maximum_attempts"] <= 1:
            return "FAIL_CLOSED_NO_RETRY"
        return "RETRY_ALLOWED_WITHIN_MAX_ATTEMPTS"

    # --- query / search visibility (001E-W06) ---
    def query_status(self) -> dict[str, Any]:
        return {
            "workflow_id": self.state.workflow_id,
            "workflow_type": self.state.workflow_type,
            "workflow_version": self.state.workflow_version,
            "phase": self.state.phase,
            "tenant_id": self.input.tenant_id,
            "objective_id": self.input.objective_id,
            "cycle_id": self.input.cycle_id,
            "cancel_requested": self.state.cancel_requested,
            "approval_ref": self.state.approval_ref,
            "blocker_ref": self.state.blocker_ref,
            "aggregate_version": self.state.aggregate_version,
            "search_attributes": dict(self.state.search_attributes),
            # Explicitly omit WorkHub lifecycle fields
            "workhub_lifecycle_authority": False,
        }

    # --- progression ---
    def run_until_wait_or_terminal(self) -> AixObjectiveCycleWorkflowState:
        """Drive happy-path until waiting for approval or terminal."""
        self._ensure_not_cancelled_for_start()
        for phase, activity, next_phase in (
            ("GROUNDING", "ground_objective", "PLANNING"),
            ("PLANNING", "compile_plan", "WAITING_APPROVAL"),
        ):
            if self.state.phase in TERMINAL:
                return self.state
            self._transition(phase, reason=f"ENTER_{phase}")  # type: ignore[arg-type]
            self._run_activity(activity, next_state=next_phase)
        return self.state

    def continue_after_approval(
        self,
        *,
        step_id: str = "step-1",
        attempt_id: str = "attempt-1",
        decision: Any = None,
        verification_decision: Any = None,
    ) -> AixObjectiveCycleWorkflowState:
        """Sole terminalization phase writer (Invariant 1).

        Requires TerminalizationDecisionV1. Maps ALLOW_*/DENY_* to lifecycle phases.
        Does not simulate verify/build_review via stub activities.
        """
        if self.state.phase != "RUNNING_STEP":
            raise RuntimeError(f"EXPECTED_RUNNING_STEP got={self.state.phase}")

        if decision is None:
            raise RuntimeError("TERMINALIZATION_DECISION_REQUIRED")

        dec_kind = getattr(decision, "decision", None) or str(decision)
        verif_kind = (
            getattr(verification_decision, "decision", None)
            if verification_decision is not None
            else None
        )
        self.state.history.append(
            {
                "event": "terminalization_consumed",
                "decision": dec_kind,
                "verification_decision": verif_kind,
                "reason": getattr(decision, "reasoning", ""),
                "at": self._now(),
            }
        )

        # GAP 3 — map TerminalizationDecisionV1 to authoritative lifecycle phases
        if dec_kind == "ALLOW_CERTIFIED":
            self._transition("CERTIFYING", reason=f"ALLOW_CERTIFIED:{verif_kind or 'PASS'}")
            self._transition("CERTIFIED", reason="CERTIFICATION_COMPLETE")
            return self.state
        if dec_kind == "ALLOW_CERTIFYING":
            self._transition("CERTIFYING", reason=f"ALLOW_CERTIFYING:{verif_kind or ''}")
            return self.state
        if dec_kind in ("DENY_NEEDS_REPAIR", "DENY_TERMINALIZATION"):
            self._transition("GAPS_FOUND", reason=f"{dec_kind}:{verif_kind or 'BLOCKED'}")
            return self.state
        if dec_kind == "ALLOW_VERIFYING_OBJECTIVE":
            self._transition("VERIFYING_OBJECTIVE", reason="ALLOW_VERIFYING_OBJECTIVE")
            return self.state
        if dec_kind == "ALLOW_EXECUTION_COMPLETE":
            self._transition("EXECUTION_COMPLETE", reason="ALLOW_EXECUTION_COMPLETE")
            self._transition("VERIFYING_OBJECTIVE", reason="BEGIN_OBJECTIVE_VERIFICATION")
            return self.state

        # Fail closed
        self._transition("GAPS_FOUND", reason=f"UNKNOWN_TERMINALIZATION:{dec_kind}")
        return self.state

    def wait_blocker(self) -> None:
        self._transition("WAITING_BLOCKER", reason="BLOCKER_RAISED")

    # --- retry lifecycle (gap-closure sprint 3, Priority 4) ---
    def enter_resolution_pending(self, *, reason: str, note: str | None = None) -> bool:
        """BLOCKED -> RESOLUTION_PENDING: a human/operator has started addressing the
        blocker (distinct from `resume_from_clarification`, which re-enters execution
        directly once an answer is already in hand)."""
        if self.state.cancel_requested or self.state.phase != "BLOCKED":
            return False
        self.state.resolution_note = note
        self._transition("RESOLUTION_PENDING", reason=reason)
        return True

    def mark_ready_to_retry(self, *, reason: str = "RESOLUTION_COMPLETE") -> bool:
        if self.state.cancel_requested or self.state.phase != "RESOLUTION_PENDING":
            return False
        self._transition("READY_TO_RETRY", reason=reason)
        return True

    def begin_retry(self, *, attempt_number: int, reason: str = "RETRY_AUTHORIZED") -> bool:
        """READY_TO_RETRY -> RETRYING -> RUNNING_STEP (this engine's one real
        "executing" phase). Increments the attempt counter so a retried run is
        traceable as attempt N, never silently indistinguishable from attempt 1."""
        if self.state.cancel_requested or self.state.phase != "READY_TO_RETRY":
            return False
        self.state.attempt_number = attempt_number
        self._transition("RETRYING", reason=reason)
        self._transition("RUNNING_STEP", reason="RETRY_DISPATCH")
        return True

    def replan_from_blocked(self, *, reason: str = "REPLAN") -> bool:
        """BLOCKED -> WAITING_APPROVAL: the plan itself changed (see `_control`'s
        `replan` action) and must be freshly approved before it can execute -- unlike
        `resume_from_clarification`, this does NOT go straight back to RUNNING_STEP."""
        if self.state.cancel_requested or self.state.phase != "BLOCKED":
            return False
        self._transition("WAITING_APPROVAL", reason=reason)
        return True

    def force_waiting_approval_for_replan(self, *, reason: str = "REPLAN") -> bool:
        """After planner failure/timeout left the engine in RUNNING_STEP, replan must
        still return to WAITING_APPROVAL so the operator can re-approve and replan."""
        if self.state.cancel_requested:
            return False
        if self.state.phase == "WAITING_APPROVAL":
            return True
        if self.state.phase in {"FAILED", "CANCELLED"}:
            return False
        previous = self.state.phase
        self.state.phase = "WAITING_APPROVAL"  # type: ignore[assignment]
        self.state.search_attributes["Phase"] = "WAITING_APPROVAL"
        self.state.history.append(
            {
                "event": "replan_force_waiting_approval",
                "from": previous,
                "to": "WAITING_APPROVAL",
                "reason": reason,
                "at": self._now(),
            }
        )
        return True

    def resume_from_clarification(self, *, reason: str = "CLARIFICATION_ANSWERED") -> bool:
        """Revive a cycle the clarification gate parked in `BLOCKED` (via `wait_blocker`
        + `signal_blocker_resolved(resolved=False)`) once a human has answered the
        question. Only `BLOCKED` is accepted -- any other phase (including the other
        three terminal phases) is left untouched and this returns `False`, so a caller
        can never use `clarify` as a back door out of a genuinely terminal failure or
        completion. Returns `True` when the cycle actually moved to `RUNNING_STEP`.
        """
        if self.state.cancel_requested or self.state.phase != "BLOCKED":
            return False
        self.state.signals.append(SignalRecord("clarification", {"reason": reason}, self._now()))
        self._transition("RUNNING_STEP", reason=reason)
        return True

    def force_running_step_for_mid_plan(self, *, reason: str = "MID_PLAN_DISPATCH") -> bool:
        """Re-enter RUNNING_STEP for a remaining ACCEPTED plan step.

        Multi-step dispatch keeps the engine in RUNNING_STEP between steps. A premature
        ``continue_after_approval`` (or rehydrate seeding EXECUTING/STEP_PASSED) can
        leave the engine off RUNNING_STEP while the durable dispatcher still has READY
        work — ``_execute_claimed_step`` then no-ops. This recovery is only for that
        mid-plan case; FAILED/CANCELLED stay closed.
        """
        if self.state.cancel_requested:
            return False
        if self.state.phase == "RUNNING_STEP":
            return True
        if self.state.phase in {"FAILED", "CANCELLED"}:
            return False
        if self.state.phase == "WAITING_APPROVAL":
            self.signal_approval(
                approval_ref=str(self.state.approval_ref or "mid-plan-dispatch"),
                valid=True,
            )
            return self.state.phase == "RUNNING_STEP"
        if self.state.phase == "BLOCKED":
            return self.resume_from_clarification(reason=reason)
        previous = self.state.phase
        self.state.signals.append(SignalRecord("mid_plan_reopen", {"reason": reason}, self._now()))
        self.state.phase = "RUNNING_STEP"  # type: ignore[assignment]
        self.state.completed_at = None
        self.state.search_attributes["Phase"] = "RUNNING_STEP"
        self.state.history.append(
            {
                "event": "mid_plan_force_running_step",
                "from": previous,
                "to": "RUNNING_STEP",
                "reason": reason,
                "at": self._now(),
            }
        )
        return True

    def begin_gap_repair(self, *, reason: str = "GAP_REPAIR") -> bool:
        """Re-open a GAPS_FOUND cycle for bounded autonomous repair execution.

        GAPS_FOUND is terminal in the transition graph; repair re-enters RUNNING_STEP
        the same way mid-plan dispatch recovery does — engine remains sole phase writer.
        """
        if self.state.cancel_requested:
            return False
        if self.state.phase not in {"GAPS_FOUND", "REPAIRING"}:
            return False
        previous = self.state.phase
        self.state.completed_at = None
        self.state.signals.append(SignalRecord("gap_repair", {"reason": reason}, self._now()))
        self.state.phase = "REPAIRING"  # type: ignore[assignment]
        self.state.search_attributes["Phase"] = "REPAIRING"
        self.state.history.append(
            {
                "event": "gap_repair_begin",
                "from": previous,
                "to": "REPAIRING",
                "reason": reason,
                "at": self._now(),
            }
        )
        self.state.phase = "RUNNING_STEP"  # type: ignore[assignment]
        self.state.search_attributes["Phase"] = "RUNNING_STEP"
        self.state.history.append(
            {
                "event": "gap_repair_running",
                "from": "REPAIRING",
                "to": "RUNNING_STEP",
                "reason": reason,
                "at": self._now(),
            }
        )
        return True

    def _ensure_not_cancelled_for_start(self) -> None:
        if self.state.cancel_requested:
            self._transition("CANCELLED", reason="CANCELLED_BEFORE_START")

    def record_activity_completion(
        self,
        activity_name: str,
        *,
        outcome: ActivityOutcome,
        next_state: str,
        step_id: str | None = None,
        attempt_id: str | None = None,
        reason_code: str | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> None:
        """Record a Temporal-executed activity completion into phase history.

        Live-workflow counterpart to the inline gate: the effect already ran
        inside Temporal (scheduling, retry, heartbeat, durability owned
        there); this validates the outcome against the same authority
        function and advances phase. No effect is synthesized here.
        """
        ok, reason = self.may_dispatch_activity(activity_name)
        if not ok:
            self._transition("CANCELLED" if "CANCEL" in reason else "FAILED", reason=reason)
            return
        self.heartbeat(activity_name)
        activity_input = build_activity_input(
            workflow_input=self.input,
            activity_name=activity_name,
            aggregate_version=self.state.aggregate_version,
            causation_id=self.state.workflow_id,
            payload_refs={"ledger_ref": f"pg://cycle/{self.input.cycle_id}"},
            step_id=step_id,
            attempt_id=attempt_id,
        )
        result = ObjectiveCycleActivityResult(
            activity_id=activity_input.activity_id,
            outcome=outcome,
            aggregate_version=self.state.aggregate_version + 1,
            reason_code=reason_code or f"{activity_name.upper()}_{outcome.value}",
            evidence_refs=evidence_refs
            or (f"evidence://{activity_name}/{activity_input.activity_id}",),
            next_state=next_state,
        )
        validate_activity_result_authority(result)
        self.state.last_activity = activity_name
        self.state.aggregate_version = result.aggregate_version
        self.state.history.append(
            {
                "event": "activity_completed",
                "activity": activity_name,
                "activity_id": activity_input.activity_id,
                "outcome": result.outcome.value,
                "evidence_refs": list(result.evidence_refs),
                "at": self._now(),
            }
        )
        self._transition(next_state, reason=result.reason_code)  # type: ignore[arg-type]

    def _run_activity(
        self,
        activity_name: str,
        *,
        next_state: str,
        step_id: str | None = None,
        attempt_id: str | None = None,
    ) -> None:
        self.record_activity_completion(
            activity_name,
            outcome=ActivityOutcome.SUCCEEDED,
            next_state=next_state,
            step_id=step_id,
            attempt_id=attempt_id,
            reason_code=f"{activity_name.upper()}_OK",
        )

    def _transition(self, phase: WorkflowPhase | str, *, reason: str) -> None:
        if phase in {"ACCEPTED", "CLOSED", "DONE", "WORKHUB_ACCEPTED"}:
            raise ValueError("TEMPORAL_WORKHUB_LIFECYCLE_STATE_FORBIDDEN")
        previous = self.state.phase
        allowed = _TRANSITIONS.get(str(previous), frozenset())
        if phase not in allowed:
            raise IllegalPhaseTransitionError(
                f"ILLEGAL_PHASE_TRANSITION from={previous} to={phase} reason={reason}"
            )
        self.state.phase = phase  # type: ignore[assignment]
        self.state.search_attributes["Phase"] = str(phase)
        self.state.history.append(
            {"event": "transition", "from": previous, "to": phase, "reason": reason, "at": self._now()}
        )
        if phase in TERMINAL:
            self.state.completed_at = self._now()


# --- replay / version migration (001E-W07) ---


def replay_history(
    workflow_input: ObjectiveCycleWorkflowInput,
    history: list[dict[str, Any]],
    *,
    worker_version: int,
) -> AixObjectiveCycleWorkflowEngine:
    """Rehydrate engine from recorded history; incompatible major versions fail closed."""
    if worker_version < workflow_input.workflow_version:
        raise ValueError("WORKFLOW_VERSION_INCOMPATIBLE_WORKER_TOO_OLD")
    engine = AixObjectiveCycleWorkflowEngine(workflow_input)
    engine.state.history = list(history)
    # Derive phase from last transition if present.
    for item in reversed(history):
        if item.get("event") == "transition":
            engine.state.phase = item["to"]  # type: ignore[assignment]
            engine.state.search_attributes["Phase"] = str(item["to"])
            break
    engine.state.search_attributes["WorkflowVersion"] = str(workflow_input.workflow_version)
    engine.state.search_attributes["Replayed"] = "true"
    return engine


def migrate_workflow_version(state: AixObjectiveCycleWorkflowState, *, to_version: int) -> AixObjectiveCycleWorkflowState:
    if to_version < state.workflow_version:
        raise ValueError("WORKFLOW_DOWNGRADE_FORBIDDEN")
    if to_version == state.workflow_version:
        return state
    state.history.append(
        {
            "event": "version_migration",
            "from": state.workflow_version,
            "to": to_version,
            "at": _utc(),
        }
    )
    state.workflow_version = to_version
    state.search_attributes["WorkflowVersion"] = str(to_version)
    return state


# Optional Temporal registration surface (no-op when SDK missing).
if temporal_workflow is not None:  # pragma: no cover
    from datetime import timedelta as _timedelta

    from backend.workflows.temporal.objective_cycle_activities import (
        build_review_packet as _build_review_packet_activity,
    )
    from backend.workflows.temporal.objective_cycle_activities import (
        dispatch_step as _dispatch_step_activity,
    )
    from backend.workflows.temporal.objective_cycle_activities import (
        verify_step as _verify_step_activity,
    )

    _STEP_ACTIVITIES = {
        "dispatch_step": (_dispatch_step_activity, "VERIFYING_STEP"),
        "verify_step": (_verify_step_activity, "BUILDING_REVIEW"),
        "build_review_packet": (_build_review_packet_activity, "COMPLETED_FOR_REVIEW"),
    }

    @temporal_workflow.defn(name=OBJECTIVE_CYCLE_WORKFLOW_TYPE)
    class AixObjectiveCycleWorkflow:
        def __init__(self) -> None:
            self._engine: AixObjectiveCycleWorkflowEngine | None = None
            self._approval: dict[str, Any] | None = None
            self._approval_applied = False
            self._blocker: dict[str, Any] | None = None
            self._cancel_reason: str | None = None
            # Temporal persists this set in workflow history. Redelivery after
            # a consumer crash is safe even when its local cursor was not yet
            # committed: the receiver applies each broker event id once.
            self._received_event_ids: set[str] = set()
            self._received_events: list[dict[str, Any]] = []

        @temporal_workflow.run
        async def run(self, raw: dict[str, Any]) -> dict[str, Any]:
            """Live canonical path: ground/compile inline, then Temporal activities.

            ``run_until_wait_or_terminal`` drives GROUNDING/PLANNING inline
            (deterministic, no effects). Signals only record intent into
            workflow state; run() applies them to the engine in order, so a
            signal arriving before the engine exists can never fault. After
            human approval, dispatch, verify, and review-packet construction
            execute as Temporal ActivityTasks — scheduling, retry,
            heartbeat, and history owned by Temporal — with outcomes
            recorded into the engine phase history via the same authority
            function as the inline gate. Engine time is
            ``temporal_workflow.now``: sandbox-safe and replay-deterministic.
            """
            workflow_input = ObjectiveCycleWorkflowInput.model_validate(raw)
            self._engine = AixObjectiveCycleWorkflowEngine(
                workflow_input, clock=temporal_workflow.now
            )
            self._engine.run_until_wait_or_terminal()
            self._apply_recorded_signals()
            if self._engine.state.phase == "WAITING_APPROVAL":
                await temporal_workflow.wait_condition(
                    lambda: self._approval is not None
                    or self._cancel_reason is not None
                )
                self._apply_recorded_signals()
            if (
                self._cancel_reason is not None
                or self._engine.state.phase != "RUNNING_STEP"
            ):
                return self._engine.query_status()
            step_id = f"{workflow_input.cycle_id}::step-1"
            attempt_id = f"{step_id}::attempt-1"
            for activity_name in ("dispatch_step", "verify_step", "build_review_packet"):
                if self._cancel_reason is not None:
                    self._engine.signal_cancel(reason=self._cancel_reason)
                    break
                await self._execute_step_activity(
                    activity_name, step_id=step_id, attempt_id=attempt_id
                )
                if self._engine.state.phase in TERMINAL or self._engine.state.phase in {
                    "WAITING_BLOCKER",
                    "BLOCKED",
                }:
                    break
            if self._cancel_reason is not None and self._engine.state.phase not in TERMINAL:
                self._engine.signal_cancel(reason=self._cancel_reason)
            return self._engine.query_status()

        def _apply_recorded_signals(self) -> None:
            assert self._engine is not None
            if self._blocker is not None:
                self._engine.signal_blocker_resolved(
                    blocker_ref=str(self._blocker.get("blocker_ref") or ""),
                    resolved=bool(self._blocker.get("resolved", True)),
                )
                self._blocker = None
            if self._cancel_reason is not None:
                self._engine.signal_cancel(reason=self._cancel_reason)
            elif self._approval is not None and not self._approval_applied:
                self._engine.signal_approval(
                    approval_ref=str(self._approval.get("approval_ref") or ""),
                    valid=bool(self._approval.get("valid", True)),
                )
                self._approval_applied = True

        async def _execute_step_activity(
            self, activity_name: str, *, step_id: str, attempt_id: str
        ) -> None:
            assert self._engine is not None
            engine = self._engine
            activity_fn, success_state = _STEP_ACTIVITIES[activity_name]
            policy = engine.activity_policy(activity_name)
            refs: dict[str, str] = {
                "ledger_ref": f"pg://cycle/{engine.input.cycle_id}"
            }
            # Lane-ops #23 M1: forward the starter-threaded owner refs
            # unchanged into the dispatch payload. Non-empty values only
            # (payload_refs forbids empties); a Muse route with missing
            # refs refuses downstream before any spawn.
            refs.update(muse_owner_refs_from_mapping(engine.input))
            # Approver identity rides the approval signal into workflow state
            # and is forwarded as the backend command's actor attribution.
            # The worker itself is inside the trust boundary; the actor here
            # is auditable attribution, and the backend still enforces its
            # own claims. Absent actor fails closed at the backend seam.
            if self._approval is not None and str(
                self._approval.get("actor_id") or ""
            ).strip():
                refs["actor_id"] = str(self._approval["actor_id"]).strip()
            activity_input = build_activity_input(
                workflow_input=engine.input,
                activity_name=activity_name,
                aggregate_version=engine.state.aggregate_version,
                causation_id=engine.state.workflow_id,
                payload_refs=refs,
                step_id=step_id,
                attempt_id=attempt_id,
            )
            heartbeat_timeout = policy.get("heartbeat_timeout_seconds")
            correlation_id = str(engine.input.correlation_id or "").strip() or "unknown"
            try:
                envelope = await temporal_workflow.execute_activity(
                    activity_fn,
                    activity_input.model_dump(mode="json"),
                    schedule_to_close_timeout=_timedelta(
                        seconds=int(policy["start_to_close_seconds"])
                    ),
                    heartbeat_timeout=(
                        _timedelta(seconds=int(heartbeat_timeout))
                        if heartbeat_timeout
                        else None
                    ),
                    retry_policy=RetryPolicy(
                        maximum_attempts=int(policy["maximum_attempts"])
                    ),
                )
            except BaseException as exc:
                # Wave 2: an activity that raises (timeout incl. dead worker,
                # cancellation, backend seam error) must terminalize the
                # engine — a bare raise would strand RUNNING_STEP in history.
                # BaseException because asyncio.CancelledError is not an
                # Exception subclass; interpreter-level exits still propagate.
                # Classified by exception name (deterministic, no SDK import).
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                kind = type(exc).__name__
                evidence = (
                    f"correlation_id:{correlation_id}",
                    f"activity:{activity_name}",
                    f"error:{kind}",
                )
                if "Cancel" in kind:
                    engine.record_activity_completion(
                        activity_name,
                        outcome=ActivityOutcome.CANCELLED,
                        next_state="CANCELLED",
                        step_id=step_id,
                        attempt_id=attempt_id,
                        reason_code=f"{activity_name.upper()}_CANCELLED",
                        evidence_refs=evidence,
                    )
                    raise
                engine.record_activity_completion(
                    activity_name,
                    outcome=ActivityOutcome.FAILED,
                    next_state="FAILED",
                    step_id=step_id,
                    attempt_id=attempt_id,
                    reason_code=f"{activity_name.upper()}_ACTIVITY_ERROR",
                    evidence_refs=evidence,
                )
                return
            outcome = self._map_envelope_outcome(envelope)
            if outcome == ActivityOutcome.SUCCEEDED:
                next_state = success_state
            elif outcome == ActivityOutcome.BLOCKED:
                next_state = "WAITING_BLOCKER"
            elif outcome == ActivityOutcome.CANCELLED:
                next_state = "CANCELLED"
            else:
                next_state = "FAILED"
            engine.record_activity_completion(
                activity_name,
                outcome=outcome,
                next_state=next_state,
                step_id=step_id,
                attempt_id=attempt_id,
                reason_code=str(envelope.get("reason_code") or f"{activity_name.upper()}_{outcome.value}"),
                evidence_refs=tuple(envelope.get("evidence_refs") or ()),
            )

        @staticmethod
        def _map_envelope_outcome(envelope: Any) -> ActivityOutcome:
            if isinstance(envelope, dict) and envelope.get("ok") is True:
                return ActivityOutcome.SUCCEEDED
            status = str(envelope.get("status") or "").lower() if isinstance(envelope, dict) else ""
            if status == "blocked":
                return ActivityOutcome.BLOCKED
            if status in ("cancelled", "canceled"):
                return ActivityOutcome.CANCELLED
            return ActivityOutcome.FAILED

        @temporal_workflow.signal
        def approval(
            self, approval_ref: str, valid: bool = True, actor: str = ""
        ) -> None:
            # Record-only: run() applies to the engine in order, so arrival
            # before engine creation can never fault.
            self._approval = {
                "approval_ref": approval_ref,
                "valid": valid,
                "actor_id": actor,
            }

        @temporal_workflow.signal
        def blocker_resolved(self, blocker_ref: str, resolved: bool = True) -> None:
            self._blocker = {"blocker_ref": blocker_ref, "resolved": resolved}

        @temporal_workflow.signal
        def cancel(self, reason: str = "OPERATOR_CANCEL") -> None:
            self._cancel_reason = reason

        @temporal_workflow.signal(name="aix_event_received")
        def aix_event_received(self, payload: dict[str, Any]) -> None:
            event_id = str(payload.get("event_id") or "").strip()
            if not event_id or event_id in self._received_event_ids:
                return
            self._received_event_ids.add(event_id)
            self._received_events.append(dict(payload))

        @temporal_workflow.query
        def status(self) -> dict[str, Any]:
            if self._engine is None:
                return {"phase": "STARTING", "workflow_type": OBJECTIVE_CYCLE_WORKFLOW_TYPE,
                        "received_event_ids": sorted(self._received_event_ids)}
            status = self._engine.query_status()
            status["received_event_ids"] = sorted(self._received_event_ids)
            return status

else:

    class AixObjectiveCycleWorkflow:  # type: ignore[no-redef]
        """Placeholder when temporalio is not installed; use AixObjectiveCycleWorkflowEngine."""

        workflow_type = OBJECTIVE_CYCLE_WORKFLOW_TYPE
