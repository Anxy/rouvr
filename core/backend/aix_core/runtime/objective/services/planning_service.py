"""AIX Objective Planning Service.

Extracts exploration gate, step contract proposal, plan quality evaluation,
and incremental replanning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.aix_core.runtime.planner.exploration_planner import (
    ExplorationPlan,
    build_exploration_plan,
    requires_exploration,
)
from backend.aix_core.runtime.planner.incremental_replan import (
    perform_incremental_replan,
)
from backend.aix_core.runtime.planner.plan_quality import (
    PlanQualityResult,
    evaluate_plan_quality,
)
from backend.aix_core.runtime.planner.plan_validation import validate_plan_capabilities
from backend.aix_core.runtime.planner.step_contract_planner import propose_step_contract
from backend.aix_core.runtime.objective.contracts import (
    ObjectiveEnvelopeV1,
    ObjectiveObligationV1,
    PlanRevisionV1,
    StepContractV1,
)

logger = logging.getLogger(__name__)


@dataclass
class PlanProposalResult:
    plan_revision: PlanRevisionV1 | None
    step_contracts: list[StepContractV1]
    plan_quality: PlanQualityResult | None
    error: dict[str, Any] | None = None


class PlanningService:
    """Coordinates exploration gating, step planning, quality validation, and replans."""

    def __init__(self) -> None:
        pass

    def check_exploration_needed(
        self,
        *,
        task_family: str,
        allowed_files: tuple[str, ...],
        understanding_decision: str,
    ) -> bool:
        """Determines if the objective requires an exploration phase before planning."""
        return bool(requires_exploration(task_family, allowed_files, understanding_decision))

    def evaluate_quality(
        self,
        *,
        plan: PlanRevisionV1,
        obligations: list[ObjectiveObligationV1],
    ) -> PlanQualityResult:
        """Evaluates plan quality against requirements."""
        return evaluate_plan_quality(plan=plan, obligations=obligations)

    def replan_incrementally(
        self,
        *,
        current_plan: PlanRevisionV1,
        failed_step_id: str,
        failure_reason: str,
        repair_context: dict[str, Any] | None = None,
    ) -> PlanRevisionV1:
        """Performs localized incremental replan around the failed step."""
        return perform_incremental_replan(
            current_plan=current_plan,
            failed_step_id=failed_step_id,
            failure_reason=failure_reason,
            repair_context=repair_context or {},
        )
