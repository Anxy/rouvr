"""AIX-MPE-003 Packet MPE-05: deterministic step scheduler.

The LLM does not choose the next legal step. This module does.
"""
from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import (
    ObjectiveObligationV1,
    PlanRevisionV1,
    StepContractV1,
    StepRuntimeStateV1,
)

_NOT_READY = frozenset({"PASS", "RUNNING", "VERIFYING", "FAILED", "BLOCKED", "SUPERSEDED"})


def get_ready_steps(
    plan: PlanRevisionV1,
    runtime: dict[str, StepRuntimeStateV1],
) -> tuple[StepContractV1, ...]:
    passed = {sid for sid, st in runtime.items() if st.status == "PASS"}
    ready: list[StepContractV1] = []
    for step in sorted(plan.steps, key=lambda s: s.step_index):
        st = runtime.get(step.id)
        if st is not None and st.status in _NOT_READY:
            continue
        if any(dep not in passed for dep in step.depends_on):
            continue
        ready.append(step)
    return tuple(ready)


def select_next_step(
    plan: PlanRevisionV1,
    runtime: dict[str, StepRuntimeStateV1],
) -> StepContractV1 | None:
    ready = get_ready_steps(plan, runtime)
    return ready[0] if ready else None


def select_ready_batch(
    plan: PlanRevisionV1,
    runtime: dict[str, StepRuntimeStateV1],
    *,
    max_batch: int = 8,
) -> tuple[StepContractV1, ...]:
    """F11: bounded ready wave from the same authority as ``select_next_step``."""
    if max_batch < 1:
        return ()
    return get_ready_steps(plan, runtime)[:max_batch]


def all_required_steps_passed(
    plan: PlanRevisionV1,
    runtime: dict[str, StepRuntimeStateV1],
) -> bool:
    return all(runtime.get(step.id) is not None and runtime[step.id].status == "PASS" for step in plan.steps)


def remaining_required_obligation_ids(
    *,
    plan: PlanRevisionV1,
    runtime: dict[str, StepRuntimeStateV1],
    obligations: tuple[ObjectiveObligationV1, ...],
) -> tuple[str, ...]:
    """Required obligations not yet covered by a PASSed step. Passing every
    currently scheduled step is not completion if one of these remains."""
    passed = {sid for sid, st in runtime.items() if st.status == "PASS"}
    satisfied: set[str] = set()
    for step in plan.steps:
        if step.id in passed:
            satisfied.update(step.obligation_refs)
    return tuple(o.id for o in obligations if o.required and o.id not in satisfied)


def validate_dag(plan: PlanRevisionV1) -> None:
    """Validates that the plan's step dependency graph contains no cycles and all declared dependencies exist."""
    step_ids = {s.id for s in plan.steps}
    for step in plan.steps:
        for dep in step.depends_on:
            if dep not in step_ids:
                raise ValueError(f"step {step.id!r} depends on non-existent step {dep!r}")

    adj: dict[str, list[str]] = {s.id: list(s.depends_on) for s in plan.steps}
    visited: set[str] = set()
    in_stack: set[str] = set()

    def _dfs(node: str) -> None:
        visited.add(node)
        in_stack.add(node)
        for dep in adj.get(node, []):
            if dep not in visited:
                _dfs(dep)
            elif dep in in_stack:
                raise ValueError(f"cycle detected involving step {dep!r}")
        in_stack.remove(node)

    for step_id in adj:
        if step_id not in visited:
            _dfs(step_id)


__all__ = [
    "get_ready_steps",
    "select_next_step",
    "select_ready_batch",
    "all_required_steps_passed",
    "remaining_required_obligation_ids",
    "validate_dag",
]
