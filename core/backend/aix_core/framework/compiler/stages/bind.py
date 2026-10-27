"""AIX Framework Compiler - Stage 5: Semantic Step Binding (PD-003B2).

Sprint: F10 Work Compiler (F10-P1)
Grounded in: F10-COMPILER-STAGE-CONTRACTS.md, product_structure_step_binding.py.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.framework.compiler.stages.define import DefinedOutcome
from backend.aix_core.framework.compiler.stages.plan import RawPlanSynthesis
from backend.aix_core.framework.compiler.stages.structure import ProductStructureDecomposition
from backend.aix_core.runtime.objective.product_structure_plan import TargetTrace
from backend.aix_core.runtime.objective.product_structure_step_binding import (
    StepSemanticBindingV1,
    compile_product_structure_step_bindings,
)


class BoundPlan(BaseModel):
    """Bound plan with semantic relationships linking targets to structural entities."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    objective_id: str
    step_scopes: tuple[tuple[str, ...], ...]
    bindings: tuple[StepSemanticBindingV1, ...]


class BindStage:
    """Stage 5: Binds synthesized targets back to product structure facets/entities."""

    @classmethod
    def run(
        cls,
        raw_plan: RawPlanSynthesis,
        traces: tuple[TargetTrace, ...],
        structure_decomp: ProductStructureDecomposition,
        outcome: DefinedOutcome,
    ) -> BoundPlan:
        contract = outcome.outcome_contract
        structure = structure_decomp.product_structure

        binding_result = compile_product_structure_step_bindings(
            contract=contract,
            structure=structure,
            step_scopes=raw_plan.step_scopes,
            traces=traces,
        )
        if not binding_result.ok:
            raise ValueError(f"Step binding failed: {binding_result.code} - {binding_result.detail}")

        return BoundPlan(
            plan_id=raw_plan.plan_id,
            objective_id=raw_plan.objective_id,
            step_scopes=raw_plan.step_scopes,
            bindings=tuple(binding_result.bindings),
        )
