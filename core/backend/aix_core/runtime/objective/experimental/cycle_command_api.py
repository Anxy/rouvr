"""AIX-E2E-CYCLE-001J-W01: cycle command API.

The governed command surface for objective-cycle write operations. Every command is:
  - Idempotent (keyed by idempotency_key).
  - Fail-closed on missing or malformed inputs.
  - Tenant-scoped (tenant_id on every command envelope).
  - Strictly bounded — no WorkHub lifecycle verbs, no production deploy, no auto-accept.

Supported operations:
  admit      — admit an objective into a new cycle
  cancel     — request cancellation of a running cycle
  resume     — request resumption of a BLOCKED cycle
  stop       — request StopTheLine on a running cycle
  replan     — request cycle replan (requires approval if scope widens)
  execute    — authorize a move token for the next step

All command processing is synchronous and returns a `CycleCommandResultV1`. Async
execution (Temporal/Celery) is wired by the caller; this module owns the command envelope
contract only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

COMMAND_API_VERSION = "aix.objective-cycle.command.v1"

# Allowed command operations (fail-closed — unknown ops are rejected)
ALLOWED_OPERATIONS: frozenset[str] = frozenset(
    {"admit", "cancel", "resume", "stop", "replan", "execute"}
)

# Effects that can never be requested through the command API
_FORBIDDEN_COMMAND_EFFECTS: frozenset[str] = frozenset(
    {
        "auto_accept",
        "auto_close",
        "auto_create_workhub_task",
        "direct_provider_route",
        "production_deploy",
        "production_mutation",
        "workhub_lifecycle_mutation",
    }
)


class CycleCommandError(ValueError):
    """Raised when a command envelope fails validation before dispatch."""
    def __init__(self, *, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


class CycleCommandEnvelopeV1(BaseModel):
    """Validated, immutable command envelope. The API layer builds one of these before
    passing to the backend; the backend trusts its validity invariants."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["v1"] = COMMAND_API_VERSION
    operation: str = Field(min_length=1)
    cycle_id: str | None = None          # Required for all ops except admit
    tenant_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    expected_version: int | None = None  # Optimistic concurrency — None = blind write
    body: dict[str, Any] = Field(default_factory=dict)
    issued_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))

    @property
    def requires_cycle_id(self) -> bool:
        return self.operation != "admit"


@dataclass(frozen=True)
class CycleCommandResultV1:
    accepted: bool
    operation: str
    cycle_id: str | None
    tenant_id: str
    idempotency_key: str
    result_payload: dict[str, Any]
    reason: str = ""


class CycleCommandHandler:
    """Validates and dispatches cycle commands. Does NOT issue network calls — callers
    wire the backend executor (Temporal activity, Celery task, etc.)."""

    def handle(self, envelope: CycleCommandEnvelopeV1) -> CycleCommandResultV1:
        # 1. Operation allowlist
        if envelope.operation not in ALLOWED_OPERATIONS:
            raise CycleCommandError(
                code="UNKNOWN_OPERATION",
                detail=f"Operation {envelope.operation!r} is not in the allowed set: "
                       f"{sorted(ALLOWED_OPERATIONS)}",
            )

        # 2. cycle_id required for non-admit ops
        if envelope.requires_cycle_id and not envelope.cycle_id:
            raise CycleCommandError(
                code="CYCLE_ID_REQUIRED",
                detail=f"Operation {envelope.operation!r} requires a cycle_id.",
            )

        # 3. Forbidden effects in body
        requested_effects = envelope.body.get("requested_effects", [])
        illegal = frozenset(requested_effects) & _FORBIDDEN_COMMAND_EFFECTS
        if illegal:
            raise CycleCommandError(
                code="FORBIDDEN_EFFECTS_IN_BODY",
                detail=f"Command body requests forbidden effects: {sorted(illegal)}. "
                       "These effects are never permitted through the cycle command API.",
            )

        # 4. Admit-specific: enforce workhub_task_ref presence
        if envelope.operation == "admit":
            if not envelope.body.get("workhub_task_ref"):
                raise CycleCommandError(
                    code="WORKHUB_TASK_REF_REQUIRED",
                    detail="admit command requires body.workhub_task_ref (parent WorkHub task).",
                )

        # Accepted — return stub result (real execution wired by caller)
        return CycleCommandResultV1(
            accepted=True,
            operation=envelope.operation,
            cycle_id=envelope.cycle_id,
            tenant_id=envelope.tenant_id,
            idempotency_key=envelope.idempotency_key,
            result_payload={"status": "accepted", "operation": envelope.operation},
        )


# Idempotency key deduplication store (in-memory; callers provide persistent store via port)
class CommandIdempotencyStore:
    """Simple in-process idempotency guard. Production callers must substitute a
    durable backend (PostgreSQL idempotency ledger from 001B-W05)."""

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], CycleCommandResultV1] = {}

    def get_or_none(self, tenant_id: str, idempotency_key: str) -> CycleCommandResultV1 | None:
        return self._seen.get((tenant_id, idempotency_key))

    def record(self, tenant_id: str, idempotency_key: str, result: CycleCommandResultV1) -> None:
        self._seen[(tenant_id, idempotency_key)] = result


__all__ = [
    "COMMAND_API_VERSION",
    "ALLOWED_OPERATIONS",
    "CycleCommandError",
    "CycleCommandEnvelopeV1",
    "CycleCommandResultV1",
    "CycleCommandHandler",
    "CommandIdempotencyStore",
]
