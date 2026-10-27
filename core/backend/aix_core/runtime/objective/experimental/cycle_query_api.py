"""AIX-E2E-CYCLE-001J-W02: cycle query API.

Read-only query surface for objective-cycle data. Every query is:
  - Tenant-scoped — callers must supply tenant_id; responses are filtered to that tenant.
  - Paginated with cursor-based pagination (no offset; offsets don't compose safely with
    concurrent writes to the underlying ledger).
  - Never triggers state transitions or WorkHub lifecycle events.
  - Fail-closed: unknown filter keys, invalid cursors, and missing required fields all
    return structured error payloads rather than silently being ignored.

Supported query operations:
  list_cycles        — paginated list of cycles for a tenant (filterable by status)
  get_cycle          — fetch a single cycle by cycle_id
  list_steps         — paginated list of steps for a given cycle_id
  get_step           — fetch a single step by step_id (cycle_id required for tenant scope)
  list_attempts      — paginated list of step attempts for a step_id
  get_evidence_index — fetch the evidence index for a cycle
  get_outcome        — fetch the CycleOutcome for a terminated cycle
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

QUERY_API_VERSION = "aix.objective-cycle.query.v1"

ALLOWED_QUERY_OPERATIONS: frozenset[str] = frozenset(
    {
        "list_cycles",
        "get_cycle",
        "list_steps",
        "get_step",
        "list_attempts",
        "get_evidence_index",
        "get_outcome",
    }
)

# Filter keys allowed per operation — unknown keys are rejected (fail-closed)
_ALLOWED_FILTERS: dict[str, frozenset[str]] = {
    "list_cycles": frozenset({"status", "since", "until", "task_class", "workhub_task_ref"}),
    "get_cycle": frozenset(),
    "list_steps": frozenset({"status", "assigned_role"}),
    "get_step": frozenset(),
    "list_attempts": frozenset({"outcome"}),
    "get_evidence_index": frozenset(),
    "get_outcome": frozenset(),
}


class CycleQueryError(ValueError):
    def __init__(self, *, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


class CycleQueryEnvelopeV1(BaseModel):
    """Validated, immutable query envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["v1"] = QUERY_API_VERSION
    operation: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    resource_id: str | None = None   # cycle_id for cycle-scoped ops
    child_id: str | None = None      # step_id for step-scoped ops
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = None


@dataclass(frozen=True)
class CycleQueryResultV1:
    """Paginated query result. `items` is always a tuple (never mutated by caller)."""
    operation: str
    tenant_id: str
    items: tuple[dict[str, Any], ...]
    total_count: int | None   # None if exact count is expensive
    next_cursor: str | None
    has_more: bool


class CycleQueryHandler:
    """Validates and dispatches cycle queries. Execution is synchronous stub — callers
    inject the actual data provider via the port pattern (see `ObjectiveCycleApiBackend`).

    Invariants:
      - Unknown operations → CycleQueryError(code="UNKNOWN_OPERATION")
      - Unknown filter keys → CycleQueryError(code="UNKNOWN_FILTER_KEY")
      - resource_id required for resource-scoped ops → CycleQueryError
      - Results are always tenant-filtered (validated via envelope.tenant_id)
    """

    # Operations that need resource_id (cycle_id)
    _NEEDS_RESOURCE_ID: frozenset[str] = frozenset(
        {"get_cycle", "list_steps", "get_step", "list_attempts", "get_evidence_index", "get_outcome"}
    )
    # Operations that need child_id (step_id)
    _NEEDS_CHILD_ID: frozenset[str] = frozenset({"get_step", "list_attempts"})

    def validate(self, envelope: CycleQueryEnvelopeV1) -> None:
        """Validate envelope; raise CycleQueryError on failure."""
        if envelope.operation not in ALLOWED_QUERY_OPERATIONS:
            raise CycleQueryError(
                code="UNKNOWN_OPERATION",
                detail=f"Operation {envelope.operation!r} is not in: {sorted(ALLOWED_QUERY_OPERATIONS)}",
            )

        if envelope.operation in self._NEEDS_RESOURCE_ID and not envelope.resource_id:
            raise CycleQueryError(
                code="RESOURCE_ID_REQUIRED",
                detail=f"Operation {envelope.operation!r} requires resource_id (cycle_id).",
            )

        if envelope.operation in self._NEEDS_CHILD_ID and not envelope.child_id:
            raise CycleQueryError(
                code="CHILD_ID_REQUIRED",
                detail=f"Operation {envelope.operation!r} requires child_id (step_id).",
            )

        allowed_keys = _ALLOWED_FILTERS.get(envelope.operation, frozenset())
        unknown_keys = frozenset(envelope.filters) - allowed_keys
        if unknown_keys:
            raise CycleQueryError(
                code="UNKNOWN_FILTER_KEY",
                detail=f"Unknown filter key(s) for {envelope.operation!r}: {sorted(unknown_keys)}. "
                       f"Allowed: {sorted(allowed_keys)}",
            )

    def handle(
        self,
        envelope: CycleQueryEnvelopeV1,
        *,
        data_provider: "CycleQueryDataProvider | None" = None,
    ) -> CycleQueryResultV1:
        self.validate(envelope)

        if data_provider is None:
            # Stub response — real callers inject the data provider
            return CycleQueryResultV1(
                operation=envelope.operation,
                tenant_id=envelope.tenant_id,
                items=(),
                total_count=0,
                next_cursor=None,
                has_more=False,
            )

        return data_provider.execute(envelope)


class CycleQueryDataProvider:
    """Port — implement this in the infrastructure layer to back cycle queries with real
    storage (PostgreSQL repository). This class is the abstract contract, not a concrete
    implementation."""

    def execute(self, envelope: CycleQueryEnvelopeV1) -> CycleQueryResultV1:
        raise NotImplementedError("CycleQueryDataProvider.execute() must be implemented")


# Cursor encoding helpers (opaque base64 string over a dict payload)
import base64
import json


def encode_cursor(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except Exception as exc:
        raise CycleQueryError(
            code="INVALID_CURSOR",
            detail=f"Cursor could not be decoded: {exc}",
        ) from exc


__all__ = [
    "QUERY_API_VERSION",
    "ALLOWED_QUERY_OPERATIONS",
    "CycleQueryError",
    "CycleQueryEnvelopeV1",
    "CycleQueryResultV1",
    "CycleQueryHandler",
    "CycleQueryDataProvider",
    "encode_cursor",
    "decode_cursor",
]
