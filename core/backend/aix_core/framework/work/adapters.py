"""F1-P3/P4: Bidirectional adapters between Framework Work and runtime contracts.

Runtime SoR remains authoritative. These adapters never mutate ledger rows.
"""

from __future__ import annotations

from typing import Any

from backend.aix_core.framework.work.models import (
    ExternalWorkRef,
    UnitStatus,
    Work,
    WorkContract,
    WorkDefinition,
    WorkGraph,
    WorkStatus,
    WorkType,
    WorkUnit,
)
from backend.aix_core.framework.work.worktype import WorkTypeTaxonomy
from backend.aix_core.runtime.objective.contracts import (
    ObjectiveEnvelopeV1,
    PlanRevisionV1,
    StepContractV1,
)
from backend.aix_core.runtime.objective.outcome_contract import OutcomeContractV1


def _risk_tier_str(value: Any) -> str:
    raw = getattr(value, "value", value)
    text = str(raw or "LOW").strip().upper()
    if text in {"LOW", "MEDIUM", "HIGH", "CRITICAL", "PROHIBITED"}:
        return text
    return "LOW"


def _unit_status_from_step(status: Any) -> UnitStatus:
    text = str(getattr(status, "value", status) or "PENDING").strip().upper()
    mapping = {
        "PENDING": UnitStatus.PENDING,
        "READY": UnitStatus.READY,
        "CLAIMED": UnitStatus.RUNNING,
        "RUNNING": UnitStatus.RUNNING,
        "SUCCEEDED": UnitStatus.SUCCEEDED,
        "FAILED": UnitStatus.FAILED,
        "SKIPPED": UnitStatus.SKIPPED,
        "CANCELLED": UnitStatus.FAILED,
    }
    return mapping.get(text, UnitStatus.PENDING)


class WorkDefinitionAdapter:
    """F1-P3: OutcomeContractV1 / envelope → WorkDefinition."""

    @staticmethod
    def from_outcome_contract(
        outcome: OutcomeContractV1,
        *,
        work_id: str | None = None,
        acceptance_criteria: tuple[str, ...] | None = None,
    ) -> WorkDefinition:
        dod_parts = tuple(str(x).strip() for x in (outcome.definition_of_done or ()) if str(x).strip())
        dod = "; ".join(dod_parts) if dod_parts else "outcome contract"
        criteria = acceptance_criteria or dod_parts or (dod,)
        facet_keys = tuple(
            name
            for name in ("UI", "API", "DB", "AUTH", "INTEGRATION")
            if getattr(outcome.facets, name, None) is not None
        )
        work_def = WorkDefinition(
            definition_id=str(outcome.contract_id),
            work_id=str(work_id or outcome.objective_id),
            definition_of_done=dod,
            acceptance_criteria=criteria,
            obligations=facet_keys,
            target_scope=tuple(str(p) for p in (outcome.scope or ()) if str(p).strip()),
            constraints=tuple(str(c) for c in (outcome.constraints or ()) if str(c).strip()),
            facet_refs=facet_keys,
            source_contract_hash=str(outcome.contract_hash or "") or None,
        )
        return work_def.model_copy(update={"definition_hash": work_def.compute_hash()})

    @staticmethod
    def from_envelope(envelope: ObjectiveEnvelopeV1) -> WorkDefinition:
        work_def = WorkDefinition(
            work_id=envelope.id,
            definition_of_done=envelope.definition_of_done,
            acceptance_criteria=tuple(envelope.acceptance_criteria),
            target_scope=tuple(envelope.repository_scope),
        )
        return work_def.model_copy(update={"definition_hash": work_def.compute_hash()})


class WorkContractAdapter:
    """F1-P4: StepContractV1 → WorkContract (preserves PD-003B2 refs)."""

    @staticmethod
    def from_step_contract(step: StepContractV1) -> WorkContract:
        effects = tuple(
            e.model_dump(mode="json") if hasattr(e, "model_dump") else dict(e)
            for e in (step.required_effects or ())
        )
        budget = step.step_budget
        return WorkContract(
            unit_id=step.id,
            required_effects=effects,
            obligation_refs=tuple(step.obligation_refs or ()),
            validation_commands=tuple(step.validation_commands or ()),
            expected_checks=tuple(step.expected_checks or ()),
            assigned_role=str(step.assigned_role),
            risk_tier=_risk_tier_str(step.risk_tier),  # type: ignore[arg-type]
            approval_required=bool(step.approval_required),
            timeout_seconds=int(getattr(budget, "time_seconds", 300) or 300),
            token_budget=int(getattr(budget, "tokens", 30000) or 30000),
            outcome_contract_id=step.outcome_contract_id,
            outcome_contract_hash=step.outcome_contract_hash,
            facet_refs=tuple(step.facet_refs or ()),
            operation_refs=tuple(step.operation_refs or ()),
            entity_refs=tuple(step.entity_refs or ()),
            mapping_refs=tuple(step.mapping_refs or ()),
            binding_role=step.binding_role,
        )


