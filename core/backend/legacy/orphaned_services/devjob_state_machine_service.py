from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.models.devjob_state_models import (
    DEVJOB_STATE_MACHINE_VERSION_Current,
    DevJobState,
    DevJobTransitionEventCurrent,
)
from backend.models.evidence_pack_models import EvidencePackCurrent
from backend.models.verifier_decision_models import validate_verifier_decision
from backend.legacy.orphaned_services.devexecutor_evidence_pack_service import (
    EvidenceBundleValidationError,
    build_partial_failure_evidence_pack,
    validate_bundle_for_ready_for_human,
)
from backend.legacy.orphaned_services.devexecutor_handoff_service import (
    build_verifier_handoff_packet_current,
)
from backend.services.devexecutor_tool_loop_service import (
    EscalationHookFn,
    PlannerFn,
    PolicyGateFn,
    execute_react_tool_loop,
    execute_tool_loop,
)

ALLOWED_TRANSITIONS: dict[DevJobState, set[DevJobState]] = {
    "queued": {"running"},
    "running": {"editing", "testing", "failed", "needs_input"},
    "editing": {"testing", "failed", "needs_input"},
    "testing": {"ready_for_human", "failed", "needs_input"},
    "ready_for_human": {"verifying"},
    "verifying": {"verified", "needs_fix"},
    "verified": set(),
    "needs_fix": {"running", "needs_input"},
    "failed": set(),
    "needs_input": set(),
}


class DevJobIllegalTransitionError(ValueError):
    pass


class DevJobStateMachine:
    def __init__(self, *, correlation_id: str, initial_state: DevJobState = "queued"):
        self.correlation_id = correlation_id
        self.state: DevJobState = initial_state
        self.events: list[DevJobTransitionEventCurrent] = []

    def transition(self, *, to_state: DevJobState, reason_code: str) -> DevJobTransitionEventCurrent:
        allowed = ALLOWED_TRANSITIONS.get(self.state, set())
        if to_state not in allowed:
            raise DevJobIllegalTransitionError(
                f"ILLEGAL_TRANSITION from={self.state} to={to_state} reason={reason_code}"
            )
        event = DevJobTransitionEventCurrent(
            schema_version=DEVJOB_STATE_MACHINE_VERSION_Current,
            sequence=len(self.events) + 1,
            from_state=self.state,
            to_state=to_state,
            reason_code=reason_code,
            correlation_id=self.correlation_id,
            occurred_at_utc=datetime.now(UTC).isoformat(),
        )
        self.events.append(event)
        self.state = to_state
        return event

    def replay(self, events: list[DevJobTransitionEventCurrent]) -> DevJobState:
        state: DevJobState = "queued"
        for event in sorted(events, key=lambda e: e.sequence):
            if event.from_state != state:
                raise DevJobIllegalTransitionError(
                    f"REPLAY_STATE_MISMATCH expected_from={state} actual_from={event.from_state}"
                )
            allowed = ALLOWED_TRANSITIONS.get(state, set())
            if event.to_state not in allowed:
                raise DevJobIllegalTransitionError(
                    f"REPLAY_ILLEGAL_TRANSITION from={event.from_state} to={event.to_state}"
                )
            state = event.to_state
        return state


def apply_verifier_decision(
    *,
    state_machine: DevJobStateMachine,
    decision_payload: dict[str, Any],
) -> dict[str, Any]:
    if state_machine.state != "ready_for_human":
        raise DevJobIllegalTransitionError(
            f"VERIFIER_DECISION_PRECONDITION_FAILED expected=ready_for_human actual={state_machine.state}"
        )
    normalized_payload = dict(decision_payload or {})
    normalized_payload.pop("actionable_items_count", None)
    decision = validate_verifier_decision(normalized_payload)
    state_machine.transition(to_state="verifying", reason_code="VERIFIER_REVIEW_STARTED")
    target_state: DevJobState = "verified" if decision.decision == "verified" else "needs_fix"
    state_machine.transition(to_state=target_state, reason_code=f"VERIFIER_DECISION_{decision.decision.upper()}")
    return {
        "final_state": state_machine.state,
        "decision": decision.model_dump(mode="python"),
        "transitions": [e.model_dump(mode="python") for e in state_machine.events],
    }


def apply_needs_fix_loopback(
    *,
    state_machine: DevJobStateMachine,
    current_cycle: int,
    max_cycles: int,
) -> dict[str, Any]:
    if state_machine.state != "needs_fix":
        raise DevJobIllegalTransitionError(
            f"LOOPBACK_PRECONDITION_FAILED expected=needs_fix actual={state_machine.state}"
        )
    if max_cycles < 1:
        raise ValueError("max_cycles must be >= 1")
    if current_cycle < 0:
        raise ValueError("current_cycle must be >= 0")

    if current_cycle < max_cycles:
        state_machine.transition(to_state="running", reason_code="NEEDS_FIX_LOOPBACK_TO_CODER")
        return {
            "final_state": state_machine.state,
            "next_cycle": current_cycle + 1,
            "escalated_to_needs_input": False,
            "transitions": [e.model_dump(mode="python") for e in state_machine.events],
        }

    state_machine.transition(to_state="needs_input", reason_code="NEEDS_FIX_MAX_CYCLE_EXCEEDED")
    return {
        "final_state": state_machine.state,
        "next_cycle": current_cycle,
        "escalated_to_needs_input": True,
        "transitions": [e.model_dump(mode="python") for e in state_machine.events],
    }


