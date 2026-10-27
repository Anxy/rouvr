"""AIX-W08-E02-T003: Incremental Replanning Protocol (Packet 05 Gap -> Planner).

Mutates a Plan DAG when invalid assumptions or gaps occur during execution:
- Supersedes ONLY dependent future steps.
- Preserves all unaffected verified steps.
- Inserts new replacement repair steps seamlessly into the DAG.
- Consumes GapReport / GapV1 for bounded, localized repair planning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.aix_core.runtime.objective.contracts import (
    AgentRole,
    BudgetV1,
    RequiredEffectV1,
    StepContractV1,
)


@dataclass(frozen=True)
class GapReport:
    gap_id: str
    objective_id: str
    category: str
    description: str
    expected: str
    observed: str
    evidence_locations: tuple[str, ...] = ()
    failed_step_id: str | None = None
    target_scope: tuple[str, ...] = ()
    suggested_fix: str = ""


@dataclass(frozen=True)
class IncrementalReplanResult:
    superseded_step_ids: tuple[str, ...]
    preserved_verified_step_ids: tuple[str, ...]
    inserted_step_ids: tuple[str, ...]
    updated_steps: tuple[StepContractV1, ...]


def find_transitive_dependents(
    step_id: str,
    steps: tuple[StepContractV1, ...] | list[StepContractV1],
) -> set[str]:
    """Finds all steps in the DAG that directly or indirectly depend on step_id."""
    dependents: set[str] = set()
    queue = [step_id]

    while queue:
        curr = queue.pop(0)
        for step in steps:
            if curr in step.depends_on and step.id not in dependents:
                dependents.add(step.id)
                queue.append(step.id)

    return dependents


def build_repair_step_from_gap(
    *,
    gap: Any,
    step_id: str,
    step_index: int = 1,
    cycle_id: str = "cyc_repair",
    tenant_id: str = "tenant_repair",
    depends_on: tuple[str, ...] = (),
    fallback_scope: tuple[str, ...] = (),
) -> StepContractV1:
    """Generate a targeted bounded repair step for a detected gap."""
    ov_id = getattr(gap, "ov_id", "") or ""
    expected = str(getattr(gap, "expected", "") or "")
    observed = str(getattr(gap, "observed", "") or "")
    gap_scope = tuple(
        str(p).strip().replace("\\", "/")
        for p in (getattr(gap, "target_scope", None) or ())
        if str(p or "").strip()
    )
    evidence_locs = tuple(
        str(p).strip().replace("\\", "/")
        for p in (getattr(gap, "evidence_locations", None) or ())
        if str(p or "").strip() and not str(p).endswith("/")
    )
    fallback = tuple(
        str(p).strip().replace("\\", "/")
        for p in (fallback_scope or ())
        if str(p or "").strip() and not str(p).endswith("/")
    )
    target_scope = gap_scope or evidence_locs or fallback
    if not target_scope:
        target_scope = ("backend/aix_core/runtime/objective/durable_api_backend.py",)
    validation_commands: tuple[str, ...] = (
        "python -m pytest backend/tests/runtime/objective_cycle/test_phase1_terminalization_delegation.py -q",
    )
    purpose = f"Repair defect {ov_id or 'gap'}: {expected}"[:240]

    return StepContractV1(
        id=step_id,
        cycle_id=cycle_id,
        tenant_id=tenant_id,
        step_index=step_index,
        purpose=purpose,
        target_scope=target_scope,
        acceptance_criteria=(
            f"Fix defect: {expected}"[:240],
            f"Resolve observed error: {observed[:80]}",
        ),
        validation_commands=validation_commands,
        expected_output_schema_ref="schema://repair_step",
        assigned_role="executor",
        required_effects=(RequiredEffectV1(kind="LITERAL_REPLACE"),),
        step_budget=BudgetV1(
            time_seconds=300,
            tokens=50000,
            cost_usd=1.0,
            retries=2,
            steps=1,
            concurrency=1,
        ),
        depends_on=depends_on,
        status="PENDING",
    )


def perform_incremental_replan(
    *,
    failed_step_id: str | None = None,
    existing_steps: tuple[StepContractV1, ...],
    replacement_steps: tuple[StepContractV1, ...],
) -> IncrementalReplanResult:
    """Supersedes dependent future steps of failed_step_id while preserving verified work."""
    superseded_ids: set[str] = set()

    if failed_step_id is not None:
        step_map = {s.id: s for s in existing_steps}
        if failed_step_id in step_map:
            dependent_ids = find_transitive_dependents(failed_step_id, existing_steps)
            superseded_ids = {failed_step_id} | dependent_ids

    preserved_ids: list[str] = []
    updated_steps_list: list[StepContractV1] = []

    for s in existing_steps:
        if s.id in superseded_ids:
            superseded_copy = s.model_copy(update={"status": "FAILED"})
            updated_steps_list.append(superseded_copy)
        else:
            if s.status == "VERIFIED":
                preserved_ids.append(s.id)
            updated_steps_list.append(s)

    # Append replacement steps
    inserted_ids = [s.id for s in replacement_steps]
    updated_steps_list.extend(replacement_steps)

    return IncrementalReplanResult(
        superseded_step_ids=tuple(sorted(superseded_ids)),
        preserved_verified_step_ids=tuple(sorted(preserved_ids)),
        inserted_step_ids=tuple(inserted_ids),
        updated_steps=tuple(updated_steps_list),
    )


__all__ = [
    "GapReport",
    "IncrementalReplanResult",
    "find_transitive_dependents",
    "build_repair_step_from_gap",
    "perform_incremental_replan",
]
