"""Deterministic workflow/activity contracts for AixObjectiveCycleWorkflow."""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

OBJECTIVE_CYCLE_WORKFLOW_CONTRACT_VERSION = "aix.objective-cycle.temporal-contract.v1"
OBJECTIVE_CYCLE_WORKFLOW_TYPE = "AixObjectiveCycleWorkflow"

_FORBIDDEN_PAYLOAD_KEYS = {
    "access_token",
    "api_key",
    "auto_accept",
    "authorization",
    "caller_authorization",
    "closed_at",
    "lifecycle_state",
    "provider_credential",
    "secret",
    "workhub_status",
}


class ActivityOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ObjectiveCycleWorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: str = OBJECTIVE_CYCLE_WORKFLOW_CONTRACT_VERSION
    workflow_version: int = Field(ge=1)
    tenant_id: str = Field(min_length=1)
    objective_id: str = Field(min_length=1)
    cycle_id: str = Field(min_length=1)
    candidate_sha: str = Field(min_length=7)
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    start_request_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    # Lane-ops #23 M1 owner refs: opaque activation/selection bindings
    # copied unchanged from the canonical activation record by the starter
    # and forwarded into dispatch activity payload_refs. Optional so
    # pre-existing payloads replay deterministically and non-Muse routes
    # are unaffected; a Muse route requires every ref non-empty before
    # the adapter spawns (fail-closed downstream, never defaulted here).
    activation_id: str = ""
    selection_id: str = ""
    workhub_attempt_id: str = ""
    accepted_model_id: str = ""
    worker_id: str = ""


class ObjectiveCycleActivityInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: str = OBJECTIVE_CYCLE_WORKFLOW_CONTRACT_VERSION
    activity_name: str = Field(min_length=1)
    activity_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    objective_id: str = Field(min_length=1)
    cycle_id: str = Field(min_length=1)
    step_id: str | None = None
    attempt_id: str | None = None
    aggregate_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    causation_id: str = Field(min_length=1)
    payload_refs: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_forbidden_payload_keys(self) -> "ObjectiveCycleActivityInput":
        forbidden = set(self.payload_refs) & _FORBIDDEN_PAYLOAD_KEYS
        if forbidden:
            raise ValueError(f"TEMPORAL_FORBIDDEN_PAYLOAD_KEYS:{','.join(sorted(forbidden))}")
        if any(not str(value).strip() for value in self.payload_refs.values()):
            raise ValueError("TEMPORAL_PAYLOAD_REFERENCE_EMPTY")
        return self


class ObjectiveCycleActivityResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: str = OBJECTIVE_CYCLE_WORKFLOW_CONTRACT_VERSION
    activity_id: str = Field(min_length=1)
    outcome: ActivityOutcome
    aggregate_version: int = Field(ge=0)
    reason_code: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    next_state: str = Field(min_length=1)


class ActivityContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    start_to_close_seconds: int = Field(ge=1)
    heartbeat_timeout_seconds: int | None = Field(default=None, ge=1)
    maximum_attempts: int = Field(ge=1)
    idempotency_required: bool = True
    may_mutate_workhub_lifecycle: bool = False
    direct_provider_route_allowed: bool = False


OBJECTIVE_CYCLE_ACTIVITY_CONTRACTS: tuple[ActivityContract, ...] = (
    ActivityContract(name="load_cycle", start_to_close_seconds=30, maximum_attempts=3),
    ActivityContract(name="ground_objective", start_to_close_seconds=120, maximum_attempts=2),
    ActivityContract(name="compile_plan", start_to_close_seconds=120, maximum_attempts=2),
    # Wave 2 Option B: no heartbeat timeout on backend-shelling activities.
    # A single start-up heartbeat cannot cover an 1800s model call, and a 30s
    # heartbeat timeout on a 1-attempt activity turns slow models into
    # timeouts. Bound by start_to_close (= schedule_to_close at the call
    # site); expiry raises ActivityError, which the workflow maps to FAILED
    # with correlation id — never a silent RUNNING_STEP.
    ActivityContract(
        name="dispatch_step",
        start_to_close_seconds=1800,
        heartbeat_timeout_seconds=None,
        maximum_attempts=1,
    ),
    ActivityContract(
        name="verify_step",
        start_to_close_seconds=900,
        heartbeat_timeout_seconds=None,
        maximum_attempts=1,
    ),
    ActivityContract(name="persist_transition", start_to_close_seconds=30, maximum_attempts=3),
    ActivityContract(name="build_review_packet", start_to_close_seconds=120, maximum_attempts=2),
)


