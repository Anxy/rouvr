"""AIX-UGEF-001: capability execution audit events.

Correlated events emitted during the governed action lifecycle. Uses the same
event-list pattern as the existing objective-cycle event recorder. Integrates
with the immutable EvidenceGraphChain (P9) to produce cryptographic evidence
for governed action sequences.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.aix_core.runtime.verification.evidence_graph import EvidenceGraphChain, EvidenceNode

if TYPE_CHECKING:
    from backend.aix_core.runtime.foundation.execution_context import ExecutionContextV1
    from backend.aix_core.runtime.foundation.policy_gateway import PolicyDecision
    from backend.aix_core.runtime.objective.contracts import ActionObservationV1, ActionRequestV1


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _principal_dict(ctx: "ExecutionContextV1 | None") -> dict[str, str]:
    if ctx is None:
        return {"type": "system", "id": "unknown", "role": ""}
    return {
        "type": ctx.principal.type,
        "id": ctx.principal.id,
        "role": ctx.principal.role,
    }


def _context_dict(ctx: "ExecutionContextV1 | None") -> dict[str, Any]:
    if ctx is None:
        return {}
    return {
        "tenant_id": ctx.tenant_id,
        "project_id": ctx.project_id,
        "repository_id": ctx.repository_id,
        "cycle_id": ctx.cycle_id,
        "step_id": ctx.step_id,
        "attempt_id": ctx.attempt_id,
    }


_AUDIT_LOG: list[dict[str, Any]] = []
_EVIDENCE_CHAINS: dict[str, EvidenceGraphChain] = {}


def emit_audit_event(event: dict[str, Any]) -> None:
    _AUDIT_LOG.append(event)
    _append_evidence_node(event)


def get_audit_log() -> list[dict[str, Any]]:
    return list(_AUDIT_LOG)


def clear_audit_log() -> None:
    _AUDIT_LOG.clear()
    _EVIDENCE_CHAINS.clear()


def get_evidence_chain(cycle_id: str) -> EvidenceGraphChain:
    """Return the evidence chain for a cycle, creating one if absent."""
    if cycle_id not in _EVIDENCE_CHAINS:
        _EVIDENCE_CHAINS[cycle_id] = EvidenceGraphChain()
    return _EVIDENCE_CHAINS[cycle_id]


def _append_evidence_node(event: dict[str, Any]) -> None:
    """Build an EvidenceNode from a governed-action audit event and append it
    to the cycle's evidence chain."""
    cycle_id = event.get("cycle_id")
    if not cycle_id:
        return
    action_id = event.get("action_id", "")
    event_type = event.get("event_type", "")
    digest = hashlib.sha256(
        f"{action_id}:{event_type}:{event.get('recorded_at', '')}".encode()
    ).hexdigest()[:16]
    node = EvidenceNode(
        criterion_id=f"cap:{event.get('capability', 'unknown')}.{event.get('operation', 'unknown')}",
        assertion_id=event_type,
        verification_id=action_id,
        artifact_digest=digest,
        producer_id=event.get("principal", {}).get("id", "system"),
        snapshot_id=f"{cycle_id}:{event.get('step_id', 'unknown')}",
        timestamp=event.get("recorded_at", _utc()),
    )
    get_evidence_chain(cycle_id).append(node)


def audit_capability_requested(
    request: "ActionRequestV1",
    ctx: "ExecutionContextV1 | None" = None,
) -> None:
    emit_audit_event({
        "event_type": "capability.requested",
        "action_id": request.action_id,
        "capability": request.capability,
        "operation": request.operation,
        "arguments": dict(request.arguments or {}),
        "principal": _principal_dict(ctx),
        **_context_dict(ctx),
        "recorded_at": _utc(),
    })


def audit_authorization_decision(
    request: "ActionRequestV1",
    decision: "PolicyDecision",
    ctx: "ExecutionContextV1 | None" = None,
) -> None:
    event_type = "capability.authorized" if decision.allowed else (
        "capability.confirmation_required" if decision.code == "APPROVAL_REQUIRED"
        else "capability.denied"
    )
    emit_audit_event({
        "event_type": event_type,
        "action_id": request.action_id,
        "capability": decision.capability,
        "operation": decision.operation,
        "decision": "ALLOW" if decision.allowed else decision.code,
        "detail": decision.detail,
        "principal": _principal_dict(ctx),
        **_context_dict(ctx),
        "recorded_at": _utc(),
    })


def audit_execution_started(
    request: "ActionRequestV1",
    ctx: "ExecutionContextV1 | None" = None,
) -> None:
    emit_audit_event({
        "event_type": "capability.execution_started",
        "action_id": request.action_id,
        "capability": request.capability,
        "operation": request.operation,
        "principal": _principal_dict(ctx),
        **_context_dict(ctx),
        "recorded_at": _utc(),
    })


def audit_execution_completed(
    request: "ActionRequestV1",
    observation: "ActionObservationV1",
    ctx: "ExecutionContextV1 | None" = None,
) -> None:
    event_type = (
        "capability.execution_completed" if observation.status == "SUCCESS"
        else "capability.execution_failed"
    )
    emit_audit_event({
        "event_type": event_type,
        "action_id": request.action_id,
        "capability": request.capability,
        "operation": request.operation,
        "status": observation.status,
        "changed": observation.changed,
        "error_code": observation.error_code,
        "principal": _principal_dict(ctx),
        **_context_dict(ctx),
        "recorded_at": _utc(),
    })


__all__ = [
    "audit_capability_requested",
    "audit_authorization_decision",
    "audit_execution_started",
    "audit_execution_completed",
    "emit_audit_event",
    "get_audit_log",
    "clear_audit_log",
    "get_evidence_chain",
]
