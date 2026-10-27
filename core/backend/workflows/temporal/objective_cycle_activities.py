"""Canonical Temporal activities for AixObjectiveCycleWorkflow.

Single activity boundary for the Objective Cycle runtime path::

    canonical_start -> aix.execution.current -> AixObjectiveCycleWorkflow
        -> ground_objective -> compile_plan -> approval
        -> dispatch_step -> verify_step

Every activity in :data:`OBJECTIVE_CYCLE_ACTIVITY_CONTRACTS` has exactly one
registered implementation here. ``dispatch_step`` and ``verify_step`` execute
through the canonical backend command seam (``dispatch_ready_step`` /
``reverify_and_terminalize``) — the same seam the objective-cycle router
uses — so there is one executor, not a worker-side duplicate. Lifecycle
activities (load/ground/compile/persist/review-packet) execute the engine
authority record: contract validation plus an authority-checked result, which
is the Temporal-executable form of the engine's inline activity gate. No
activity mutates WorkHub lifecycle state or routes providers directly.

temporalio is an optional dependency: when it is missing the module still
imports and the plain async functions remain directly callable (local Night
Ops / unit-test path). The Temporal worker registers these same callables.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from backend.workflows.temporal.objective_cycle_contracts import (
    OBJECTIVE_CYCLE_ACTIVITY_CONTRACTS,
    ActivityOutcome,
    ObjectiveCycleActivityInput,
    ObjectiveCycleActivityResult,
    muse_owner_refs_from_mapping,
    validate_activity_result_authority,
)

try:  # pragma: no cover - optional dependency
    from temporalio import activity as temporal_activity
except ImportError:  # pragma: no cover
    temporal_activity = None  # type: ignore[assignment]

_CANONICAL_ACTIVITY_NAMES: tuple[str, ...] = tuple(
    item.name for item in OBJECTIVE_CYCLE_ACTIVITY_CONTRACTS
)

# Activity -> backend command operation for effect-bearing steps. Lifecycle
# activities have no backend command; they execute the authority record.
_ACTIVITY_COMMANDS: dict[str, str] = {
    "dispatch_step": "dispatch_ready_step",
    "verify_step": "reverify_and_terminalize",
}

BackendFactory = Callable[[], Awaitable[Any]]
_BACKEND_FACTORY: BackendFactory | None = None
_IDEMPOTENCY_CACHE: dict[str, dict[str, Any]] = {}


def bind_backend_factory(factory: BackendFactory | None) -> None:
    """Bind (or clear) the backend factory used by effect-bearing activities.

    The Temporal worker bootstrap binds the same factory the application uses
    (``build_objective_cycle_api_backend``). Tests bind fakes. Unbound
    dispatch/verify calls fail closed instead of executing anywhere.
    """
    global _BACKEND_FACTORY
    _BACKEND_FACTORY = factory


def _heartbeat(details: str) -> None:
    if temporal_activity is None:  # pragma: no cover - no SDK
        return
    try:
        temporal_activity.heartbeat(details)
    except Exception:
        pass


def _validate_input(raw: dict[str, Any]) -> ObjectiveCycleActivityInput:
    if not isinstance(raw, dict):
        raise ValueError("TEMPORAL_ACTIVITY_PAYLOAD_MUST_BE_MAPPING")
    activity_input = ObjectiveCycleActivityInput.model_validate(raw)
    if activity_input.activity_name not in _CANONICAL_ACTIVITY_NAMES:
        raise ValueError(
            f"TEMPORAL_ACTIVITY_NOT_REGISTERED:{activity_input.activity_name}"
        )
    return activity_input


def _cached(activity_id: str) -> dict[str, Any] | None:
    cached = _IDEMPOTENCY_CACHE.get(activity_id)
    if cached is None:
        return None
    replayed = dict(cached)
    replayed["idempotent_replay"] = True
    return replayed


def _remember(activity_id: str, result: dict[str, Any]) -> None:
    _IDEMPOTENCY_CACHE[activity_id] = dict(result)


def _map_backend_outcome(body: Any) -> tuple[ActivityOutcome, str]:
    """Map a backend command payload onto an activity outcome, fail-closed."""
    if isinstance(body, dict):
        lowered = {str(key).lower(): value for key, value in body.items()}
        status = str(
            lowered.get("status") or lowered.get("state") or lowered.get("verdict") or ""
        ).lower()
        if lowered.get("ok") is True or lowered.get("success") is True:
            return ActivityOutcome.SUCCEEDED, "BACKEND_OK"
        # A verification that ran to an honest gap terminal (GAPS_FOUND) is a
        # successful activity execution carrying a domain outcome, not an
        # activity failure: the workflow engine owns the GAPS_FOUND terminal
        # transition downstream. Only genuine gap terminals qualify here.
        phase = str(lowered.get("phase") or "").upper()
        code = str(lowered.get("code") or lowered.get("error") or "").upper()
        if phase == "GAPS_FOUND" and ("GAP" in code or "GAP" in str(lowered.get("terminal_reason") or "").upper()):
            return ActivityOutcome.SUCCEEDED, (code[:120] or "GAPS_FOUND")
        if any(
            marker in status
            for marker in ("blocked", "needs_human", "needs-review", "awaiting")
        ):
            return ActivityOutcome.BLOCKED, "BACKEND_BLOCKED"
        code = str(lowered.get("code") or lowered.get("error") or "").upper()
        if code:
            return ActivityOutcome.FAILED, code[:120]
    return ActivityOutcome.FAILED, "BACKEND_UNRECOGNIZED_RESULT"


async def _execute_via_backend(
    *,
    activity_input: ObjectiveCycleActivityInput,
    operation: str,
    backend: Any | None = None,
) -> dict[str, Any]:
    replayed = _cached(activity_input.activity_id)
    if replayed is not None:
        return replayed
    _heartbeat(f"{activity_input.activity_name}:{activity_input.activity_id}")
    resolved = backend
    if resolved is None and _BACKEND_FACTORY is not None:
        resolved = await _BACKEND_FACTORY()
    if resolved is None:
        return {
            "ok": False,
            "status": "failed",
            "reason_code": "TEMPORAL_BACKEND_NOT_BOUND",
            "activity": activity_input.activity_name,
            "activity_id": activity_input.activity_id,
            "idempotent_replay": False,
        }
    refs = dict(activity_input.payload_refs)
    body: dict[str, Any] = {
        "caller_authorization": refs.get("caller_authorization", ""),
        "correlation_id": activity_input.correlation_id,
        "causation_id": activity_input.causation_id,
        "step_id": activity_input.step_id,
        "attempt_id": activity_input.attempt_id,
    }
    # Lane-ops #23 M1: carry the dispatch owner refs unchanged into the
    # backend command body. Opaque refs only — never credentials, which
    # stay out of the activity payload by contract.
    body.update(muse_owner_refs_from_mapping(refs))
    # expected_version is intentionally None: the workflow engine's
    # aggregate_version and the backend cycle record's version are
    # independent counters, so coupling them would manufacture version
    # conflicts. Concurrency control here is the single canonical workflow
    # plus the contract idempotency key (the backend dedupes on it).
    outcome_body = await resolved.command(
        operation=operation,
        tenant_id=activity_input.tenant_id,
        actor_id=refs.get("actor_id", ""),
        resource_id=activity_input.cycle_id,
        body=body,
        idempotency_key=activity_input.idempotency_key,
        expected_version=None,
    )
    outcome, reason = _map_backend_outcome(outcome_body)
    result = ObjectiveCycleActivityResult(
        activity_id=activity_input.activity_id,
        outcome=outcome,
        aggregate_version=activity_input.aggregate_version + 1,
        reason_code=reason,
        evidence_refs=(f"evidence://{activity_input.activity_name}/{activity_input.activity_id}",),
        next_state="RECORD",
    )
    validate_activity_result_authority(result)
    envelope: dict[str, Any] = {
        "ok": outcome == ActivityOutcome.SUCCEEDED,
        "status": outcome.value.lower(),
        "reason_code": reason,
        "activity": activity_input.activity_name,
        "activity_id": activity_input.activity_id,
        "aggregate_version": result.aggregate_version,
        "evidence_refs": list(result.evidence_refs),
        "backend": outcome_body,
        "idempotent_replay": False,
    }
    _remember(activity_input.activity_id, envelope)
    return envelope


async def _execute_authority_record(
    *, activity_input: ObjectiveCycleActivityInput, next_state: str
) -> dict[str, Any]:
    """Execute the engine authority record for lifecycle activities.

    This is the Temporal-executable form of the engine's inline activity
    gate: same contract validators, same authority function, same
    idempotency key. Step effects are NOT performed here; they execute via
    dispatch_step / verify_step through the backend command seam.
    """
    replayed = _cached(activity_input.activity_id)
    if replayed is not None:
        return replayed
    _heartbeat(f"{activity_input.activity_name}:{activity_input.activity_id}")
    result = ObjectiveCycleActivityResult(
        activity_id=activity_input.activity_id,
        outcome=ActivityOutcome.SUCCEEDED,
        aggregate_version=activity_input.aggregate_version + 1,
        reason_code=f"{activity_input.activity_name.upper()}_RECORDED",
        evidence_refs=(f"evidence://{activity_input.activity_name}/{activity_input.activity_id}",),
        next_state=next_state,
    )
    validate_activity_result_authority(result)
    envelope: dict[str, Any] = {
        "ok": True,
        "status": "recorded",
        "reason_code": result.reason_code,
        "activity": activity_input.activity_name,
        "activity_id": activity_input.activity_id,
        "aggregate_version": result.aggregate_version,
        "evidence_refs": list(result.evidence_refs),
        "next_state": next_state,
        "idempotent_replay": False,
    }
    _remember(activity_input.activity_id, envelope)
    return envelope


def _defn(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    if temporal_activity is None:  # pragma: no cover - no SDK
        return lambda fn: fn
    return temporal_activity.defn(name=f"objective_cycle.{name}")  # type: ignore[no-any-return]


@_defn("load_cycle")
async def load_cycle(raw: dict[str, Any]) -> dict[str, Any]:
    return await _execute_authority_record(
        activity_input=_validate_input(raw), next_state="LOADED"
    )


@_defn("ground_objective")
async def ground_objective(raw: dict[str, Any]) -> dict[str, Any]:
    return await _execute_authority_record(
        activity_input=_validate_input(raw), next_state="PLANNING"
    )


@_defn("compile_plan")
async def compile_plan(raw: dict[str, Any]) -> dict[str, Any]:
    return await _execute_authority_record(
        activity_input=_validate_input(raw), next_state="WAITING_APPROVAL"
    )


@_defn("dispatch_step")
async def dispatch_step(raw: dict[str, Any], backend: Any | None = None) -> dict[str, Any]:
    return await _execute_via_backend(
        activity_input=_validate_input(raw),
        operation=_ACTIVITY_COMMANDS["dispatch_step"],
        backend=backend,
    )


@_defn("verify_step")
async def verify_step(raw: dict[str, Any], backend: Any | None = None) -> dict[str, Any]:
    return await _execute_via_backend(
        activity_input=_validate_input(raw),
        operation=_ACTIVITY_COMMANDS["verify_step"],
        backend=backend,
    )


@_defn("persist_transition")
async def persist_transition(raw: dict[str, Any]) -> dict[str, Any]:
    return await _execute_authority_record(
        activity_input=_validate_input(raw), next_state="PERSISTED"
    )


@_defn("build_review_packet")
async def build_review_packet(raw: dict[str, Any]) -> dict[str, Any]:
    return await _execute_authority_record(
        activity_input=_validate_input(raw), next_state="REVIEW_READY"
    )


CANONICAL_ACTIVITIES: dict[str, Callable[..., Any]] = {
    "load_cycle": load_cycle,
    "ground_objective": ground_objective,
    "compile_plan": compile_plan,
    "dispatch_step": dispatch_step,
    "verify_step": verify_step,
    "persist_transition": persist_transition,
    "build_review_packet": build_review_packet,
}

assert set(CANONICAL_ACTIVITIES) == set(_CANONICAL_ACTIVITY_NAMES), (
    "CANONICAL_ACTIVITIES drifted from OBJECTIVE_CYCLE_ACTIVITY_CONTRACTS"
)