def execute_tool_loop_with_state_machine(
    *,
    workspace_root: str,
    steps: list[dict[str, Any]],
    allow_prefixes: list[list[str]],
    correlation_id: str,
    evidence_pack: EvidencePackCurrent | None = None,
    planner: PlannerFn | None = None,
    max_iterations: int = 16,
    max_runtime_seconds: float = 120.0,
    max_patch_attempts: int = 10,
    policy_gate: PolicyGateFn | None = None,
    escalation_hook: EscalationHookFn | None = None,
    approved_escalation_channels: set[str] | None = None,
    command_runtime: dict[str, Any] | None = None,
    auto_handoff_to_verifier: bool = False,
    project_id: str | None = None,
    source_task_number: int | None = None,
    verifier_persona_id: str = "verifier.current",
) -> dict[str, Any]:
    sm = DevJobStateMachine(correlation_id=correlation_id, initial_state="queued")
    sm.transition(to_state="running", reason_code="JOB_STARTED")

    seen_editing = False
    seen_testing = False
    for step in steps:
        tool = str(step.get("tool") or "")
        if not seen_editing and tool in {"write_file", "apply_patch"}:
            sm.transition(to_state="editing", reason_code="ENTER_EDITING")
            seen_editing = True
        if not seen_testing and tool == "cmd.run":
            if sm.state == "running":
                sm.transition(to_state="testing", reason_code="ENTER_TESTING_DIRECT")
            elif sm.state == "editing":
                sm.transition(to_state="testing", reason_code="ENTER_TESTING")
            seen_testing = True

    if planner is None:
        transcript = execute_tool_loop(
            workspace_root=workspace_root,
            steps=steps,
            allow_prefixes=allow_prefixes,
            max_runtime_seconds=max_runtime_seconds,
            max_patch_attempts=max_patch_attempts,
            policy_gate=policy_gate,
            escalation_hook=escalation_hook,
            approved_escalation_channels=approved_escalation_channels,
            command_runtime=command_runtime,
        )
    else:
        transcript = execute_react_tool_loop(
            workspace_root=workspace_root,
            planner=planner,
            allow_prefixes=allow_prefixes,
            max_iterations=max_iterations,
            max_runtime_seconds=max_runtime_seconds,
            max_patch_attempts=max_patch_attempts,
            planner_context={"correlation_id": correlation_id},
            policy_gate=policy_gate,
            escalation_hook=escalation_hook,
            approved_escalation_channels=approved_escalation_channels,
            command_runtime=command_runtime,
        )
    has_failure = any(not item.get("ok", False) for item in transcript)
    if has_failure:
        partial_pack = build_partial_failure_evidence_pack(
            transcript=transcript,
            reason_code="TOOL_LOOP_FAILURE",
            summary="Tool loop failed before completion.",
        )
        sm.transition(to_state="failed", reason_code="TOOL_LOOP_FAILURE")
        return {
            "final_state": sm.state,
            "completion": {
                "outcome": "failed",
                "reason_code": "TOOL_LOOP_FAILURE",
                "terminal": True,
            },
            "evidence_pack": partial_pack.model_dump(mode="python"),
            "transcript": transcript,
            "transitions": [e.model_dump(mode="python") for e in sm.events],
        }

    if sm.state == "running":
        sm.transition(to_state="testing", reason_code="NO_EDIT_PHASE_TESTING")

    if evidence_pack is None:
        partial_pack = build_partial_failure_evidence_pack(
            transcript=transcript,
            reason_code="MISSING_EVIDENCE_PACK",
            summary="Execution succeeded but evidence pack was missing.",
        )
        sm.transition(to_state="needs_input", reason_code="MISSING_EVIDENCE_PACK")
        return {
            "final_state": sm.state,
            "completion": {
                "outcome": "needs_input",
                "reason_code": "MISSING_EVIDENCE_PACK",
                "terminal": False,
            },
            "evidence_pack": partial_pack.model_dump(mode="python"),
            "transcript": transcript,
            "transitions": [e.model_dump(mode="python") for e in sm.events],
        }
    try:
        validate_bundle_for_ready_for_human(evidence_pack)
    except EvidenceBundleValidationError:
        partial_pack = build_partial_failure_evidence_pack(
            transcript=transcript,
            reason_code="EVIDENCE_GATE_FAILED",
            summary="Execution produced output but evidence gate failed validation.",
        )
        sm.transition(to_state="needs_input", reason_code="EVIDENCE_GATE_FAILED")
        final_evidence = partial_pack
        completion = {
            "outcome": "needs_input",
            "reason_code": "EVIDENCE_GATE_FAILED",
            "terminal": False,
        }
    else:
        sm.transition(to_state="ready_for_human", reason_code="EVIDENCE_GATE_PASSED")
        final_evidence = evidence_pack
        completion = {
            "outcome": "ready_for_human",
            "reason_code": "EVIDENCE_GATE_PASSED",
            "terminal": False,
        }
    verifier_handoff_message = (
        build_verifier_handoff_packet_current(
            project_id=str(project_id or ""),
            tenant_id="default",
            source_task_number=source_task_number,
            coder_correlation_id=correlation_id,
            coder_result={
                "final_state": sm.state,
                "completion": completion,
                "evidence_pack": final_evidence.model_dump(mode="python"),
                "transcript": transcript,
                "transitions": [e.model_dump(mode="python") for e in sm.events],
            },
        )
        if auto_handoff_to_verifier and sm.state == "ready_for_human" and project_id
        else None
    )

    return {
        "final_state": sm.state,
        "completion": completion,
        "evidence_pack": final_evidence.model_dump(mode="python"),
        "transcript": transcript,
        "transitions": [e.model_dump(mode="python") for e in sm.events],
        "verifier_handoff_message": verifier_handoff_message,
    }
