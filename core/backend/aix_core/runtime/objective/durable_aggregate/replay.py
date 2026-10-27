"""C011: event replay + decision-state snapshot reconstruction.

Serving dicts in `DurableObjectiveCycleApiBackend` are projections. Decision-
relevant state must reconstruct from durable snapshots (and optional event fold)
without inventing fields from workspace paths.
"""

from __future__ import annotations

import json
import os
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Protocol
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

DECISION_STATE_SCHEMA = "aix.decision_state.v1"

REDUCER_INVARIANTS: tuple[str, ...] = (
    "Events for an aggregate are append-only and ordered by sequence_number.",
    "A gap or duplicate sequence_number fails closed (CORRUPT_EVENT_STREAM).",
    "Snapshot at sequence N folded with events N+1..M yields state at M.",
    "Decision state is reconstructed from durable payloads only — never from workspace paths.",
    "Cross-tenant snapshot load is rejected.",
    "Unknown decision schema_version fails closed.",
    "cycle_id maps to run_id; aggregate_type 'cycle' and 'run' are aliases during migration.",
)


class ReplayError(RuntimeError):
    """Fail-closed replay / reconstruction error."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class AggregateSnapshot:
    tenant_id: str
    aggregate_type: str
    aggregate_id: str
    sequence_number: int
    schema_version: str
    snapshot: dict[str, Any]


class ProjectionStore(Protocol):
    def upsert_snapshot(
        self,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        sequence_number: int,
        schema_version: str,
        snapshot: Mapping[str, Any],
    ) -> None: ...

    def latest_snapshot(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> AggregateSnapshot | None: ...


class InMemoryProjectionStore:
    """Process-local projection store for inprocess mode and restart simulations."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str, str], list[AggregateSnapshot]] = {}

    def upsert_snapshot(
        self,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        sequence_number: int,
        schema_version: str,
        snapshot: Mapping[str, Any],
    ) -> None:
        if not tenant_id.strip():
            raise ReplayError("EMPTY_TENANT", "tenant_id required")
        key = (tenant_id, _normalize_aggregate_type(aggregate_type), aggregate_id)
        snap = AggregateSnapshot(
            tenant_id=tenant_id,
            aggregate_type=key[1],
            aggregate_id=aggregate_id,
            sequence_number=int(sequence_number),
            schema_version=schema_version,
            snapshot=deepcopy(dict(snapshot)),
        )
        bucket = self._by_key.setdefault(key, [])
        for i, existing in enumerate(bucket):
            if existing.sequence_number == snap.sequence_number:
                bucket[i] = snap
                return
        bucket.append(snap)
        bucket.sort(key=lambda s: s.sequence_number)

    def latest_snapshot(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> AggregateSnapshot | None:
        key = (tenant_id, _normalize_aggregate_type(aggregate_type), aggregate_id)
        bucket = self._by_key.get(key) or []
        return bucket[-1] if bucket else None


class JsonFileProjectionStore:
    """Atomic filesystem projection store for single-node durable deployments.

    This is intentionally a projection/snapshot store, not an event database. It
    gives the in-process runtime a crash-recovery option when Postgres is
    unavailable, while preserving the same tenant-scoped ProjectionStore contract.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, tenant_id: str, aggregate_type: str, aggregate_id: str) -> Path:
        normalized = _normalize_aggregate_type(aggregate_type)
        digest = sha256(
            f"{tenant_id}\0{normalized}\0{aggregate_id}".encode("utf-8")
        ).hexdigest()
        return self._root / normalized / f"{digest}.json"

    def upsert_snapshot(
        self,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        sequence_number: int,
        schema_version: str,
        snapshot: Mapping[str, Any],
    ) -> None:
        if not tenant_id.strip():
            raise ReplayError("EMPTY_TENANT", "tenant_id required")
        normalized = _normalize_aggregate_type(aggregate_type)
        path = self._path(tenant_id, normalized, aggregate_id)
        payload = {
            "tenant_id": tenant_id,
            "aggregate_type": normalized,
            "aggregate_id": aggregate_id,
            "sequence_number": int(sequence_number),
            "schema_version": schema_version,
            "snapshot": deepcopy(dict(snapshot)),
        }
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file():
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                    if int(existing.get("sequence_number") or 0) > int(sequence_number):
                        return
                except (OSError, ValueError, TypeError):
                    raise ReplayError("CORRUPT_SNAPSHOT_FILE", str(path))
            tmp = path.with_suffix(f".{os.getpid()}.{new_ulid()}.tmp")
            tmp.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
                encoding="utf-8",
            )
            for attempt in range(5):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if attempt == 4:
                        tmp.unlink(missing_ok=True)
                        raise
                    time.sleep(0.02 * (attempt + 1))

    def latest_snapshot(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> AggregateSnapshot | None:
        path = self._path(tenant_id, aggregate_type, aggregate_id)
        with self._lock:
            if not path.is_file():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                raise ReplayError("CORRUPT_SNAPSHOT_FILE", str(path)) from exc
        if (
            str(payload.get("tenant_id") or "") != tenant_id
            or str(payload.get("aggregate_id") or "") != aggregate_id
            or _normalize_aggregate_type(str(payload.get("aggregate_type") or ""))
            != _normalize_aggregate_type(aggregate_type)
        ):
            raise ReplayError("CROSS_TENANT_SNAPSHOT", aggregate_id)
        return AggregateSnapshot(
            tenant_id=tenant_id,
            aggregate_type=_normalize_aggregate_type(aggregate_type),
            aggregate_id=aggregate_id,
            sequence_number=int(payload.get("sequence_number") or 0),
            schema_version=str(payload.get("schema_version") or ""),
            snapshot=deepcopy(dict(payload.get("snapshot") or {})),
        )


def _normalize_aggregate_type(aggregate_type: str) -> str:
    t = str(aggregate_type or "").strip().lower()
    if t in {"cycle", "run"}:
        return "run"
    if t == "objective":
        return "objective"
    return t


def decision_state_objective(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical byte-comparable objective decision fields."""
    dod = fields.get("definition_of_done") or ()
    allowed = fields.get("allowed_files") or ()
    return {
        "objective_id": str(fields["objective_id"]),
        "tenant_id": str(fields["tenant_id"]),
        "workhub_task_id": str(fields.get("workhub_task_id") or ""),
        "objective": str(fields.get("objective") or ""),
        "original_objective": str(
            fields.get("original_objective") or fields.get("objective") or ""
        ),
        "task_class": str(fields.get("task_class") or ""),
        "definition_of_done": list(dod),
        "repository_id": str(fields.get("repository_id") or ""),
        "allowed_files": list(allowed),
        "project_id": fields.get("project_id"),
        "task_number": fields.get("task_number"),
        "version": int(fields.get("version") or 1),
        "created_at": str(fields.get("created_at") or ""),
        "workhub_task_created": bool(fields.get("workhub_task_created") or False),
        "task_family": str(fields.get("task_family") or "UNKNOWN"),
        "conversation_id": fields.get("conversation_id"),
        "objective_source_message_id": fields.get("objective_source_message_id"),
    }


def decision_state_run(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical byte-comparable run/cycle decision fields (no workspace guessing)."""
    budgets = fields.get("budgets") or {}
    return {
        "cycle_id": str(fields["cycle_id"]),
        "run_id": str(fields.get("run_id") or fields["cycle_id"]),
        "objective_id": str(fields["objective_id"]),
        "tenant_id": str(fields["tenant_id"]),
        "candidate_sha": str(fields.get("candidate_sha") or ""),
        "budgets": {str(k): int(v) for k, v in dict(budgets).items()},
        "phase": str(fields.get("phase") or "STARTING"),
        "version": int(fields.get("version") or 1),
        "terminal_reason": fields.get("terminal_reason"),
        "last_error_detail": fields.get("last_error_detail"),
        "approval_reference": fields.get("approval_reference"),
        "plan_id": str(fields.get("plan_id") or ""),
        "plan_revision": int(fields.get("plan_revision") or 1),
        "approval_gate_id": fields.get("current_gate_id") or fields.get("approval_gate_id"),
        "approved_gate_id": fields.get("approved_gate_id"),
        "approved_plan_revision": fields.get("approved_plan_revision"),
        "approved_operations": list(fields.get("approved_operations") or ()),
        "approved_resources": list(fields.get("approved_resources") or ()),
        "event_sequence": int(fields.get("event_sequence") or 0),
        "trace_id": str(fields.get("trace_id") or ""),
        "created_at": str(fields.get("created_at") or ""),
        "retry_budget_remaining": int(fields.get("retry_budget_remaining") or 0),
        "replan_budget_remaining": int(fields.get("replan_budget_remaining") or 0),
        "cancel_requested": bool(fields.get("cancel_requested") or False),
        "current_step_cursor": fields.get("current_step_cursor"),
        "dispatch_state": str(fields.get("dispatch_state") or "IDLE"),
        "dispatch_required": bool(fields.get("dispatch_required") or False),
        "execution_claim_id": fields.get("execution_claim_id"),
        "execution_claim_step_id": fields.get("execution_claim_step_id"),
        "execution_claim_attempt": int(fields.get("execution_claim_attempt") or 0),
        "execution_claim_expired": bool(fields.get("execution_claim_expired") or False),
        "active_step_started_at": fields.get("active_step_started_at"),
        "step_lease_expires_at": fields.get("step_lease_expires_at"),
        "last_progress_at": fields.get("last_progress_at"),
        "previous_step_id": fields.get("previous_step_id"),
    }


def decision_bytes(decision: Mapping[str, Any]) -> bytes:
    return json.dumps(decision, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def wrap_objective_snapshot(*, decision: Mapping[str, Any], sequence_number: int) -> dict[str, Any]:
    return {
        "schema_version": DECISION_STATE_SCHEMA,
        "kind": "objective",
        "sequence_number": int(sequence_number),
        "decision": dict(decision),
    }


def wrap_run_snapshot(
    *,
    decision: Mapping[str, Any],
    sequence_number: int,
    engine_history: list[dict[str, Any]] | None = None,
    real_execute: Mapping[str, Any] | None = None,
    model_routes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": DECISION_STATE_SCHEMA,
        "kind": "run",
        "sequence_number": int(sequence_number),
        "decision": dict(decision),
        "engine_history": list(engine_history or []),
        # Durable execution refs only — callers must not rely on live workspace paths
        # for decision equivalence (C011 DoD).
        "real_execute": deepcopy(dict(real_execute)) if real_execute else None,
        "model_routes": dict(model_routes or {}),
    }


def validate_snapshot_envelope(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    version = str(snapshot.get("schema_version") or "")
    if version != DECISION_STATE_SCHEMA:
        raise ReplayError("UNKNOWN_DECISION_SCHEMA", version or "<missing>")
    kind = str(snapshot.get("kind") or "")
    decision = snapshot.get("decision")
    if not isinstance(decision, dict):
        raise ReplayError("INVALID_SNAPSHOT", "decision missing")
    if kind not in {"objective", "run"}:
        raise ReplayError("INVALID_SNAPSHOT", f"kind={kind!r}")
    return dict(snapshot)


def fold_events(
    base: Mapping[str, Any] | None,
    events: SequenceLike,
    *,
    from_sequence: int = 0,
) -> dict[str, Any]:
    """Fold ordered events onto a base decision snapshot.

    Event payloads may carry a full `decision` blob or sparse patches under
    `decision_patch`. Sequence must be contiguous starting at from_sequence+1
    when base is present, or at 1 when base is None.
    """
    state: dict[str, Any] = deepcopy(dict(base)) if base else {}
    expected = int(from_sequence) + 1 if base is not None else 1
    seen: set[int] = set()
    for raw in events:
        seq = int(_event_seq(raw))
        if seq in seen:
            raise ReplayError("CORRUPT_EVENT_STREAM", f"duplicate sequence {seq}")
        if seq != expected:
            raise ReplayError(
                "CORRUPT_EVENT_STREAM",
                f"expected sequence {expected} got {seq}",
            )
        seen.add(seq)
        expected = seq + 1
        payload = _event_payload(raw)
        if "decision" in payload and isinstance(payload["decision"], dict):
            state = deepcopy(payload["decision"])
        elif "decision_patch" in payload and isinstance(payload["decision_patch"], dict):
            state = {**state, **deepcopy(payload["decision_patch"])}
        elif payload:
            # Sparse historical ledger events: merge known decision keys only.
            patch = {k: v for k, v in payload.items() if k in _DECISION_KEYS}
            if patch:
                state = {**state, **patch}
    return state


# Typing without importing collections.abc Sequence name collision in older stubs.
SequenceLike = Any


_DECISION_KEYS = frozenset(
    {
        "objective_id",
        "tenant_id",
        "workhub_task_id",
        "objective",
        "task_class",
        "definition_of_done",
        "repository_id",
        "allowed_files",
        "project_id",
        "task_number",
        "version",
        "created_at",
        "cycle_id",
        "run_id",
        "candidate_sha",
        "budgets",
        "phase",
        "terminal_reason",
        "last_error_detail",
        "approval_reference",
        "plan_id",
        "plan_revision",
        "approval_gate_id",
        "current_gate_id",
        "approved_gate_id",
        "approved_plan_revision",
        "approved_operations",
        "approved_resources",
        "event_sequence",
        "trace_id",
        "retry_budget_remaining",
        "replan_budget_remaining",
        "cancel_requested",
        "current_step_cursor",
        "dispatch_state",
        "dispatch_required",
        "execution_claim_id",
        "execution_claim_step_id",
        "execution_claim_attempt",
        "execution_claim_expired",
        "active_step_started_at",
        "step_lease_expires_at",
        "last_progress_at",
        "previous_step_id",
    }
)


def _event_seq(raw: Any) -> int:
    if isinstance(raw, Mapping):
        for key in ("sequence_number", "sequence", "seq"):
            if key in raw and raw[key] is not None:
                return int(raw[key])
        return int(getattr(raw, "sequence_number", 0) or 0)
    return int(getattr(raw, "sequence_number", 0) or 0)


def _event_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        payload = raw.get("payload")
        if isinstance(payload, Mapping):
            return dict(payload)
        # Treat the mapping itself as payload when no nested payload.
        return {k: v for k, v in raw.items() if k not in {"sequence_number", "sequence", "seq", "event_type", "id"}}
    payload = getattr(raw, "payload", None)
    return dict(payload) if isinstance(payload, Mapping) else {}


def apply_snapshot_to_objective_fields(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    env = validate_snapshot_envelope(snapshot)
    if env["kind"] != "objective":
        raise ReplayError("INVALID_SNAPSHOT", "expected objective kind")
    return decision_state_objective(env["decision"])


def apply_snapshot_to_run_fields(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    env = validate_snapshot_envelope(snapshot)
    if env["kind"] != "run":
        raise ReplayError("INVALID_SNAPSHOT", "expected run kind")
    return decision_state_run(env["decision"])


def reconstruct_decision_pair(
    *,
    objective_snap: AggregateSnapshot | None,
    run_snap: AggregateSnapshot | None,
    tenant_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Load validated decision states; reject cross-tenant."""
    obj_dec: dict[str, Any] | None = None
    run_dec: dict[str, Any] | None = None
    if objective_snap is not None:
        if objective_snap.tenant_id != tenant_id:
            raise ReplayError("CROSS_TENANT_SNAPSHOT", objective_snap.aggregate_id)
        obj_dec = apply_snapshot_to_objective_fields(objective_snap.snapshot)
    if run_snap is not None:
        if run_snap.tenant_id != tenant_id:
            raise ReplayError("CROSS_TENANT_SNAPSHOT", run_snap.aggregate_id)
        run_dec = apply_snapshot_to_run_fields(run_snap.snapshot)
    return obj_dec, run_dec


__all__ = [
    "DECISION_STATE_SCHEMA",
    "REDUCER_INVARIANTS",
    "ReplayError",
    "AggregateSnapshot",
    "ProjectionStore",
    "InMemoryProjectionStore",
    "JsonFileProjectionStore",
    "decision_state_objective",
    "decision_state_run",
    "decision_bytes",
    "wrap_objective_snapshot",
    "wrap_run_snapshot",
    "validate_snapshot_envelope",
    "fold_events",
    "apply_snapshot_to_objective_fields",
    "apply_snapshot_to_run_fields",
    "reconstruct_decision_pair",
]
