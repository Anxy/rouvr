"""C006: canonical command/event contract (versioned, fail-closed).

Every durable state transition must eventually derive from a persisted
command/event pair carrying tenant, actor, expected version, idempotency,
correlation, policy and provenance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from backend.aix_core.runtime.objective.ulid_compat import new_ulid
COMMAND_EVENT_CONTRACT_VERSION = "aix.command_event.v1"
SUPPORTED_CONTRACT_VERSIONS = frozenset({COMMAND_EVENT_CONTRACT_VERSION})

# Seed vocabulary from live objective_cycle router operations + observed events.
KNOWN_COMMANDS = frozenset(
    {
        "admit_objective",
        "start_cycle",
        "clarify",
        "cancel",
        "reject",
        "block",
        "resume",
        "retry_step",
        "replan",
        "set_approval_reference",
        "discuss",
    }
)

KNOWN_EVENT_TYPES = frozenset(
    {
        "objective.admitted",
        "objective.created",
        "objective.obligations_created",
        "objective.envelope_created",
        "objective.envelope_construction_failed",
        "objective.blocked",
        "objective.understanding_evidence_collected",
        "request.received",
        "intent.classified",
        "cycle.started",
        "cycle.stuck_detected",
        "cycle.cancel_requested",
        "cycle.retry_denied",
        "cycle.retrying",
        "cycle.replan",
        "cycle.clarify",
        "cycle.uncaught_error",
        "cycle.outcome_recorded",
        "plan.rejected",
        "plan.coverage_incomplete",
        "execution.blocked",
        "execution_context.updated",
        "step_contract_proposed",
        "step_authorized",
        "executor_package_issued",
        "attempt.succeeded",
        "attempt.failed",
        "completion.decision_recorded",
    }
)


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class CommandEventContractError(ValueError):
    pass


@dataclass(frozen=True)
class CommandEnvelopeV1:
    contract_version: str
    command_name: str
    tenant_id: str
    actor_id: str
    objective_id: str | None
    run_id: str | None
    expected_version: int
    idempotency_key: str
    correlation_id: str
    causation_id: str | None = None
    policy_context: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    issued_at: str = field(default_factory=_utc)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EventEnvelopeV1:
    contract_version: str
    event_type: str
    tenant_id: str
    actor_id: str
    objective_id: str | None
    run_id: str | None
    sequence_number: int
    command_id: str
    correlation_id: str
    causation_id: str | None = None
    policy_context: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    recorded_at: str = field(default_factory=_utc)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_contract_version(version: str) -> None:
    if version not in SUPPORTED_CONTRACT_VERSIONS:
        raise CommandEventContractError(f"UNKNOWN_CONTRACT_VERSION:{version}")


def build_command(
    *,
    command_name: str,
    tenant_id: str,
    actor_id: str,
    expected_version: int,
    idempotency_key: str,
    correlation_id: str | None = None,
    objective_id: str | None = None,
    run_id: str | None = None,
    causation_id: str | None = None,
    policy_context: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    allow_unknown_command: bool = False,
) -> CommandEnvelopeV1:
    validate_contract_version(COMMAND_EVENT_CONTRACT_VERSION)
    if not tenant_id.strip() or not actor_id.strip():
        raise CommandEventContractError("TENANT_AND_ACTOR_REQUIRED")
    if expected_version < 0:
        raise CommandEventContractError("EXPECTED_VERSION_INVALID")
    if not idempotency_key.strip():
        raise CommandEventContractError("IDEMPOTENCY_KEY_REQUIRED")
    if command_name not in KNOWN_COMMANDS and not allow_unknown_command:
        raise CommandEventContractError(f"UNKNOWN_COMMAND:{command_name}")
    return CommandEnvelopeV1(
        contract_version=COMMAND_EVENT_CONTRACT_VERSION,
        command_name=command_name,
        tenant_id=tenant_id,
        actor_id=actor_id,
        objective_id=objective_id,
        run_id=run_id,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id or f"corr_{new_ulid()[:12]}",
        causation_id=causation_id,
        policy_context=dict(policy_context or {}),
        provenance=dict(provenance or {"adr_set": ["ADR-001", "ADR-004", "ADR-006"]}),
        payload=dict(payload or {}),
    )


def build_event(
    *,
    event_type: str,
    tenant_id: str,
    actor_id: str,
    sequence_number: int,
    command_id: str,
    correlation_id: str,
    objective_id: str | None = None,
    run_id: str | None = None,
    causation_id: str | None = None,
    policy_context: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    allow_unknown_event: bool = False,
) -> EventEnvelopeV1:
    validate_contract_version(COMMAND_EVENT_CONTRACT_VERSION)
    if sequence_number < 1:
        raise CommandEventContractError("SEQUENCE_NUMBER_INVALID")
    if not command_id.strip():
        raise CommandEventContractError("COMMAND_ID_REQUIRED")
    if event_type not in KNOWN_EVENT_TYPES and not allow_unknown_event:
        raise CommandEventContractError(f"UNKNOWN_EVENT_TYPE:{event_type}")
    return EventEnvelopeV1(
        contract_version=COMMAND_EVENT_CONTRACT_VERSION,
        event_type=event_type,
        tenant_id=tenant_id,
        actor_id=actor_id,
        objective_id=objective_id,
        run_id=run_id,
        sequence_number=sequence_number,
        command_id=command_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        policy_context=dict(policy_context or {}),
        provenance=dict(provenance or {}),
        payload=dict(payload or {}),
    )


def round_trip_command(command: CommandEnvelopeV1) -> CommandEnvelopeV1:
    data = command.to_dict()
    validate_contract_version(str(data["contract_version"]))
    return CommandEnvelopeV1(**data)


def round_trip_event(event: EventEnvelopeV1) -> EventEnvelopeV1:
    data = event.to_dict()
    validate_contract_version(str(data["contract_version"]))
    return EventEnvelopeV1(**data)
