"""AIX Objective Repair Service.

Coordinates gap tracking, repair directive synthesis, and autonomous repair loops.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.aix_core.runtime.recovery.repair_directive import (
    build_repair_directive,
)
from backend.aix_core.runtime.recovery.repair_loop import AutonomousRepairCoordinator
from backend.aix_core.runtime.verification.gap import (
    GapCategory,
    GapSeverity,
    GapStatus,
    GapStore,
    GapV1,
)
from backend.aix_core.runtime.verification.repair_protocol import (
    RepairHypothesisV1,
    validate_repair_hypothesis,
)
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    PlanRevisionV1,
    RepairDirectiveV1,
    StepContractV1,
)

logger = logging.getLogger(__name__)


@dataclass
class RepairDirectiveResult:
    directive: RepairDirectiveV1 | None
    blocking_gaps: list[GapV1]
    can_auto_repair: bool


class RepairService:
    """Coordinates defect gap management and repair orchestration."""

    def __init__(self, gap_store: GapStore | None = None) -> None:
        self.gap_store = gap_store or GapStore()
        self.repair_coordinator = AutonomousRepairCoordinator()

    def record_gaps(self, gaps: list[GapV1]) -> None:
        """Records identified gaps in the gap store."""
        for gap in gaps:
            self.gap_store.put_gap(gap)

    def get_blocking_gaps(self, objective_id: str | None = None) -> list[GapV1]:
        """Returns all open blocking gaps."""
        if objective_id:
            return self.gap_store.get_open_blocking_gaps(objective_id)
        # Scan in-memory store for any open blocking gaps
        all_gaps: list[GapV1] = []
        for obj_id in self.gap_store._in_memory:
            all_gaps.extend(self.gap_store.get_open_blocking_gaps(obj_id))
        return all_gaps

    def synthesize_repair_directive(
        self,
        *,
        objective_id: str,
        plan: PlanRevisionV1 | None,
        failed_step: StepContractV1 | None = None,
        failure_reasons: tuple[str, ...] = (),
    ) -> RepairDirectiveResult:
        """Synthesizes a localized repair directive from open gaps or step failures."""
        blocking_gaps = self.get_blocking_gaps(objective_id)
        if not blocking_gaps and failed_step is None:
            return RepairDirectiveResult(directive=None, blocking_gaps=[], can_auto_repair=False)

        if failed_step is not None:
            reasons = failure_reasons or ("Step verification failed",)
            directive = build_repair_directive(
                step=failed_step,
                reasons=reasons,
            )
            can_auto_repair = bool(directive.required_correction)
        elif blocking_gaps:
            step_proposal = StepContractV1(
                id="repair-step-1",
                cycle_id="repair-cycle",
                tenant_id="tenant",
                step_index=0,
                purpose=f"Address {len(blocking_gaps)} blocking defect gaps",
                target_scope=("workspace",),
                expected_output_schema_ref="aix.step_contract.v1.proposal",
                acceptance_criteria=("Address blocking gaps",),
                validation_commands=("true; repair-verification",),
                assigned_role="executor",
                step_budget=BudgetV1(
                    time_seconds=60, tokens=1000, cost_usd=0.1, retries=1, steps=1, concurrency=1
                ),
                status="PENDING",
            )
            reasons = tuple(g.observed or g.category for g in blocking_gaps)
            directive = build_repair_directive(
                step=step_proposal,
                reasons=reasons,
            )
            can_auto_repair = bool(directive.required_correction)
        else:
            directive = None
            can_auto_repair = False

        return RepairDirectiveResult(
            directive=directive,
            blocking_gaps=blocking_gaps,
            can_auto_repair=can_auto_repair,
        )
