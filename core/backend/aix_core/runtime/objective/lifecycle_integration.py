"""GATE-D: Production wiring of P19-P22 into the objective lifecycle.

This module provides lifecycle hooks that the DurableObjectiveCycleApiBackend
(or any caller) invokes at key lifecycle points. Each hook delegates to the
appropriate P19-P22 component. This is the single integration point — no
component wires itself.

Lifecycle chain:
  schedule → admit → budget_reserve → control_room_register → start
  → checkpoint → pause → interrupt → recovery → reconcile → resume
  → execute → verify → budget_finalize → control_room_complete
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
import uuid

from backend.aix_core.runtime.scheduling.work_scheduler import (
    ScheduleState,
    ScheduleTriggerV1,
    ScheduledWorkV1,
    WorkScheduler,
)
from backend.aix_core.runtime.observability.control_room import (
    ControlCommandV1,
    ControlRoom,
    ObjectiveStatusV1,
    TimelineEventV1,
)
from backend.aix_core.runtime.governance.budget_ledger import (
    BudgetCheckResult,
    BudgetLedger,
    BudgetLimitV1,
)
from backend.aix_core.runtime.reliability.recovery_manager import (
    CompensationActionV1,
    RecoveryCheckpointV1,
    RecoveryManager,
    RecoveryState,
)


@dataclass
class LifecycleContext:
    """Binds a single objective to all P19-P22 subsystems."""
    objective_id: str
    tenant_id: str
    cycle_id: str = ""
    step_id: str = ""
    attempt_id: str = ""


class ObjectiveLifecycleIntegration:
    """Wires P19-P22 into objective lifecycle. One instance per backend."""

    def __init__(
        self,
        *,
        scheduler: WorkScheduler | None = None,
        control_room: ControlRoom | None = None,
        budget_ledger: BudgetLedger | None = None,
        recovery_manager: RecoveryManager | None = None,
    ) -> None:
        self.scheduler = scheduler or WorkScheduler()
        self.control_room = control_room or ControlRoom()
        self.budget_ledger = budget_ledger or BudgetLedger()
        self.recovery_manager = recovery_manager or RecoveryManager()

    # ── P19: Schedule ────────────────────────────────────────────────────

    def schedule_objective(
        self, ctx: LifecycleContext, *, trigger: ScheduleTriggerV1,
        description: str = "", payload: dict[str, Any] | None = None,
    ) -> ScheduledWorkV1:
        work = self.scheduler.schedule(ScheduledWorkV1(
            work_id=f"sched-{ctx.objective_id}",
            tenant_id=ctx.tenant_id,
            objective_id=ctx.objective_id,
            description=description or f"Scheduled: {ctx.objective_id}",
            trigger=trigger,
            payload=payload or {},
        ))
        self._emit_event(ctx, "objective.scheduled", f"Objective scheduled ({trigger.kind})")
        return work

    # ── P21: Budget ──────────────────────────────────────────────────────

    def reserve_budget(
        self, ctx: LifecycleContext, *, limit: BudgetLimitV1,
    ) -> BudgetCheckResult:
        self.budget_ledger.set_limit(limit)
        result = self.budget_ledger.check(limit.scope, limit.scope_id)
        self._emit_event(ctx, "budget.reserved", f"Budget reserved for {limit.scope}:{limit.scope_id}")
        return result

    def consume_budget(
        self, ctx: LifecycleContext, *,
        tokens: int = 0, cost_usd: float = 0.0,
        tool_calls: int = 0, retries: int = 0,
    ) -> BudgetCheckResult:
        result = self.budget_ledger.record(
            "objective", ctx.objective_id,
            tokens=tokens, cost_usd=cost_usd,
            tool_calls=tool_calls, retries=retries,
        )
        if not result.allowed:
            self._emit_event(
                ctx, "budget.exhausted",
                f"Budget exhausted: {result.exhausted_dimension}",
                severity="error",
            )
        return result

    # ── P20: Control Room ────────────────────────────────────────────────

    def register_objective(
        self, ctx: LifecycleContext, *, title: str, steps_total: int = 0,
    ) -> None:
        self.control_room.update_objective(ObjectiveStatusV1(
            objective_id=ctx.objective_id,
            tenant_id=ctx.tenant_id,
            title=title,
            state="admitted",
            steps_total=steps_total,
        ))
        self._emit_event(ctx, "objective.admitted", f"Objective admitted: {title}")

    def update_objective_state(
        self, ctx: LifecycleContext, *,
        state: str,
        current_step: str | None = None,
        steps_completed: int | None = None,
        cost_usd: float | None = None,
        tokens_used: int | None = None,
    ) -> None:
        existing = self.control_room.get_objective(ctx.objective_id)
        if existing is None:
            return
        updates: dict[str, Any] = {**existing.__dict__, "state": state}
        if current_step is not None:
            updates["current_step"] = current_step
        if steps_completed is not None:
            updates["steps_completed"] = steps_completed
        if cost_usd is not None:
            updates["cost_usd"] = cost_usd
        if tokens_used is not None:
            updates["tokens_used"] = tokens_used
        self.control_room.update_objective(ObjectiveStatusV1(**updates))  # type: ignore[arg-type]

    def pause_objective(self, ctx: LifecycleContext, *, reason: str, issued_by: str) -> bool:
        result = self.control_room.issue_command(ControlCommandV1(
            command_id=f"cmd-pause-{uuid.uuid4().hex[:8]}",
            action="pause",
            target_type="objective",
            target_id=ctx.objective_id,
            issued_by=issued_by,
            reason=reason,
        ))
        return result.accepted

    def resume_objective(self, ctx: LifecycleContext, *, issued_by: str) -> bool:
        result = self.control_room.issue_command(ControlCommandV1(
            command_id=f"cmd-resume-{uuid.uuid4().hex[:8]}",
            action="resume",
            target_type="objective",
            target_id=ctx.objective_id,
            issued_by=issued_by,
            reason="resume after pause/recovery",
        ))
        return result.accepted

    # ── P22: Checkpoint & Recovery ───────────────────────────────────────

    def checkpoint(
        self, ctx: LifecycleContext, *,
        state_snapshot: dict[str, Any] | None = None,
        completed_actions: list[str] | None = None,
    ) -> RecoveryCheckpointV1:
        cp = RecoveryCheckpointV1(
            checkpoint_id=f"cp-{uuid.uuid4().hex[:12]}",
            objective_id=ctx.objective_id,
            tenant_id=ctx.tenant_id,
            step_id=ctx.step_id,
            attempt_id=ctx.attempt_id,
            state_snapshot=state_snapshot or {},
            completed_actions=completed_actions or [],
        )
        self.recovery_manager.save_checkpoint(cp)
        self._emit_event(ctx, "checkpoint.saved", f"Checkpoint {cp.checkpoint_id}")
        return cp

    def register_compensation(
        self, ctx: LifecycleContext, *,
        action_id: str, capability: str, operation: str,
        rollback_args: dict[str, Any] | None = None,
    ) -> None:
        self.recovery_manager.register_compensation(
            ctx.objective_id,
            CompensationActionV1(
                compensation_id=f"comp-{uuid.uuid4().hex[:8]}",
                original_action_id=action_id,
                capability=capability,
                operation=operation,
                rollback_args=rollback_args or {},
            ),
        )

    def simulate_interruption(self, ctx: LifecycleContext) -> RecoveryState:
        """Simulate a crash/interruption for the objective."""
        self.recovery_manager.start_recovery(ctx.objective_id)
        self._emit_event(ctx, "recovery.started", "Simulated interruption, recovery started", severity="warn")
        return RecoveryState.RECOVERING

    def recover_and_reconcile(self, ctx: LifecycleContext) -> tuple[RecoveryState, Any]:
        """Attempt recovery + reconciliation after interruption."""
        recon = self.recovery_manager.reconcile(ctx.objective_id)
        if recon.state_after == RecoveryState.HEALTHY:
            self.recovery_manager.complete_recovery(ctx.objective_id, success=True)
            self._emit_event(ctx, "recovery.completed", f"Recovery succeeded: {recon.issues_resolved} issues resolved")
        else:
            self.recovery_manager.complete_recovery(ctx.objective_id, success=False)
            self._emit_event(ctx, "recovery.failed", f"Recovery failed: {recon.issues_found} issues", severity="error")
        return self.recovery_manager.get_state(ctx.objective_id), recon

    def complete_objective(self, ctx: LifecycleContext, *, success: bool) -> None:
        state = "completed" if success else "failed"
        self.update_objective_state(ctx, state=state)
        self._emit_event(ctx, f"objective.{state}", f"Objective {state}")

    # ── Internal ─────────────────────────────────────────────────────────

    def _emit_event(
        self, ctx: LifecycleContext, event_type: str, summary: str,
        severity: str = "info",
    ) -> None:
        self.control_room.record_event(TimelineEventV1(
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            objective_id=ctx.objective_id,
            event_type=event_type,
            summary=summary,
            severity=severity,  # type: ignore[arg-type]
        ))


__all__ = ["LifecycleContext", "ObjectiveLifecycleIntegration"]
