"""Temporal seam for the maintained AIX event consumer.

One event becomes exactly one durable Temporal action on queue
``aix.execution.current`` against ``AixObjectiveCycleWorkflow``: a start for
the first event of an objective cycle, a signal for later ones. Workflow ids
are deterministic per (tenant, objective, cycle) via the canonical contract,
so replays and restarts converge instead of duplicating work.

``InMemoryTemporalDouble`` is the isolated-test stand-in behind the same
protocol; the production starter wraps ``TemporalRuntimeManager`` (the same
manager ``run_temporal_worker.py`` uses) and is injected, never imported here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.workflows.temporal.objective_cycle_contracts import (
    OBJECTIVE_CYCLE_WORKFLOW_CONTRACT_VERSION,
    OBJECTIVE_CYCLE_WORKFLOW_TYPE,
    objective_cycle_workflow_id,
)

WORKFLOW_TYPE = OBJECTIVE_CYCLE_WORKFLOW_TYPE
EXECUTION_QUEUE = "aix.execution.current"

# All broker events enter through one receiver.  The event type remains in the
# payload; using one named signal keeps the workflow's event-id inbox as the
# single idempotency boundary.
DEFAULT_SIGNAL_NAME = "aix_event_received"


@dataclass(frozen=True)
class TemporalAction:
    event_id: str
    mode: str  # "start" | "signal"
    workflow_id: str
    workflow_type: str = WORKFLOW_TYPE
    task_queue: str = EXECUTION_QUEUE
    workflow_input: dict[str, Any] | None = None
    signal_name: str = DEFAULT_SIGNAL_NAME
    signal_payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""


@dataclass
class TemporalResult:
    workflow_id: str
    run_id: str
    task_queue: str
    mode: str
    acted: bool


class TemporalStarter(Protocol):
    def has_workflow(self, workflow_id: str) -> bool: ...
    def start_or_signal(self, action: TemporalAction) -> TemporalResult: ...


def deterministic_workflow_id(*, tenant_id: str, objective_id: str, cycle_id: str) -> str:
    return objective_cycle_workflow_id(
        tenant_id=tenant_id, objective_id=objective_id, cycle_id=cycle_id
    )


def signal_name_for(event_type: str) -> str:
    del event_type
    return DEFAULT_SIGNAL_NAME


def build_action(event: Any) -> TemporalAction:
    """Build the deterministic Temporal action template for one event."""
    workflow_input: dict[str, Any] | None = None
    if event.candidate_sha and event.plan_sha256:
        workflow_input = {
            "contract_version": OBJECTIVE_CYCLE_WORKFLOW_CONTRACT_VERSION,
            "workflow_version": 1,
            "tenant_id": event.tenant_id,
            "objective_id": event.objective_id,
            "cycle_id": event.cycle_id,
            "candidate_sha": event.candidate_sha,
            "plan_sha256": event.plan_sha256,
            "start_request_id": event.event_id,
            "correlation_id": event.correlation_id or event.event_id,
        }
    return TemporalAction(
        event_id=event.event_id,
        mode="start",
        workflow_id=deterministic_workflow_id(
            tenant_id=event.tenant_id,
            objective_id=event.objective_id,
            cycle_id=event.cycle_id,
        ),
        workflow_input=workflow_input,
        signal_name=signal_name_for(event.event_type),
        signal_payload={
            "event_id": event.event_id,
            "event_type": event.event_type,
            "correlation_id": event.correlation_id or event.event_id,
        },
        idempotency_key=event.event_id,
    )


class InMemoryTemporalDouble:
    """Deterministic isolated-test double behind the starter protocol."""

    def __init__(self) -> None:
        self.starts: dict[str, dict[str, Any]] = {}
        self.signals: list[dict[str, Any]] = []
        self._runs = 0
        self._received_event_ids: set[str] = set()

    def has_workflow(self, workflow_id: str) -> bool:
        return workflow_id in self.starts

    def start_or_signal(self, action: TemporalAction) -> TemporalResult:
        if action.event_id in self._received_event_ids:
            record = self.starts.get(action.workflow_id, {})
            return TemporalResult(
                workflow_id=action.workflow_id,
                run_id=str(record.get("run_id", "")),
                task_queue=action.task_queue,
                mode=action.mode,
                acted=False,
            )
        if action.mode == "start":
            existing = self.starts.get(action.workflow_id)
            if existing is not None:
                return TemporalResult(
                    workflow_id=action.workflow_id,
                    run_id=existing["run_id"],
                    task_queue=action.task_queue,
                    mode="start",
                    acted=False,
                )
            self._runs += 1
            run_id = f"run-{self._runs:04d}"
            self.starts[action.workflow_id] = {
                "run_id": run_id,
                "workflow_type": action.workflow_type,
                "task_queue": action.task_queue,
                "workflow_input": action.workflow_input,
                "idempotency_key": action.idempotency_key,
            }
            self._received_event_ids.add(action.event_id)
            return TemporalResult(
                workflow_id=action.workflow_id,
                run_id=run_id,
                task_queue=action.task_queue,
                mode="start",
                acted=True,
            )
        if action.workflow_id not in self.starts:
            if action.workflow_input is None:
                raise RuntimeError(
                    "TEMPORAL_UNKNOWN_WORKFLOW: signal for unknown workflow "
                    "with no start input fails closed"
                )
            self.start_or_signal(
                TemporalAction(
                    event_id=action.event_id,
                    mode="start",
                    workflow_id=action.workflow_id,
                    workflow_input=action.workflow_input,
                    signal_name=action.signal_name,
                    signal_payload=action.signal_payload,
                    idempotency_key=action.idempotency_key,
                )
            )
        record = self.starts[action.workflow_id]
        self.signals.append(
            {
                "workflow_id": action.workflow_id,
                "signal_name": action.signal_name,
                "signal_payload": action.signal_payload,
                "event_id": action.event_id,
            }
        )
        self._received_event_ids.add(action.event_id)
        return TemporalResult(
            workflow_id=action.workflow_id,
            run_id=record["run_id"],
            task_queue=action.task_queue,
            mode="signal",
            acted=True,
        )
