"""Durable bridge from canonical selection events to Temporal-owned Muse work.

This is an ingress adapter, not a workflow engine. Persistence supplies atomic
claims; Temporal owns execution. Every external effect carries a stable
idempotency key so a crashed process may safely retry an incomplete effect.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Literal, Protocol

from backend.workflows.temporal.objective_cycle_contracts import (
    objective_cycle_workflow_id,
)

WORKHUB_BINDING_VERSION = 1
WORKHUB_BINDING_KEYS = frozenset({
    "schema_version",
    "tenant_id",
    "project_id",
    "task_id",
    "task_number",
    "assignment_id",
    "assignment_revision",
    "executor_sub",
    "workhub_attempt_id",
    "attempt_version",
    "controller_attempt_id",
    "lease_epoch",
    "lease_holder",
    "contract_ref",
    "authority_ref",
    "plan_digest",
    "selection",
    "route_digest",
})

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ActivationStore(Protocol):
    async def claim_activation(
        self, activation_id: str, record: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]: ...
    async def claim_attempt_owner(
        self, tenant_id: str, project_id: str, workhub_attempt_id: str,
        activation_id: str,
    ) -> tuple[bool, str | None]: ...
    async def get_activation(self, activation_id: str) -> dict[str, Any] | None: ...
    async def find_attempt_activation(
        self, tenant_id: str, project_id: str, workhub_attempt_id: str
    ) -> list[str]: ...
    async def mark_activation_started(
        self, activation_id: str, result: dict[str, Any]
    ) -> None: ...
    async def mark_activation_result(
        self, activation_id: str, status: str,
        repair_round: int | None = None,
    ) -> None:
        """Record the authoritative terminal workflow outcome.

        Transitions STARTED to SUCCEEDED/FAILED exactly once; a replay
        carrying the same terminal status is a no-op, while a conflicting
        terminal status raises ValueError
        (ACTIVATION_RESULT_REPLAY_CONFLICT).

        Lane-ops #23 repair scope: when repair_round is not None it is
        the CURRENT AUTHORIZED round (the store's repair_count at claim
        time). A delayed mark carrying a stale round never overwrites a
        newly authorized round (raises REPLAY_CONFLICT); a current-round
        terminal-to-terminal transition for a genuinely released repair
        is allowed, same-status stays a no-op.
        """
        ...
    async def get_repair_count(self, activation_id: str) -> int:
        """Return the authorized repair count (current round owner).

        Lane-ops #23: 0 for the initial round, N after N repairs were
        allocated via claim_next_repair (bounded by max_repairs). Used
        to scope result ownership and terminal proof to the current
        authorized round.
        """
        ...
    async def claim_verdict(
        self, verdict_id: str, record: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]: ...
    async def claim_result(
        self, result_id: str, record: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]: ...
    async def claim_result_with_mark(
        self, result_id: str, record: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """Atomically claim result ownership and apply the terminal mark.

        Lane-ops #23 crash-hole owner: one store transaction locks the
        activation, verifies the repair binding against the existing
        allocation/verdict/release records (missing/stale/future
        refuses before insertion), refuses a distinct same-round
        contender, inserts the result row, and moves the activation to
        its fenced terminal outcome. No crash window remains between
        the committed result and the terminal transition, so replay
        never observes a recorded result on a STARTED activation.
        """
        ...
    async def get_result(self, result_id: str) -> dict[str, Any] | None: ...
    async def get_result_by_activation(
        self, activation_id: str
    ) -> dict[str, Any] | None:
        """Return the current-round terminal result for one activation.

        Lane-ops #23: the terminal result row is the authoritative
        workflow proof the by-attempt readback surfaces for supervision,
        scoped to the CURRENT AUTHORIZED round (the store's repair_count
        owner). None when the current round has no result yet (hold;
        a superseded prior round is never surfaced as current). Raises
        ValueError (ACTIVATION_RESULT_AMBIGUOUS) when distinct result
        rows exist for the same round, including legacy rows without a
        repair_round owner: no single authoritative proof can be
        surfaced, and terminal observers must hold (never pick an
        arbitrary/latest row).
        """
        ...
    async def claim_next_repair(
        self, activation_id: str, verdict_id: str
    ) -> tuple[Literal["allocated", "exhausted"], int | None]: ...
    async def claim_release(
        self, release_id: str, record: dict[str, Any]
    ) -> tuple[
        Literal["acquired", "busy", "completed"], dict[str, Any], str | None
    ]: ...
    async def abandon_release(
        self, release_id: str, acquisition_token: str
    ) -> None: ...
    async def mark_release_completed(
        self, release_id: str, acquisition_token: str, result: dict[str, Any]
    ) -> None: ...


TemporalStarter = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
Release = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
ReviewerAuthority = Callable[[dict[str, Any]], Awaitable[bool]]


def _stable_id(prefix: str, *parts: str) -> str:
    material = "\x1f".join(parts).encode()
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:24]}"


def _require_ids(*values: str) -> None:
    if any(not _ID.fullmatch(value) for value in values):
        raise ValueError("EVENT_ID_INVALID")


def _require_evidence_ref(value: str) -> None:
    if not value or len(value) > 512 or any(ord(char) < 32 for char in value):
        raise ValueError("VERDICT_EVIDENCE_INVALID")


@dataclass(frozen=True)
class SelectionEvent:
    tenant_id: str
    objective_id: str
    cycle_id: str
    selection_id: str
    mandate_id: str
    session_id: str
    worker_id: str
    accepted: bool
    workflow_version: int
    candidate_sha: str
    plan_sha256: str
    correlation_id: str
    model: str = "Muse"
    executor: str = "muse-lane"
    max_repairs: int = 2
    workhub_binding: dict[str, Any] = field(default_factory=dict)


def require_workhub_binding(binding: Any, *, tenant_id: str) -> dict[str, Any]:
    """Validate the closed versioned WorkHub binding (lane-ops #23).

    Required values are never recorded-absent: every key must be
    present, identifiers non-empty, and counters positive integers.
    """
    if not isinstance(binding, dict) or not binding:
        raise ValueError("WORKHUB_BINDING_MISSING")
    if binding.get("schema_version") != WORKHUB_BINDING_VERSION:
        raise ValueError("WORKHUB_BINDING_VERSION_UNSUPPORTED")
    missing = [key for key in WORKHUB_BINDING_KEYS if key not in binding]
    if missing:
        raise ValueError("WORKHUB_BINDING_INCOMPLETE")
    for key in (
        "tenant_id", "project_id", "task_id", "assignment_id",
        "executor_sub", "workhub_attempt_id", "controller_attempt_id",
        "lease_holder", "contract_ref", "authority_ref", "plan_digest",
        "route_digest",
    ):
        value = binding.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("WORKHUB_BINDING_FIELD_EMPTY")
    for key in (
        "task_number", "assignment_revision", "attempt_version",
        "lease_epoch",
    ):
        value = binding.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("WORKHUB_BINDING_COUNTER_INVALID")
    selection = binding.get("selection")
    if not isinstance(selection, dict) or not selection:
        raise ValueError("WORKHUB_BINDING_SELECTION_MISSING")
    if binding["tenant_id"] != tenant_id:
        raise ValueError("WORKHUB_BINDING_TENANT_MISMATCH")
    return dict(binding)


@dataclass(frozen=True)
class VerdictEvent:
    tenant_id: str
    activation_id: str
    verdict_id: str
    outcome: str
    worker_id: str
    reviewer_id: str
    evidence_ref: str
    result_id: str = ""


@dataclass(frozen=True)
class ActivationResultEvent:
    tenant_id: str
    activation_id: str
    result_id: str
    worker_id: str
    status: str
    evidence_ref: str
    # Lane-ops #23 repair binding: the canonical authorized RELEASED
    # repair identity this result answers. None/"": the initial round
    # (round 0) legacy claim, accepted only while no repair is
    # authorized. repair_number >= 1 with repair_verdict_id: the exact
    # fail-verdict allocation plus its completed repair release; the
    # store verifies allocation/verdict/release records atomically and
    # refuses missing/stale/future bindings before insertion, so a
    # delayed distinct old result can never capture a newer round.
    repair_number: int | None = None
    repair_verdict_id: str = ""


# Every outer SelectionEvent field that drives an activation effect and
# has a counterpart inside the verified WorkHub binding selection. The
# ingress and the pre-start gate require exact equality on all of these,
# so an approved inner binding can never launch a substituted outer
# candidate/cycle. Fields with no selection counterpart (model, executor,
# workflow_version, max_repairs) stay caller-chosen within validation and
# remain covered by the exact replay comparison on the stored record.
SELECTION_BINDING_FIELDS = (
    "tenant_id",
    "objective_id",
    "cycle_id",
    "selection_id",
    "mandate_id",
    "session_id",
    "worker_id",
    "candidate_sha",
    "plan_sha256",
    "correlation_id",
)


def _canonical_verified_selection(
    verified: Any, binding: dict[str, Any]
) -> dict[str, Any]:
    """Canonical verified inner selection for outer-field binding.

    The verifier proves the binding selection against supported owner
    reads; its returned selection is canonical. Verifiers that predate
    the selection proof (returning only an approval marker) fall back to
    the shape-validated binding selection they just approved.
    """
    if isinstance(verified, dict):
        selection = verified.get("selection")
        if isinstance(selection, dict) and selection:
            return dict(selection)
    selection = binding.get("selection")
    if isinstance(selection, dict) and selection:
        return dict(selection)
    raise ValueError("WORKHUB_BINDING_SELECTION_MISSING")


def _require_outer_selection_binding(
    event: SelectionEvent, selection: dict[str, Any]
) -> None:
    """Bind every effect-driving outer field to the verified selection."""
    for field in SELECTION_BINDING_FIELDS:
        if getattr(event, field) != selection.get(field):
            raise ValueError("SELECTION_BINDING_MISMATCH")


def _require_binding_selection_canonical(
    verified: Any, binding: dict[str, Any]
) -> None:
    """Fail closed when the binding disagrees with the proven selection.

    The verifier's returned selection is authoritative; a binding whose
    inner selection differs from that proof is inconsistent on its face
    and refuses before any durable write or effect.
    """
    if isinstance(verified, dict):
        proven = verified.get("selection")
        if isinstance(proven, dict) and proven:
            if proven != binding.get("selection"):
                raise ValueError("SELECTION_BINDING_MISMATCH")


_RESULT_CORE_KEYS = (
    "tenant_id", "activation_id", "result_id", "worker_id", "status",
    "evidence_ref",
)


def _result_core(record: dict[str, Any]) -> dict[str, Any]:
    """Byte-identity for exact retries, ignoring the round owner.

    Lane-ops #23: an exact old-result replay (same core) must deduplicate
    to its original round and never become a new-round result or
    overwrite the current round.
    """
    return {key: record.get(key) for key in _RESULT_CORE_KEYS}


def _result_round(record: dict[str, Any]) -> int | None:
    """Round owner carried on a stored result row, if any."""
    value = record.get("repair_round")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _result_binding(record: dict[str, Any]) -> tuple[int | None, str]:
    """Normalized (repair_number, repair_verdict_id) binding of a row/event."""
    number = record.get("repair_number")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        number = None
    verdict = record.get("repair_verdict_id")
    if not isinstance(verdict, str):
        verdict = ""
    return (number, verdict)


def _is_exact_replay(
    existing: dict[str, Any], record: dict[str, Any], incoming_round: int
) -> bool:
    """Exact-replay identity: core plus round owner plus repair binding.

    The same result_id with a different core, round, or binding is a
    conflicting replay, never a deduplication: a recorded round-0
    result replayed as a newer round refuses instead of capturing it.
    Legacy rows without a round owner mean round 0 only.
    """
    if _result_core(existing) != _result_core(record):
        return False
    if _result_binding(existing) != _result_binding(record):
        return False
    stored_round = _result_round(existing)
    if stored_round is None:
        stored_round = 0
    return int(stored_round) == int(incoming_round)


AttemptAuthorityVerifier = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class MuseActivationCoordinator:
    def __init__(
        self,
        *,
        store: ActivationStore,
        temporal_start: TemporalStarter,
        release: Release,
        reviewer_authority: ReviewerAuthority,
        verifier: AttemptAuthorityVerifier,
    ) -> None:
        self._store = store
        self._temporal_start = temporal_start
        self._release = release
        self._reviewer_authority = reviewer_authority
        self._verifier = verifier

    async def accept_selection(self, event: SelectionEvent) -> dict[str, Any]:
        if not event.accepted:
            raise ValueError("SELECTION_NOT_ACCEPTED")
        _require_ids(
            event.tenant_id,
            event.objective_id,
            event.cycle_id,
            event.selection_id,
            event.mandate_id,
            event.session_id,
            event.worker_id,
            event.correlation_id,
            event.model,
            event.executor,
        )
        if not 0 <= event.max_repairs <= 10:
            raise ValueError("REPAIR_LIMIT_INVALID")
        binding = require_workhub_binding(
            event.workhub_binding, tenant_id=event.tenant_id
        )
        # Ingress authority precedes any durable write: the verifier
        # proves the inner selection against supported owner reads, and
        # every effect-driving outer field must equal that canonical
        # selection before ownership is claimed or anything is inserted.
        # A tampered outer candidate/cycle refuses here and inserts
        # nothing, so it can neither start nor poison recovery.
        verified = await self._verifier(binding)
        _require_binding_selection_canonical(verified, binding)
        _require_outer_selection_binding(
            event, _canonical_verified_selection(verified, binding)
        )
        canonical_selection = (
            event.tenant_id,
            event.objective_id,
            event.cycle_id,
            event.selection_id,
        )
        # One canonical selection has exactly one activation/workflow identity.
        # The complete execution binding is persisted under that key and exact-
        # compared below, so a substitution cannot mint a parallel activation.
        activation_id = _stable_id("act", *canonical_selection)
        workflow_id = objective_cycle_workflow_id(
            tenant_id=event.tenant_id,
            objective_id=event.objective_id,
            cycle_id=event.cycle_id,
        )
        record = {
            **asdict(event),
            "activation_id": activation_id,
            "workflow_id": workflow_id,
            "status": "STARTING",
        }
        # Per-attempt ownership is claimed atomically BEFORE any
        # activation row exists: a conflicting attempt refuses here and
        # inserts nothing, so it can never poison the legitimate
        # STARTING record or its recovery.
        owned, owner_id = await self._store.claim_attempt_owner(
            binding["tenant_id"],
            binding["project_id"],
            binding["workhub_attempt_id"],
            activation_id,
        )
        if not owned and owner_id != activation_id:
            raise ValueError("ACTIVATION_ATTEMPT_BINDING_CONFLICT")
        claimed, current = await self._store.claim_activation(activation_id, record)
        if not claimed:
            expected = {key: record[key] for key in record if key != "status"}
            observed = {key: current.get(key) for key in expected}
            if observed != expected:
                raise ValueError("ACTIVATION_REPLAY_CONFLICT")
            if current.get("status") in ("STARTED", "SUCCEEDED", "FAILED"):
                # Exact replay at any lifecycle stage is a readback: the
                # workflow identity is stable, so no second start follows
                # a terminal result either.
                return {**current, "deduplicated": True}
        rivals = await self._store.find_attempt_activation(
            binding["tenant_id"],
            binding["project_id"],
            binding["workhub_attempt_id"],
        )
        if any(aid != activation_id for aid in rivals):
            raise ValueError("ACTIVATION_ATTEMPT_BINDING_CONFLICT")
        # Authority is reverified immediately before Temporal start: the
        # ingress proof predates queueing, and lease/contract may have
        # moved since. The outer fields are rebound to the fresh canonical
        # selection here, so a substitution between ingress and start
        # cannot reach the starter. A refusal starts nothing.
        reverified = await self._verifier(binding)
        _require_binding_selection_canonical(reverified, binding)
        _require_outer_selection_binding(
            event, _canonical_verified_selection(reverified, binding)
        )
        try:
            result = await self._temporal_start(
                {**record, "idempotency_key": workflow_id}
            )
        except Exception:
            # STARTING remains durable; retry uses exactly the same workflow identity.
            raise
        await self._store.mark_activation_started(activation_id, result)
        return {
            **record,
            "temporal_result": dict(result),
            "status": "STARTED",
            "deduplicated": not claimed,
        }

    async def _release_once(
        self, release_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        state, current, acquisition_token = await self._store.claim_release(
            release_id, payload
        )
        if state == "completed":
            return {**current, "released": True, "deduplicated": True}
        if state == "busy":
            return {
                **current,
                "released": False,
                "release_pending": True,
                "deduplicated": True,
            }
        if acquisition_token is None:
            raise RuntimeError("RELEASE_ACQUISITION_TOKEN_MISSING")
        effect = {**payload, "release_id": release_id, "idempotency_key": release_id}
        try:
            result = await self._release(effect)
        except Exception:
            await self._store.abandon_release(release_id, acquisition_token)
            raise
        await self._store.mark_release_completed(release_id, acquisition_token, result)
        return {
            **payload,
            "release_result": dict(result),
            "released": True,
            "deduplicated": False,
        }

    async def _recover_unfinished_result_mark(
        self, activation_id: str, status: str, existing: dict[str, Any]
    ) -> None:
        """Repair an incomplete same-round terminal mark on exact replay.

        Lane-ops #23 crash-hole recovery: when a result row committed but
        the process died before the terminal transition, the activation
        stays STARTED while the result is recorded. An exact replay of
        that same result (same identity: core, round owner, binding)
        completes the unfinished mark under the round fence instead of
        returning while STARTED persists. An exact OLD replay after a
        newer round was authorized is harmless: its round no longer
        matches the current authorized round, so no mark is attempted
        and the newer round is never touched.
        """
        existing_round = _result_round(existing)
        if existing_round is None:
            # Legacy rows without a round owner mean round 0 only.
            existing_round = 0
        current_round = int(
            await self._store.get_repair_count(activation_id)
        )
        if int(existing_round) != int(current_round):
            return
        try:
            await self._store.mark_activation_result(
                activation_id, status, int(existing_round)
            )
        except ValueError as exc:
            if str(exc) != "ACTIVATION_RESULT_REPLAY_CONFLICT":
                raise
            # The round moved under this replay (a repair was authorized
            # concurrently): the replay is stale and stays harmless.

    async def accept_result(self, event: ActivationResultEvent) -> dict[str, Any]:
        _require_ids(
            event.tenant_id, event.activation_id, event.result_id, event.worker_id
        )
        _require_evidence_ref(event.evidence_ref)
        if event.status not in {"SUCCEEDED", "FAILED"}:
            raise ValueError("ACTIVATION_RESULT_STATUS_INVALID")
        if event.repair_number is not None:
            if (
                isinstance(event.repair_number, bool)
                or not isinstance(event.repair_number, int)
                or event.repair_number < 1
            ):
                raise ValueError(
                    "ACTIVATION_RESULT_REPLAY_CONFLICT:"
                    "REPAIR_BINDING_INVALID"
                )
            _require_ids(event.repair_verdict_id)
        elif event.repair_verdict_id:
            raise ValueError(
                "ACTIVATION_RESULT_REPLAY_CONFLICT:REPAIR_BINDING_INVALID"
            )
        activation = await self._store.get_activation(event.activation_id)
        if activation is None:
            raise ValueError("ACTIVATION_RESULT_ACTIVATION_UNKNOWN")
        if (
            activation.get("tenant_id") != event.tenant_id
            or activation.get("worker_id") != event.worker_id
        ):
            raise ValueError("ACTIVATION_RESULT_BINDING_MISMATCH")
        record = asdict(event)
        if event.repair_number is None:
            incoming_round = 0
        else:
            incoming_round = int(event.repair_number)
        # Exact old-result replay (same identity by result_id: core,
        # round owner, repair binding) deduplicates to its original
        # round: it never becomes a new-round result and never
        # overwrites the current round. When the original claim
        # committed but the terminal mark never landed (crash between
        # the two), this same replay completes the unfinished
        # same-round mark under the round fence.
        existing = await self._store.get_result(event.result_id)
        if existing is not None:
            if _is_exact_replay(existing, record, incoming_round):
                await self._recover_unfinished_result_mark(
                    event.activation_id, event.status, existing
                )
                return {
                    **existing, "status": "RECORDED", "deduplicated": True,
                }
            raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
        # Bind this unseen result to the canonical authorized round
        # BEFORE inserting: a result without a repair binding is the
        # initial round-0 claim and is accepted only while no repair is
        # authorized; a bound result must name exactly the current
        # authorized round. A delayed distinct old result arriving after
        # a repair allocation is stale and refuses here — it is never
        # stamped with whatever repair_count exists at arrival. The
        # store re-verifies round, verdict allocation, and completed
        # release atomically under its lock, so concurrent
        # allocation/result races still fence exactly once.
        target_round = incoming_round
        try:
            owned = await self._store.get_result_by_activation(
                event.activation_id
            )
        except ValueError as exc:
            raise ValueError(
                "ACTIVATION_RESULT_REPLAY_CONFLICT") from exc
        current_round = int(await self._store.get_repair_count(
            event.activation_id))
        if target_round != current_round:
            raise ValueError(
                "ACTIVATION_RESULT_REPLAY_CONFLICT:REPAIR_BINDING_STALE"
            )
        if owned is not None:
            # Same-round distinct contender (same current round already
            # owns a different core): lose before insertion. Preserve
            # the historical different-status refusal shape.
            if event.status != activation.get("status"):
                raise ValueError("ACTIVATION_RESULT_ACTIVATION_UNKNOWN")
            raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
        # No current-round result: this is the initial (round 0) or the
        # next genuinely released repair round (round == repair_count).
        # A different-status repair outcome is allowed here; it will
        # move the activation to the new round's terminal outcome via
        # the fenced mark. A delayed old mark/result with a stale round
        # cannot overwrite the new round (store enforces).
        if activation.get("status") == "STARTED":
            terminal_replay = False
        elif (activation.get("status") == event.status
                and event.status in {"SUCCEEDED", "FAILED"}):
            terminal_replay = True
        elif activation.get("status") in {"SUCCEEDED", "FAILED"}:
            terminal_replay = False
        else:
            raise ValueError("ACTIVATION_RESULT_ACTIVATION_UNKNOWN")
        record = {**record, "repair_round": target_round}
        claim_with_mark = getattr(
            self._store, "claim_result_with_mark", None
        )
        if claim_with_mark is not None:
            # Maintained path: ownership and the fenced terminal mark
            # commit in one store transaction — no crash window.
            claimed, current = await claim_with_mark(
                event.result_id, record
            )
            if not claimed:
                if not _is_exact_replay(current, record, target_round):
                    raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
                await self._recover_unfinished_result_mark(
                    event.activation_id, event.status, current
                )
                return {
                    **current, "status": "RECORDED", "deduplicated": True,
                }
            return {**record, "status": "RECORDED", "deduplicated": not claimed}
        claimed, current = await self._store.claim_result(event.result_id, record)
        if not claimed:
            if not _is_exact_replay(current, record, target_round):
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
            await self._recover_unfinished_result_mark(
                event.activation_id, event.status, current
            )
            return {
                **current, "status": "RECORDED", "deduplicated": True,
            }
        if not terminal_replay:
            # The result row is the authoritative terminal workflow state:
            # the activation itself transitions to SUCCEEDED/FAILED so
            # supervision observes terminality instead of renewing STARTED.
            # For a new repair round with a different outcome this moves
            # terminal-to-terminal under the round fence; a stale round
            # refuses inside the store and never overwrites the current.
            # No TypeError fallback: silently dropping the fence would
            # let a delayed mark overwrite a newly authorized round.
            await self._store.mark_activation_result(
                event.activation_id, event.status, target_round
            )
        return {**record, "status": "RECORDED", "deduplicated": not claimed}

    async def accept_verdict(self, event: VerdictEvent) -> dict[str, Any]:
        _require_ids(
            event.tenant_id,
            event.activation_id,
            event.verdict_id,
            event.worker_id,
            event.reviewer_id,
        )
        _require_evidence_ref(event.evidence_ref)
        if event.outcome not in {"pass", "fail", "blocked"}:
            raise ValueError("VERDICT_OUTCOME_INVALID")
        activation = await self._store.get_activation(event.activation_id)
        if activation is None or activation.get("accepted") is not True:
            raise ValueError("VERDICT_ACTIVATION_UNKNOWN")
        if (
            activation.get("tenant_id") != event.tenant_id
            or activation.get("worker_id") != event.worker_id
        ):
            raise ValueError("VERDICT_ACTIVATION_BINDING_MISMATCH")
        result = await self._store.get_result(event.result_id)
        if (
            result is None
            or result.get("activation_id") != event.activation_id
            or result.get("tenant_id") != event.tenant_id
            or result.get("worker_id") != event.worker_id
            or result.get("evidence_ref") != event.evidence_ref
        ):
            raise ValueError("VERDICT_RESULT_BINDING_MISMATCH")
        if event.worker_id == event.reviewer_id:
            raise ValueError("VERDICT_SELF_REVIEW_DENIED")
        authority_input = {**asdict(event), "activation": dict(activation)}
        if not await self._reviewer_authority(authority_input):
            raise ValueError("VERDICT_REVIEWER_UNAUTHORISED")
        verdict = asdict(event)
        claimed, current = await self._store.claim_verdict(event.verdict_id, verdict)
        if not claimed and current != verdict:
            raise ValueError("VERDICT_REPLAY_CONFLICT")

        if event.outcome == "pass":
            payload = {**verdict, "kind": "successor", "status": "READY"}
            result = await self._release_once(
                f"{event.activation_id}:successor", payload
            )
        elif event.outcome == "fail":
            state, repair_number = await self._store.claim_next_repair(
                event.activation_id, event.verdict_id
            )
            if state == "exhausted":
                return {
                    **verdict,
                    "status": "BLOCKED",
                    "released": False,
                    "deduplicated": not claimed,
                }
            payload = {
                **verdict,
                "kind": "repair",
                "repair_number": repair_number,
                "status": "READY",
            }
            result = await self._release_once(
                f"{event.activation_id}:repair:{repair_number}", payload
            )
        else:
            return {
                **verdict,
                "status": "BLOCKED",
                "released": False,
                "deduplicated": not claimed,
            }
        return {
            **result,
            "status": "RELEASED" if result.get("released") else "RELEASE_PENDING",
        }
