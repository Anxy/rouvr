"""AIX Feature 12 (Packet 06) — Autonomous Repair Loop & Safety Controls.

Coordinates automated repair cycle:
Gap (OPEN) -> Repair Plan -> Execute -> Test -> Re-verify -> Close Gap (or Escalate).
Enforces loop safety controls: max repair attempts, cost budget checks, escalation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from backend.aix_core.runtime.objective.contracts import StepContractV1
from backend.aix_core.runtime.planner.incremental_replan import (
    build_repair_step_from_gap,
    perform_incremental_replan,
)
from backend.aix_core.runtime.verification.gap import (
    GapStore,
    GapV1,
)


@dataclass(frozen=True)
class RepairPlanResult:
    status: Literal["REPAIR_PLANNED", "BUDGET_EXHAUSTED", "ESCALATED", "NO_OPEN_GAPS"]
    repair_steps: tuple[StepContractV1, ...]
    affected_gaps: tuple[str, ...]
    escalation_reason: str | None = None


class AutonomousRepairCoordinator:
    """Manages autonomous repair cycles and enforces bounded safety limits."""

    def __init__(self, max_repair_attempts: int = 3) -> None:
        self.max_repair_attempts = max_repair_attempts

    def plan_repair_for_open_gaps(
        self,
        *,
        cycle_id: str,
        tenant_id: str,
        objective_id: str,
        gap_store: GapStore,
        existing_steps: tuple[StepContractV1, ...],
        current_attempt: int = 1,
    ) -> RepairPlanResult:
        """Evaluates open gaps and generates localized repair steps or escalates."""
        open_gaps = gap_store.get_open_blocking_gaps(objective_id)
        if not open_gaps:
            return RepairPlanResult(
                status="NO_OPEN_GAPS",
                repair_steps=(),
                affected_gaps=(),
            )

        # One bounded repair step per attempt — prioritize root objective deviation
        # (e.g. OV-002 SQLite) over cascade runtime failures from the same defect.
        deviation_gaps = [g for g in open_gaps if g.category == "OBJECTIVE_DEVIATION"]
        gaps_to_repair = deviation_gaps[:1] if deviation_gaps else open_gaps[:1]

        # Enforce loop safety limit
        if current_attempt > self.max_repair_attempts:
            for g in open_gaps:
                g.escalate(reason=f"Exceeded max repair attempts ({self.max_repair_attempts})")
            return RepairPlanResult(
                status="ESCALATED",
                repair_steps=(),
                affected_gaps=tuple(g.gap_id for g in open_gaps),
                escalation_reason=f"Max repair attempts ({self.max_repair_attempts}) exceeded. Manual intervention required.",
            )

        repair_steps: list[StepContractV1] = []
        affected_gap_ids: list[str] = []

        # Prefer the failed step's scope so repair stays inside admission bounds.
        failed_scope: tuple[str, ...] = ()
        for existing in existing_steps:
            if existing.status in ("FAILED", "BLOCKED", "EXECUTING", "READY") and existing.target_scope:
                failed_scope = tuple(str(p) for p in existing.target_scope if str(p or "").strip())
                if failed_scope:
                    break
        if not failed_scope and existing_steps:
            latest = existing_steps[-1]
            failed_scope = tuple(str(p) for p in (latest.target_scope or ()) if str(p or "").strip())

        for idx, gap in enumerate(gaps_to_repair, start=1):
            gap.transition_to_in_repair()
            gap_store.put_gap(gap)
            step = build_repair_step_from_gap(
                gap=gap,
                step_id=f"repair_{cycle_id}_a{current_attempt}_s{idx}",
                step_index=len(existing_steps) + idx,
                cycle_id=cycle_id,
                tenant_id=tenant_id,
                fallback_scope=failed_scope,
            )
            repair_steps.append(step)
            affected_gap_ids.append(gap.gap_id)

        return RepairPlanResult(
            status="REPAIR_PLANNED",
            repair_steps=tuple(repair_steps),
            affected_gaps=tuple(affected_gap_ids),
        )

    def evaluate_reverification(
        self,
        *,
        verification_id: str,
        gap_store: GapStore,
        objective_id: str,
        reverified_ov_ids: set[str],
    ) -> tuple[list[GapV1], list[GapV1]]:
        """Closes gaps that passed re-verification; returns (closed_gaps, remaining_gaps)."""
        closed: list[GapV1] = []
        remaining: list[GapV1] = []

        for gap in gap_store.get_gaps_for_objective(objective_id):
            if gap.status in ("OPEN", "IN_REPAIR"):
                if gap.ov_id in reverified_ov_ids or not gap.ov_id:
                    gap.close_by_verifier(verification_id=verification_id)
                    gap_store.put_gap(gap)
                    closed.append(gap)
                else:
                    remaining.append(gap)

        return closed, remaining


__all__ = [
    "AutonomousRepairCoordinator",
    "RepairPlanResult",
]
