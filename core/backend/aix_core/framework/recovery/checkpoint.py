"""AIX Framework Durable Checkpoints.

Sprint: F6 Recovery Framework (F6-P2)
Grounded in: objective_cycle_events ledger, ObjectiveCycleRepository, RecoveryCheckpointV1.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

CheckpointReason = Literal[
    "POST_ADMISSION",
    "POST_PLAN_ACCEPTED",
    "PRE_ATTEMPT_BEGIN",
    "POST_ATTEMPT_COMMIT",
    "POST_VERIFICATION",
    "WAITING_APPROVAL",
    "BLOCKED",
    "TERMINAL",
]

CHECKPOINT_EVENT_TYPE = "recovery.checkpoint_recorded"
CHECKPOINT_SCHEMA_VERSION = "aix.recovery.checkpoint.v1"


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _uuid_str() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class CheckpointEventV1:
    """Immutable, replay-complete checkpoint event recorded in the ledger."""

    checkpoint_id: str
    tenant_id: str
    objective_id: str
    cycle_id: str
    event_sequence: int
    command_id: str
    reason: CheckpointReason
    recorded_at: str
    workflow: dict[str, Any]
    dispatcher: dict[str, Any]
    plan: dict[str, Any]
    attempt: dict[str, Any] | None
    budgets: dict[str, int]
    approval: dict[str, Any] | None
    evidence_refs: tuple[str, ...]
    decision_state: dict[str, Any]
    state_hash: str
    projection_ref: str | None = None
    workhub_task_id: str | None = None
    schema_version: Literal["aix.recovery.checkpoint.v1"] = CHECKPOINT_SCHEMA_VERSION

    @classmethod
    def compute_state_hash(
        cls,
        *,
        schema_version: str,
        checkpoint_id: str,
        tenant_id: str,
        objective_id: str,
        cycle_id: str,
        event_sequence: int,
        command_id: str,
        reason: str,
        recorded_at: str,
        workflow: dict[str, Any],
        dispatcher: dict[str, Any],
        plan: dict[str, Any],
        attempt: dict[str, Any] | None,
        budgets: dict[str, int],
        approval: dict[str, Any] | None,
        evidence_refs: tuple[str, ...],
        decision_state: dict[str, Any],
        projection_ref: str | None = None,
        workhub_task_id: str | None = None,
    ) -> str:
        payload = {
            "schema_version": schema_version,
            "checkpoint_id": checkpoint_id,
            "tenant_id": tenant_id,
            "objective_id": objective_id,
            "cycle_id": cycle_id,
            "event_sequence": event_sequence,
            "command_id": command_id,
            "reason": reason,
            "recorded_at": recorded_at,
            "workflow": workflow,
            "dispatcher": dispatcher,
            "plan": plan,
            "attempt": attempt,
            "budgets": budgets,
            "approval": approval,
            "evidence_refs": sorted(evidence_refs),
            "decision_state": decision_state,
            "projection_ref": projection_ref,
            "workhub_task_id": workhub_task_id,
        }
        raw_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()

    def validate_integrity(self) -> bool:
        expected = self.compute_state_hash(
            schema_version=self.schema_version,
            checkpoint_id=self.checkpoint_id,
            tenant_id=self.tenant_id,
            objective_id=self.objective_id,
            cycle_id=self.cycle_id,
            event_sequence=self.event_sequence,
            command_id=self.command_id,
            reason=self.reason,
            recorded_at=self.recorded_at,
            workflow=self.workflow,
            dispatcher=self.dispatcher,
            plan=self.plan,
            attempt=self.attempt,
            budgets=self.budgets,
            approval=self.approval,
            evidence_refs=self.evidence_refs,
            decision_state=self.decision_state,
            projection_ref=self.projection_ref,
            workhub_task_id=self.workhub_task_id,
        )
        return expected == self.state_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "checkpoint_id": self.checkpoint_id,
            "tenant_id": self.tenant_id,
            "objective_id": self.objective_id,
            "cycle_id": self.cycle_id,
            "event_sequence": self.event_sequence,
            "command_id": self.command_id,
            "reason": self.reason,
            "recorded_at": self.recorded_at,
            "workflow": self.workflow,
            "dispatcher": self.dispatcher,
            "plan": self.plan,
            "attempt": self.attempt,
            "budgets": self.budgets,
            "approval": self.approval,
            "evidence_refs": list(self.evidence_refs),
            "decision_state": self.decision_state,
            "projection_ref": self.projection_ref,
            "workhub_task_id": self.workhub_task_id,
            "state_hash": self.state_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckpointEventV1:
        return cls(
            checkpoint_id=data["checkpoint_id"],
            tenant_id=data["tenant_id"],
            objective_id=data["objective_id"],
            cycle_id=data["cycle_id"],
            event_sequence=data["event_sequence"],
            command_id=data["command_id"],
            reason=data["reason"],
            recorded_at=data["recorded_at"],
            workflow=data.get("workflow") or {},
            dispatcher=data.get("dispatcher") or {},
            plan=data.get("plan") or {},
            attempt=data.get("attempt"),
            budgets=data.get("budgets") or {},
            approval=data.get("approval"),
            evidence_refs=tuple(data.get("evidence_refs") or ()),
            decision_state=data.get("decision_state") or {},
            projection_ref=data.get("projection_ref"),
            workhub_task_id=data.get("workhub_task_id"),
            state_hash=data["state_hash"],
            schema_version=data.get("schema_version", CHECKPOINT_SCHEMA_VERSION),
        )


class DurableCheckpointStore(Protocol):
    """Abstract protocol for durable checkpoint persistence."""

    async def append(self, event: CheckpointEventV1) -> str: ...
    async def latest(self, tenant_id: str, cycle_id: str) -> CheckpointEventV1 | None: ...
    async def by_id(self, tenant_id: str, checkpoint_id: str) -> CheckpointEventV1 | None: ...


class ObjectiveCycleCheckpointStore:
    """In-memory and repository adapter for durable checkpoint storage."""

    def __init__(self, repository: Any = None) -> None:
        self._repository = repository
        self._in_memory: dict[str, list[CheckpointEventV1]] = {}  # keyed by (tenant_id, cycle_id)
        self._by_id_map: dict[str, CheckpointEventV1] = {}

    async def append(self, event: CheckpointEventV1) -> str:
        if not event.validate_integrity():
            raise ValueError(f"Checkpoint integrity validation failed: state_hash mismatch for {event.checkpoint_id}")

        key = f"{event.tenant_id}:{event.cycle_id}"
        if key not in self._in_memory:
            self._in_memory[key] = []
        self._in_memory[key].append(event)
        self._by_id_map[event.checkpoint_id] = event

        if self._repository is not None and hasattr(self._repository, "append_event"):
            await self._repository.append_event(
                tenant_id=event.tenant_id,
                aggregate_type="run",
                aggregate_id=event.cycle_id,
                event_type=CHECKPOINT_EVENT_TYPE,
                payload=event.to_dict(),
                command_id=event.command_id,
            )

        return event.checkpoint_id

    async def latest(self, tenant_id: str, cycle_id: str) -> CheckpointEventV1 | None:
        key = f"{tenant_id}:{cycle_id}"
        events = self._in_memory.get(key, [])
        if not events:
            return None
        return events[-1]

    async def by_id(self, tenant_id: str, checkpoint_id: str) -> CheckpointEventV1 | None:
        event = self._by_id_map.get(checkpoint_id)
        if event and event.tenant_id == tenant_id:
            return event
        return None
