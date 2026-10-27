"""AIX Framework Compiler - Stage 7: Canonical WorkGraph Emission.

Sprint: F10 Work Compiler (F10-P2)
Grounded in: F10-COMPILER-STAGE-CONTRACTS.md, WorkGraph, WorkGraphAdapter.

Emits from the *admitted* PlanRevision (planner SoR) — never invents a second plan.
"""

from __future__ import annotations

from backend.aix_core.framework.compiler.stages.validate import VerifiedPlan
from backend.aix_core.framework.work.adapters import WorkGraphAdapter
from backend.aix_core.framework.work.models import WorkGraph, WorkUnit
from backend.aix_core.runtime.objective.contracts import PlanRevisionV1


class GraphStage:
    """Stage 7: Emits canonical WorkGraph from verified / admitted plan revision."""

    @classmethod
    def run(cls, verified_plan: VerifiedPlan) -> WorkGraph:
        return cls.from_plan(verified_plan.plan_revision)

    @classmethod
    def from_plan(cls, plan: PlanRevisionV1) -> WorkGraph:
        """Emit WorkGraph from canonical admitted PlanRevisionV1 (planner authority)."""
        graph = WorkGraphAdapter.from_plan(plan)
        if not WorkGraphAdapter.dependency_edges_match_plan(plan, graph):
            raise ValueError("Emitted WorkGraph dependency edges do not match PlanRevision")

        enriched: list[WorkUnit] = []
        for unit in graph.units:
            verification = tuple(unit.contract.validation_commands or ()) + tuple(
                unit.contract.expected_checks or ()
            )
            enriched.append(
                unit.model_copy(
                    update={
                        "verification_requirements": verification,
                        "recovery_metadata": {
                            "risk_tier": unit.contract.risk_tier,
                            "approval_required": unit.contract.approval_required,
                            "timeout_seconds": unit.contract.timeout_seconds,
                        },
                        "context_refs": tuple(
                            r
                            for r in (
                                unit.contract.outcome_contract_id,
                                *(unit.contract.facet_refs or ()),
                            )
                            if r
                        ),
                        "evidence_refs": tuple(unit.contract.obligation_refs or ()),
                        "required_agent_role": str(unit.contract.assigned_role or "") or None,
                    }
                )
            )
        ordered = WorkGraph(
            graph_id=graph.graph_id,
            work_id=graph.work_id,
            revision=graph.revision,
            units=tuple(enriched),
            created_from_revision=graph.created_from_revision,
            created_at=graph.created_at,
        )
        return ordered.model_copy(update={"execution_order": ordered.compute_execution_order()})
