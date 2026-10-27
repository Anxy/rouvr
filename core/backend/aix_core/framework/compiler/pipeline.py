"""AIX Framework Compiler Pipeline.

Sprint: F10 Work Compiler (F10-P1, F10-P2, F10-P3)
Orchestrates compiler stages 1 through 8 into a cohesive compilation flow.
"""

from __future__ import annotations

from typing import Any

from backend.aix_core.framework.agent.models import Role
from backend.aix_core.framework.compiler.stages.assign import AssignedWorkGraph, AssignStage
from backend.aix_core.framework.compiler.stages.bind import BindStage
from backend.aix_core.framework.compiler.stages.define import DefineStage
from backend.aix_core.framework.compiler.stages.graph import GraphStage
from backend.aix_core.framework.compiler.stages.intake import IntakeStage
from backend.aix_core.framework.compiler.stages.plan import PlanStage
from backend.aix_core.framework.compiler.stages.structure import StructureStage
from backend.aix_core.framework.compiler.stages.validate import ValidateStage, VerifiedPlan
from backend.aix_core.framework.work.models import WorkGraph
from backend.aix_core.runtime.objective.contracts import ObjectiveEnvelopeV1
from backend.aix_core.runtime.objective.outcome_contract import OutcomeFacetsV1


class CompilerPipeline:
    """Canonical staged Work Compiler pipeline."""

    @classmethod
    def compile_objective(
        cls,
        envelope: ObjectiveEnvelopeV1,
        *,
        definition_of_done: tuple[str, ...] | None = None,
        repository_id: str = "workspace",
        allowed_files: tuple[str, ...] | None = None,
        facets: OutcomeFacetsV1 | None = None,
        create_roots: tuple[str, ...] = ("proposed/",),
        create_capability: tuple[str, ...] = ("CREATE_FILE", "CREATE_DIRECTORY"),
        outcome_claim_ids: tuple[str, ...] = ("outcome_1",),
    ) -> VerifiedPlan:
        """Runs stages 1 through 6 to produce a verified plan."""
        # Stage 1: Intake
        intake = IntakeStage.run(envelope)

        # Stage 2: Define Outcome
        outcome = DefineStage.run(
            intake,
            definition_of_done=definition_of_done,
            repository_id=repository_id,
            allowed_files=allowed_files,
            facets=facets,
        )

        # Stage 3: Product Structure
        structure = StructureStage.run(outcome)

        # Stage 4: Synthesize Plan Targets
        raw_plan, traces = PlanStage.run(
            structure,
            outcome,
            create_roots=create_roots,
            create_capability=create_capability,
            outcome_claim_ids=outcome_claim_ids,
        )

        # Stage 5: Semantic Binding
        bound_plan = BindStage.run(raw_plan, traces, structure, outcome)

        # Stage 6: Validation (PD-003D/E + Continuity)
        return ValidateStage.run(bound_plan, traces, structure, outcome)

    @classmethod
    def compile_work_graph(
        cls,
        envelope: ObjectiveEnvelopeV1,
        *,
        definition_of_done: tuple[str, ...] | None = None,
        repository_id: str = "workspace",
        allowed_files: tuple[str, ...] | None = None,
        facets: OutcomeFacetsV1 | None = None,
        create_roots: tuple[str, ...] = ("proposed/",),
        create_capability: tuple[str, ...] = ("CREATE_FILE", "CREATE_DIRECTORY"),
        outcome_claim_ids: tuple[str, ...] = ("outcome_1",),
    ) -> WorkGraph:
        """Runs stages 1 through 7 to emit a canonical WorkGraph."""
        verified_plan = cls.compile_objective(
            envelope,
            definition_of_done=definition_of_done,
            repository_id=repository_id,
            allowed_files=allowed_files,
            facets=facets,
            create_roots=create_roots,
            create_capability=create_capability,
            outcome_claim_ids=outcome_claim_ids,
        )
        return GraphStage.run(verified_plan)

    @classmethod
    def compile_assigned_work_graph(
        cls,
        envelope: ObjectiveEnvelopeV1,
        *,
        default_role: Role = Role.EXECUTOR_CODER,
        definition_of_done: tuple[str, ...] | None = None,
        repository_id: str = "workspace",
        allowed_files: tuple[str, ...] | None = None,
        facets: OutcomeFacetsV1 | None = None,
        create_roots: tuple[str, ...] = ("proposed/",),
        create_capability: tuple[str, ...] = ("CREATE_FILE", "CREATE_DIRECTORY"),
        outcome_claim_ids: tuple[str, ...] = ("outcome_1",),
    ) -> AssignedWorkGraph:
        """Runs stages 1 through 8 to emit an assigned and governed WorkGraph."""
        graph = cls.compile_work_graph(
            envelope,
            definition_of_done=definition_of_done,
            repository_id=repository_id,
            allowed_files=allowed_files,
            facets=facets,
            create_roots=create_roots,
            create_capability=create_capability,
            outcome_claim_ids=outcome_claim_ids,
        )
        return AssignStage.run(graph, default_role=default_role)
