"""F10 compiler runtime bridge — compile from F1 Work + admitted PlanRevision.

Preserves ``propose_step_contract`` / PlanRevision as planning authority.
Does NOT invent a second planner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.aix_core.framework.compiler.stages.assign import AssignedWorkGraph, AssignStage
from backend.aix_core.framework.compiler.stages.graph import GraphStage
from backend.aix_core.framework.compiler.stages.structure import (
    ProductStructureDecomposition,
    StructureStage,
)
from backend.aix_core.framework.work.adapters import WorkDomainAdapter
from backend.aix_core.framework.work.models import Work, WorkDefinition, WorkGraph
from backend.aix_core.runtime.objective.contracts import ObjectiveEnvelopeV1, PlanRevisionV1
from backend.aix_core.runtime.objective.outcome_contract import OutcomeContractV1


@dataclass(frozen=True)
class CompilerCompileResult:
    """Canonical F10 compile artifacts from Work + admitted plan."""

    work: Work
    work_definition: WorkDefinition
    product_structure: ProductStructureDecomposition | None
    execution_plan: PlanRevisionV1
    work_graph: WorkGraph
    assigned: AssignedWorkGraph


def compiler_enabled(settings: object | None = None) -> bool:
    if settings is not None and hasattr(settings, "AIX_FRAMEWORK_COMPILER"):
        return bool(getattr(settings, "AIX_FRAMEWORK_COMPILER"))
    try:
        from backend.hyeve_aix_service.config import settings as _settings

        return bool(getattr(_settings, "AIX_FRAMEWORK_COMPILER", False))
    except Exception:
        return False


def multi_agent_enabled(settings: object | None = None) -> bool:
    if settings is not None and hasattr(settings, "AIX_FRAMEWORK_MULTI_AGENT"):
        return bool(getattr(settings, "AIX_FRAMEWORK_MULTI_AGENT"))
    try:
        from backend.hyeve_aix_service.config import settings as _settings

        return bool(getattr(_settings, "AIX_FRAMEWORK_MULTI_AGENT", False))
    except Exception:
        return False


def ensure_work_from_canonical(
    *,
    envelope: ObjectiveEnvelopeV1,
    plan: PlanRevisionV1,
    outcome: OutcomeContractV1 | None = None,
    existing: Work | None = None,
) -> Work:
    """F1 Work projection used as compiler input (idempotent)."""
    if existing is not None and existing.graph is not None:
        return existing
    return WorkDomainAdapter.work_from_envelope(envelope, outcome, plan=plan)


def compile_from_work_and_plan(
    *,
    work: Work,
    plan: PlanRevisionV1,
    outcome: OutcomeContractV1 | None = None,
    bind_models: bool = True,
) -> CompilerCompileResult:
    """
    F10-P1..P3 chain:

    Work → WorkDefinition → ProductStructure → ExecutionPlan(admitted) → WorkGraph → Assign

    ExecutionPlan is the admitted PlanRevision from the live planner — not re-planned.
    """
    work_def = work.definition
    product_structure: ProductStructureDecomposition | None = None
    if outcome is not None:
        try:
            import hashlib

            from backend.aix_core.framework.compiler.stages.define import DefinedOutcome as DO

            raw = outcome.model_dump(mode="json") if hasattr(outcome, "model_dump") else {}
            digest = hashlib.sha256(
                __import__("json").dumps(raw, sort_keys=True, default=str).encode()
            ).hexdigest()
            defined = DO(outcome_contract=outcome, definition_hash=digest)
            product_structure = StructureStage.run(defined)
        except Exception:
            product_structure = None

    work_graph = GraphStage.from_plan(plan)
    # Attach graph onto Work for continuity
    work_with_graph = work.model_copy(update={"graph": work_graph})
    assigned = AssignStage.run(work_graph, bind_models=bind_models)
    return CompilerCompileResult(
        work=work_with_graph,
        work_definition=work_def,
        product_structure=product_structure,
        execution_plan=plan,
        work_graph=assigned.graph,
        assigned=assigned,
    )


__all__ = [
    "CompilerCompileResult",
    "compile_from_work_and_plan",
    "compiler_enabled",
    "ensure_work_from_canonical",
    "multi_agent_enabled",
]
