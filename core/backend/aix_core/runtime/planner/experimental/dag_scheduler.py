"""C043: DAG ready-set, effect conflict analysis, parallel vs serialize dispatch."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from backend.aix_core.runtime.objective.contracts import PlanRevisionV1, StepContractV1

DispatchMode = Literal["PARALLEL", "SERIALIZE"]


class DagSchedulerError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class EffectLease:
    resource: str
    holder_step_id: str
    cycle_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EffectLeaseTable:
    """In-memory durable-shaped lease table for overlapping effect serialization."""

    _leases: dict[str, EffectLease] = field(default_factory=dict)

    def try_acquire(self, *, resource: str, step_id: str, cycle_id: str) -> EffectLease | None:
        current = self._leases.get(resource)
        if current is not None and current.holder_step_id != step_id:
            return None
        lease = EffectLease(resource=resource, holder_step_id=step_id, cycle_id=cycle_id)
        self._leases[resource] = lease
        return lease

    def release(self, *, resource: str, step_id: str) -> None:
        current = self._leases.get(resource)
        if current and current.holder_step_id == step_id:
            del self._leases[resource]

    def holder(self, resource: str) -> str | None:
        lease = self._leases.get(resource)
        return lease.holder_step_id if lease else None


@dataclass(frozen=True)
class ReadySet:
    ready_step_ids: tuple[str, ...]
    blocked_step_ids: tuple[str, ...]
    serialize_pairs: tuple[tuple[str, str], ...]  # (a,b) must not run concurrently
    dispatch_mode_by_step: dict[str, DispatchMode]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready_step_ids": list(self.ready_step_ids),
            "blocked_step_ids": list(self.blocked_step_ids),
            "serialize_pairs": [list(p) for p in self.serialize_pairs],
            "dispatch_mode_by_step": dict(self.dispatch_mode_by_step),
        }


def _effects_of(step: StepContractV1) -> set[str]:
    return set(step.target_scope)


def overlapping_effect_pairs(plan: PlanRevisionV1) -> tuple[tuple[str, str], ...]:
    steps = list(plan.steps)
    pairs: list[tuple[str, str]] = []
    for i, a in enumerate(steps):
        ea = _effects_of(a)
        for b in steps[i + 1 :]:
            if ea & _effects_of(b):
                pairs.append((a.id, b.id))
    return tuple(pairs)


def compute_ready_set(
    *,
    plan: PlanRevisionV1,
    completed_step_ids: set[str] | frozenset[str],
    in_flight_step_ids: set[str] | frozenset[str] | None = None,
) -> ReadySet:
    """READY = deps satisfied and not completed; overlapping effects serialize."""
    in_flight = set(in_flight_step_ids or ())
    by_id = {s.id: s for s in plan.steps}
    if len(by_id) != len(plan.steps):
        raise DagSchedulerError("DUPLICATE_STEP_ID")

    serialize_pairs = overlapping_effect_pairs(plan)
    serialize_neighbors: dict[str, set[str]] = {sid: set() for sid in by_id}
    for a, b in serialize_pairs:
        serialize_neighbors[a].add(b)
        serialize_neighbors[b].add(a)

    ready: list[str] = []
    blocked: list[str] = []
    modes: dict[str, DispatchMode] = {}

    for step in plan.steps:
        if step.id in completed_step_ids or step.id in in_flight:
            continue
        deps_ok = all(dep in completed_step_ids for dep in step.depends_on)
        if not deps_ok:
            blocked.append(step.id)
            modes[step.id] = "SERIALIZE"
            continue
        # If any overlapping neighbor is in-flight, this step must wait (serialize)
        conflict_inflight = serialize_neighbors[step.id] & in_flight
        if conflict_inflight:
            blocked.append(step.id)
            modes[step.id] = "SERIALIZE"
            continue
        ready.append(step.id)
        modes[step.id] = "SERIALIZE" if serialize_neighbors[step.id] else "PARALLEL"

    # Among ready steps that overlap each other, only the first in plan order stays PARALLEL-eligible;
    # others are marked SERIALIZE relative to the cohort (caller dispatches one-at-a-time for those).
    ready_set = set(ready)
    for a, b in serialize_pairs:
        if a in ready_set and b in ready_set:
            modes[a] = "SERIALIZE"
            modes[b] = "SERIALIZE"

    return ReadySet(
        ready_step_ids=tuple(ready),
        blocked_step_ids=tuple(blocked),
        serialize_pairs=serialize_pairs,
        dispatch_mode_by_step=modes,
    )


def parallel_dispatch_batch(ready: ReadySet) -> tuple[str, ...]:
    """Steps that may run concurrently: READY and PARALLEL mode only."""
    return tuple(
        sid for sid in ready.ready_step_ids if ready.dispatch_mode_by_step.get(sid) == "PARALLEL"
    )


def serialize_dispatch_batch(ready: ReadySet) -> tuple[str, ...]:
    """Overlapping-effect READY steps — at most one should be started by the caller."""
    return tuple(
        sid for sid in ready.ready_step_ids if ready.dispatch_mode_by_step.get(sid) == "SERIALIZE"
    )