def objective_cycle_workflow_id(*, tenant_id: str, objective_id: str, cycle_id: str) -> str:
    raw = f"{tenant_id}|{objective_id}|{cycle_id}"
    readable = re.sub(r"[^a-zA-Z0-9_-]+", "-", f"aix-cycle-{tenant_id}-{cycle_id}").strip("-")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{readable[:180]}-{digest}"


def objective_cycle_activity_id(
    *,
    cycle_id: str,
    activity_name: str,
    step_id: str | None = None,
    attempt_id: str | None = None,
) -> str:
    identity = "|".join((cycle_id, activity_name, step_id or "-", attempt_id or "-"))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"aix-activity-{activity_name}-{digest}"


def build_activity_input(
    *,
    workflow_input: ObjectiveCycleWorkflowInput,
    activity_name: str,
    aggregate_version: int,
    causation_id: str,
    payload_refs: dict[str, str],
    step_id: str | None = None,
    attempt_id: str | None = None,
) -> ObjectiveCycleActivityInput:
    if activity_name not in {item.name for item in OBJECTIVE_CYCLE_ACTIVITY_CONTRACTS}:
        raise ValueError(f"TEMPORAL_ACTIVITY_NOT_REGISTERED:{activity_name}")
    activity_id = objective_cycle_activity_id(
        cycle_id=workflow_input.cycle_id,
        activity_name=activity_name,
        step_id=step_id,
        attempt_id=attempt_id,
    )
    return ObjectiveCycleActivityInput(
        activity_name=activity_name,
        activity_id=activity_id,
        tenant_id=workflow_input.tenant_id,
        objective_id=workflow_input.objective_id,
        cycle_id=workflow_input.cycle_id,
        step_id=step_id,
        attempt_id=attempt_id,
        aggregate_version=aggregate_version,
        idempotency_key=activity_id,
        correlation_id=workflow_input.correlation_id,
        causation_id=causation_id,
        payload_refs=payload_refs,
    )


# Lane-ops #23 M1: the single ordered owner-ref key set threaded
# starter -> ObjectiveCycleWorkflowInput -> dispatch payload_refs ->
# activity command body -> durable backend dispatch -> ExecuteStepRequest.
# One definition reused by every layer; no parallel manifest.
MUSE_OWNER_REF_KEYS: tuple[str, ...] = (
    "activation_id",
    "selection_id",
    "workhub_attempt_id",
    "candidate_sha",
    "plan_sha256",
    "accepted_model_id",
    "worker_id",
)


def muse_owner_refs_from_mapping(source: object) -> dict[str, str]:
    """Copy already-owned opaque refs unchanged, skipping absent/blank ones.

    Accepts a mapping or an object with same-named attributes (e.g. a
    validated ``ObjectiveCycleWorkflowInput``). Values travel byte-for-byte;
    blank values are dropped rather than defaulted — a Muse route refuses
    a missing ref downstream instead of executing unbound. ``payload_refs``
    additionally forbids empty values at validation, so this shape feeds
    it directly.
    """
    refs: dict[str, str] = {}
    getter = None
    if isinstance(source, dict):
        getter = source.get
    for key in MUSE_OWNER_REF_KEYS:
        if getter is not None:
            value = getter(key)
        else:
            value = getattr(source, key, None)
        if isinstance(value, str) and value.strip():
            refs[key] = value
    return refs


def validate_activity_result_authority(result: ObjectiveCycleActivityResult) -> None:
    forbidden_states = {"ACCEPTED", "CLOSED", "DONE", "WORKHUB_ACCEPTED", "WORKHUB_CLOSED"}
    if result.next_state.upper() in forbidden_states:
        raise ValueError("TEMPORAL_WORKHUB_LIFECYCLE_STATE_FORBIDDEN")
