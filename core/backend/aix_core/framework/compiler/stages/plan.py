"""AIX Framework Compiler - Stage 4: Target Plan Synthesis (PD-003B).

Sprint: F10 Work Compiler (F10-P1)
Grounded in: F10-COMPILER-STAGE-CONTRACTS.md, product_structure_plan.py.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.framework.compiler.stages.define import DefinedOutcome
from backend.aix_core.framework.compiler.stages.structure import ProductStructureDecomposition
from backend.aix_core.runtime.objective.product_structure_plan import (
    TargetTrace,
    compile_product_structure_plan,
)


class RawPlanSynthesis(BaseModel):
    """Synthesized plan targets derived from product structure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    objective_id: str
    step_scopes: tuple[tuple[str, ...], ...]
    product_root: str
    target_count: int


class PlanStage:
    """Stage 4: Synthesizes execution plan targets from product structure."""

    @classmethod
    def run(
        cls,
        structure_decomp: ProductStructureDecomposition,
        outcome: DefinedOutcome,
        *,
        create_roots: tuple[str, ...] = ("src", "frontend", "backend"),
        create_capability: tuple[str, ...] = ("CREATE_FILE", "CREATE_DIRECTORY"),
        outcome_claim_ids: tuple[str, ...] = ("outcome_1",),
    ) -> tuple[RawPlanSynthesis, tuple[TargetTrace, ...]]:
        contract = outcome.outcome_contract
        structure = structure_decomp.product_structure
        intent_text = contract.definition_of_done[0] if contract.definition_of_done else "task"

        plan_result = compile_product_structure_plan(
            contract=contract,
            structure=structure,
            intent_text=intent_text,
            create_roots=create_roots,
            create_capability=create_capability,
            outcome_claim_ids=outcome_claim_ids,
        )
        if not plan_result.ok:
            raise ValueError(f"Plan target synthesis failed: {plan_result.code} - {plan_result.detail}")

        synthesis = RawPlanSynthesis(
            plan_id=f"plan_{structure_decomp.structure_hash[:16]}",
            objective_id=structure_decomp.objective_id,
            step_scopes=tuple(plan_result.step_scopes),
            product_root=plan_result.product_root,
            target_count=len(plan_result.targets),
        )
        return synthesis, tuple(plan_result.traces)
