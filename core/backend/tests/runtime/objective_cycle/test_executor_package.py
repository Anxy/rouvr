"""AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P7: Step Context & Execution Package
Hardening. Unit tests for `build_executor_package()` -- pure bundling, no backend.
"""
from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    ObjectiveEnvelopeV1,
    StepContextCapsuleV1,
    StepContractV1,
)
from backend.aix_core.runtime.executor.executor_package import build_executor_package


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1)


def _envelope() -> ObjectiveEnvelopeV1:
    return ObjectiveEnvelopeV1(
        tenant_id="t", actor_ref="human-1", project_id="proj-1", workhub_task_ref="wh-1",
        task_class="docs_only_change", definition_of_done="x", acceptance_criteria=("x",),
        repository_scope=("repo-1",), budget_ceiling=_budget(), idempotency_key="idem-1",
    )


def _step(obligation_refs: tuple[str, ...] = ()) -> StepContractV1:
    return StepContractV1(
        cycle_id="cyc-1", tenant_id="t", step_index=0, purpose="x", target_scope=("a.php",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit", acceptance_criteria=("x",),
        validation_commands=("x",), assigned_role="executor", step_budget=_budget(),
        obligation_refs=obligation_refs, status="PENDING",
    )


def test_package_bundles_envelope_and_step_contract_boundaries() -> None:
    envelope = _envelope()
    step = _step(obligation_refs=("ob-1", "ob-2"))
    package = build_executor_package(
        cycle_id="cyc-1", objective_id="obj-1", objective="do the thing",
        envelope=envelope, step_contract=step, context_capsule=None, repair_context=None,
    )
    assert package.objective_boundary == "do the thing"
    assert package.acceptance_criteria == envelope.acceptance_criteria
    assert package.forbidden_effects == envelope.forbidden_effects
    assert package.step_contract_id == step.id
    assert package.obligation_ids == ("ob-1", "ob-2")


def test_package_with_no_envelope_or_step_contract_still_records_the_boundary() -> None:
    package = build_executor_package(
        cycle_id="cyc-1", objective_id="obj-1", objective="do the thing",
        envelope=None, step_contract=None, context_capsule=None, repair_context=None,
    )
    assert package.objective_boundary == "do the thing"
    assert package.acceptance_criteria == ()
    assert package.step_contract_id is None
    assert package.obligation_ids == ()


def test_package_records_context_capsule_and_repair_context() -> None:
    capsule = StepContextCapsuleV1(step_id="step-1", relevant_previous_failure="prior REPAIR: x failed")
    package = build_executor_package(
        cycle_id="cyc-1", objective_id="obj-1", objective="do the thing",
        envelope=None, step_contract=None, context_capsule=capsule,
        repair_context="prior REPAIR: x failed",
    )
    assert package.context_capsule_id == capsule.id
    assert package.repair_context == "prior REPAIR: x failed"
