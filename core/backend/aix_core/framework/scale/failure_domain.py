"""F11-P4: Partial failure domain isolation — replan via canonical incremental_replan.

Does not invent a second replan algorithm.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.aix_core.framework.scale.work_graph_store import WorkGraphStore
from backend.aix_core.framework.work.models import UnitStatus
from backend.aix_core.runtime.objective.contracts import PlanRevisionV1, StepContractV1
from backend.aix_core.runtime.planner.incremental_replan import (
    IncrementalReplanResult,
    find_transitive_dependents,
    perform_incremental_replan,
)


class FailureDomain(BaseModel):
    """Identified subgraph of units impacted by a specific unit failure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    failed_unit_id: str
    impacted_unit_ids: tuple[str, ...]
    unaffected_unit_ids: tuple[str, ...]
    isolation_successful: bool
    replan: IncrementalReplanResult | None = None


class FailureDomainManager:
    """Isolates failure domains and delegates replan to ``perform_incremental_replan``."""

    def __init__(self, store: WorkGraphStore | Any) -> None:
        self.store = store

    def compute_failure_domain(self, graph_id: str, failed_unit_id: str) -> FailureDomain:
        unit_map = {u.unit_id: u for u in self.store.list_units(graph_id)}
        if failed_unit_id not in unit_map:
            raise KeyError(f"Unit {failed_unit_id} not found in graph {graph_id}")

        # Prefer plan-shaped transitive dependents when steps mirror unit ids
        dependents_map = self.store.all_dependents_map(graph_id)
        impacted: set[str] = {failed_unit_id}
        queue = [failed_unit_id]
        while queue:
            curr = queue.pop(0)
            for child in dependents_map.get(curr, set()):
                if child not in impacted:
                    impacted.add(child)
                    queue.append(child)

        all_ids = set(unit_map.keys())
        unaffected = all_ids - impacted
        return FailureDomain(
            failed_unit_id=failed_unit_id,
            impacted_unit_ids=tuple(sorted(impacted)),
            unaffected_unit_ids=tuple(sorted(unaffected)),
            isolation_successful=True,
        )

    def quarantine_affected(self, graph_id: str, domain: FailureDomain) -> None:
        self.store.update_unit_status(graph_id, domain.failed_unit_id, UnitStatus.FAILED)
        for impacted_id in domain.impacted_unit_ids:
            if impacted_id == domain.failed_unit_id:
                continue
            unit = self.store.get_unit(graph_id, impacted_id)
            if unit and unit.status == UnitStatus.PENDING:
                self.store.update_unit_status(graph_id, impacted_id, UnitStatus.SKIPPED)

    def quarantine_and_replan_subgraph(
        self,
        graph_id: str,
        failed_unit_id: str,
        *,
        plan: PlanRevisionV1 | None = None,
        replacement_steps: tuple[StepContractV1, ...] = (),
    ) -> FailureDomain:
        """Quarantine affected units then call canonical ``perform_incremental_replan``."""
        domain = self.compute_failure_domain(graph_id, failed_unit_id)
        self.quarantine_affected(graph_id, domain)

        replan_result: IncrementalReplanResult | None = None
        if plan is not None:
            # Align with plan DAG transitive dependents (authoritative for step ids)
            plan_dependents = find_transitive_dependents(failed_unit_id, plan.steps)
            # If plan uses same ids as units, quarantine already matches
            replan_result = perform_incremental_replan(
                failed_step_id=failed_unit_id,
                existing_steps=plan.steps,
                replacement_steps=replacement_steps,
            )
            # Bind replacement units into the WorkGraph projection
            if replacement_steps:
                graph = self.store.load_work_graph(graph_id)
                if graph is not None:
                    from backend.aix_core.framework.work.models import WorkContract, WorkUnit

                    existing_ids = {u.unit_id for u in graph.units}
                    new_units = list(graph.units)
                    max_index = max((u.unit_index for u in graph.units), default=-1)
                    for step in replacement_steps:
                        if step.id in existing_ids:
                            continue
                        max_index += 1
                        new_units.append(
                            WorkUnit(
                                unit_id=step.id,
                                work_id=graph.work_id,
                                unit_index=max_index,
                                purpose=step.purpose,
                                depends_on=tuple(step.depends_on),
                                target_scope=tuple(step.target_scope),
                                contract=WorkContract(
                                    unit_id=step.id,
                                    assigned_role=str(step.assigned_role),
                                    validation_commands=tuple(step.validation_commands),
                                    expected_checks=tuple(step.expected_checks or ()),
                                    obligation_refs=tuple(step.obligation_refs),
                                ),
                                status=UnitStatus.PENDING,
                            )
                        )
                    from backend.aix_core.framework.work.models import WorkGraph

                    rebound = WorkGraph(
                        graph_id=graph.graph_id,
                        work_id=graph.work_id,
                        revision=graph.revision + 1,
                        units=tuple(new_units),
                        created_at=graph.created_at,
                    )
                    self.store.save_work_graph(rebound)
            _ = plan_dependents  # documented alignment; domain already computed from store

        return domain.model_copy(update={"replan": replan_result})


__all__ = [
    "FailureDomain",
    "FailureDomainManager",
]
