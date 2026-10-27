"""AIX Framework Compiler - Stage 6: Invariant & Continuity Verification.

Sprint: F10 Work Compiler (F10-P1)
Grounded in: flow_coherence.py (PD-003D), coverage_alignment.py (PD-003E), step_acceptance_continuity.py (SEM-001-W3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.framework.compiler.stages.bind import BoundPlan
from backend.aix_core.framework.compiler.stages.define import DefinedOutcome
from backend.aix_core.framework.compiler.stages.structure import ProductStructureDecomposition
from backend.aix_core.runtime.objective.contracts import BudgetV1, PlanRevisionV1, StepContractV1
from backend.aix_core.runtime.objective.flow_coherence import validate_product_structure_flow_coherence
from backend.aix_core.runtime.objective.product_structure_plan import TargetTrace
from backend.aix_core.runtime.verification.coverage_alignment import (
    validate_product_structure_coverage_alignment,
)


class CompilerVerificationError(RuntimeError):
    """Raised when compilation fails any invariant or verification gate."""


class VerifiedPlan(BaseModel):
    """Verified plan revision passing all structural, flow coherence, and coverage gates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_revision: PlanRevisionV1
    flow_coherence_verified: bool
    coverage_alignment_verified: bool


class ValidateStage:
    """Stage 6: Verifies flow coherence and coverage alignment fail-closed."""

    @classmethod
    def run(
        cls,
        bound_plan: BoundPlan,
        traces: tuple[TargetTrace, ...],
        structure_decomp: ProductStructureDecomposition,
        outcome: DefinedOutcome,
    ) -> VerifiedPlan:
        contract = outcome.outcome_contract
        structure = structure_decomp.product_structure

        # 1. Flow Coherence Check (PD-003D)
        flow_result = validate_product_structure_flow_coherence(
            contract=contract,
            structure=structure,
            bindings=bound_plan.bindings,
            step_scopes=bound_plan.step_scopes,
        )
        if not flow_result.ok:
            raise CompilerVerificationError(f"PD-003D Flow Coherence validation failed: {flow_result.code} - {flow_result.detail}")

        # 2. Coverage Alignment Check (PD-003E)
        cov_result = validate_product_structure_coverage_alignment(
            contract=contract,
            structure=structure,
            bindings=bound_plan.bindings,
        )
        if not cov_result.ok:
            raise CompilerVerificationError(f"PD-003E Coverage Alignment validation failed: {cov_result.code} - {cov_result.detail}")

        now_str = datetime.now(UTC).isoformat()
        default_step_budget = BudgetV1(
            time_seconds=60,
            tokens=4000,
            cost_usd=0.10,
            retries=2,
            steps=1,
            concurrency=1,
        )

        # 3. Build canonical step contracts
        step_contracts = []
        for idx, (scope, binding) in enumerate(zip(bound_plan.step_scopes, bound_plan.bindings)):
            step_id = f"step_{idx+1}"
            step_contracts.append(
                StepContractV1(
                    id=step_id,
                    cycle_id=f"cycle_{contract.objective_id}",
                    tenant_id="tenant-default",
                    step_index=idx + 1,
                    purpose=f"Implement components for {', '.join(scope)}",
                    target_scope=tuple(scope),
                    expected_output_schema_ref="schema:step_output_v1",
                    acceptance_criteria=tuple(contract.definition_of_done),
                    validation_commands=("pytest",),
                    assigned_role="executor",
                    step_budget=default_step_budget,
                    status="PENDING",
                    created_at=now_str,
                    facet_refs=binding.facet_refs,
                    operation_refs=binding.operation_refs,
                    entity_refs=binding.entity_refs,
                    mapping_refs=binding.mapping_refs,
                    obligation_refs=binding.obligation_refs,
                    outcome_contract_id=contract.contract_id,
                    outcome_contract_hash=contract.contract_hash,
                )
            )

        plan_revision = PlanRevisionV1(
            plan_id=bound_plan.plan_id,
            objective_id=contract.objective_id,
            cycle_id=f"cycle_{contract.objective_id}",
            revision=1,
            steps=tuple(step_contracts),
            status="ACCEPTED",
            created_at=now_str,
        )

        return VerifiedPlan(
            plan_revision=plan_revision,
            flow_coherence_verified=True,
            coverage_alignment_verified=True,
        )