class WorkUnitAdapter:
    """F1-P4: StepContractV1 → WorkUnit."""

    @staticmethod
    def from_step_contract(step: StepContractV1, *, work_id: str | None = None) -> WorkUnit:
        contract = WorkContractAdapter.from_step_contract(step)
        actions = tuple(
            a.model_dump(mode="json") if hasattr(a, "model_dump") else dict(a)
            for a in (step.actions or ())
        )
        return WorkUnit(
            unit_id=step.id,
            work_id=str(work_id or step.cycle_id),
            unit_index=int(step.step_index),
            purpose=step.purpose,
            depends_on=tuple(step.depends_on or ()),
            target_scope=tuple(step.target_scope),
            contract=contract,
            actions=actions,
            status=_unit_status_from_step(step.status),
            created_at=str(step.created_at),
        )


class WorkGraphAdapter:
    """F1-P4: PlanRevisionV1 → WorkGraph (dependency DAG parity)."""

    @staticmethod
    def from_plan(plan: PlanRevisionV1) -> WorkGraph:
        units = tuple(
            WorkUnitAdapter.from_step_contract(step, work_id=plan.objective_id)
            for step in plan.steps
        )
        return WorkGraph(
            graph_id=plan.plan_id,
            work_id=plan.objective_id,
            revision=int(plan.revision),
            units=units,
            created_from_revision=plan.created_from_plan_revision,
        )

    @staticmethod
    def dependency_edges_match_plan(plan: PlanRevisionV1, graph: WorkGraph) -> bool:
        plan_edges = {
            (step.id, dep)
            for step in plan.steps
            for dep in (step.depends_on or ())
        }
        return set(graph.dependency_edges()) == plan_edges


class WorkDomainAdapter:
    """Envelope / plan aggregate → Work (F1-AC-01 round-trip surface)."""

    @staticmethod
    def work_from_envelope(
        envelope: ObjectiveEnvelopeV1,
        outcome: OutcomeContractV1 | None = None,
        *,
        plan: PlanRevisionV1 | None = None,
        status: WorkStatus = WorkStatus.ADMITTED,
    ) -> Work:
        work_type = WorkType(
            task_class=envelope.task_class,
            task_family=WorkTypeTaxonomy.from_task_class(envelope.task_class).task_family,
            allowed_effects=tuple(envelope.allowed_effects),
            forbidden_effects=tuple(envelope.forbidden_effects),
            environment_scope=envelope.environment_scope,
            approval_policy=str(envelope.approval_policy),
        )
        if outcome is not None:
            work_def = WorkDefinitionAdapter.from_outcome_contract(
                outcome,
                work_id=envelope.id,
                acceptance_criteria=tuple(envelope.acceptance_criteria),
            )
        else:
            work_def = WorkDefinitionAdapter.from_envelope(envelope)

        external_ref = ExternalWorkRef(
            system="workhub",
            external_task_id=envelope.workhub_task_ref,
            project_id=envelope.project_id,
            correlation_id=envelope.correlation_id,
        )
        graph = WorkGraphAdapter.from_plan(plan) if plan is not None else None
        return Work(
            work_id=envelope.id,
            tenant_id=envelope.tenant_id,
            project_id=envelope.project_id,
            actor_ref=envelope.actor_ref,
            work_type=work_type,
            definition=work_def,
            graph=graph,
            external_ref=external_ref,
            status=status,
            created_at=envelope.created_at,
        )

    from_objective = work_from_envelope
    work_unit_from_step = staticmethod(WorkUnitAdapter.from_step_contract)
    work_graph_from_plan = staticmethod(WorkGraphAdapter.from_plan)


WorkDefinition.from_outcome_contract = staticmethod(  # type: ignore[attr-defined]
    WorkDefinitionAdapter.from_outcome_contract
)
WorkUnit.from_step_contract = staticmethod(WorkUnitAdapter.from_step_contract)  # type: ignore[attr-defined]
WorkContract.from_step_contract = staticmethod(  # type: ignore[attr-defined]
    WorkContractAdapter.from_step_contract
)
Work.from_objective = staticmethod(WorkDomainAdapter.work_from_envelope)  # type: ignore[attr-defined]


__all__ = [
    "WorkDefinitionAdapter",
    "WorkContractAdapter",
    "WorkUnitAdapter",
    "WorkGraphAdapter",
    "WorkDomainAdapter",
]
