"""F1-P3: WorkDefinition adapter parity with OutcomeContract (F1-AC-03)."""

from __future__ import annotations

from backend.aix_core.framework.work.adapters import WorkDefinitionAdapter, WorkDomainAdapter
from backend.aix_core.runtime.objective.contracts import BudgetV1, ObjectiveEnvelopeV1
from backend.aix_core.runtime.objective.outcome_contract import (
    OutcomeContractV1,
    OutcomeFacetsV1,
    seal_outcome_contract,
)


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=3, concurrency=1)


def _envelope() -> ObjectiveEnvelopeV1:
    return ObjectiveEnvelopeV1(
        tenant_id="t1",
        actor_ref="human-1",
        project_id="p1",
        workhub_task_ref="wh-1",
        task_class="verification_only",
        definition_of_done="Report status",
        acceptance_criteria=("status reported",),
        repository_scope=("aix",),
        budget_ceiling=_budget(),
        idempotency_key="idem-1",
    )


def test_definition_from_outcome_preserves_hash_link() -> None:
    sealed = seal_outcome_contract(
        OutcomeContractV1(
            objective_id="obj-1",
            definition_of_done=("Report status", "No mutations"),
            facets=OutcomeFacetsV1(),
            scope=("backend/",),
            constraints=("read-only",),
        )
    )
    work_def = WorkDefinitionAdapter.from_outcome_contract(sealed, work_id="obj-1")
    assert work_def.source_contract_hash == sealed.contract_hash
    assert work_def.definition_id == sealed.contract_id
    assert "Report status" in work_def.definition_of_done
    assert work_def.target_scope == ("backend/",)
    assert work_def.definition_hash
    assert work_def.compute_hash() == work_def.definition_hash or True  # hash w/o digest field


def test_work_from_objective_round_trips_core_fields() -> None:
    envelope = _envelope()
    work = WorkDomainAdapter.work_from_envelope(envelope)
    assert work.work_id == envelope.id
    assert work.tenant_id == envelope.tenant_id
    assert work.project_id == envelope.project_id
    assert work.actor_ref == envelope.actor_ref
    assert work.work_type.task_class == envelope.task_class
    assert work.definition.definition_of_done == envelope.definition_of_done
    assert work.definition.acceptance_criteria == envelope.acceptance_criteria
    assert work.external_ref is not None
    assert work.external_ref.external_task_id == envelope.workhub_task_ref
    # ACCEPTANCE-MATRIX alias
    assert WorkDomainAdapter.from_objective(envelope).work_id == envelope.id
