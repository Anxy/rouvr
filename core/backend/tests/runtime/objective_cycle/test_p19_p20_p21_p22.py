"""Tests for P19 (scheduling), P20 (control room), P21 (budget ledger), P22 (recovery)."""

from __future__ import annotations

import pytest


# ── P19: Work Scheduler ─────────────────────────────────────────────────────

class TestWorkScheduler:
    def test_schedule_once(self):
        from backend.aix_core.runtime.scheduling.work_scheduler import (
            WorkScheduler, ScheduledWorkV1, ScheduleTriggerV1, ScheduleState,
        )
        sched = WorkScheduler()
        work = sched.schedule(ScheduledWorkV1(
            work_id="w-1", tenant_id="t1", objective_id="obj-1",
            description="one-time job",
            trigger=ScheduleTriggerV1(kind="once", at="2026-08-20T00:00:00Z"),
        ))
        assert work.state == ScheduleState.ACTIVE
        assert work.next_run_at == "2026-08-20T00:00:00Z"

    def test_get_due_work(self):
        from backend.aix_core.runtime.scheduling.work_scheduler import (
            WorkScheduler, ScheduledWorkV1, ScheduleTriggerV1,
        )
        sched = WorkScheduler()
        sched.schedule(ScheduledWorkV1(
            work_id="w-1", tenant_id="t1", objective_id="obj-1",
            description="past due",
            trigger=ScheduleTriggerV1(kind="once", at="2020-01-01T00:00:00Z"),
        ))
        sched.schedule(ScheduledWorkV1(
            work_id="w-2", tenant_id="t1", objective_id="obj-2",
            description="future",
            trigger=ScheduleTriggerV1(kind="once", at="2099-01-01T00:00:00Z"),
        ))
        due = sched.get_due_work(now="2026-08-19T00:00:00Z")
        assert len(due) == 1
        assert due[0].work_id == "w-1"

    def test_cancel_work(self):
        from backend.aix_core.runtime.scheduling.work_scheduler import (
            WorkScheduler, ScheduledWorkV1, ScheduleTriggerV1, ScheduleState,
        )
        sched = WorkScheduler()
        sched.schedule(ScheduledWorkV1(
            work_id="w-1", tenant_id="t1", objective_id="obj-1",
            description="cancel me",
            trigger=ScheduleTriggerV1(kind="once", at="2099-01-01T00:00:00Z"),
        ))
        assert sched.cancel("w-1") is True
        assert sched.get("w-1").state == ScheduleState.CANCELLED

    def test_pause_resume(self):
        from backend.aix_core.runtime.scheduling.work_scheduler import (
            WorkScheduler, ScheduledWorkV1, ScheduleTriggerV1, ScheduleState,
        )
        sched = WorkScheduler()
        sched.schedule(ScheduledWorkV1(
            work_id="w-1", tenant_id="t1", objective_id="obj-1",
            description="pausable",
            trigger=ScheduleTriggerV1(kind="recurring", interval_seconds=60),
        ))
        assert sched.pause("w-1") is True
        assert sched.get("w-1").state == ScheduleState.PAUSED
        assert sched.resume("w-1") is True
        assert sched.get("w-1").state == ScheduleState.ACTIVE

    def test_event_trigger(self):
        from backend.aix_core.runtime.scheduling.work_scheduler import (
            WorkScheduler, ScheduledWorkV1, ScheduleTriggerV1,
        )
        sched = WorkScheduler()
        sched.schedule(ScheduledWorkV1(
            work_id="w-evt", tenant_id="t1", objective_id="obj-1",
            description="on deploy",
            trigger=ScheduleTriggerV1(kind="event", event_type="deployment.completed"),
        ))
        triggered = sched.fire_event("deployment.completed")
        assert len(triggered) == 1
        assert triggered[0].work_id == "w-evt"

    def test_execution_recording(self):
        from backend.aix_core.runtime.scheduling.work_scheduler import (
            WorkScheduler, ScheduledWorkV1, ScheduleTriggerV1, ScheduleState,
        )
        sched = WorkScheduler()
        sched.schedule(ScheduledWorkV1(
            work_id="w-1", tenant_id="t1", objective_id="obj-1",
            description="run once",
            trigger=ScheduleTriggerV1(kind="once", at="2020-01-01T00:00:00Z"),
        ))
        rec = sched.record_execution_start("w-1")
        assert rec is not None
        sched.record_execution_complete(rec.execution_id, success=True)
        assert sched.get("w-1").state == ScheduleState.COMPLETED

    def test_max_runs_enforcement(self):
        from backend.aix_core.runtime.scheduling.work_scheduler import (
            WorkScheduler, ScheduledWorkV1, ScheduleTriggerV1, ScheduleState,
        )
        sched = WorkScheduler()
        sched.schedule(ScheduledWorkV1(
            work_id="w-1", tenant_id="t1", objective_id="obj-1",
            description="limited", max_runs=2,
            trigger=ScheduleTriggerV1(kind="recurring", interval_seconds=60),
        ))
        for _ in range(2):
            rec = sched.record_execution_start("w-1")
            sched.record_execution_complete(rec.execution_id, success=True)
        assert sched.get("w-1").state == ScheduleState.COMPLETED


# ── P20: Control Room ───────────────────────────────────────────────────────

class TestControlRoom:
    def test_update_and_query_objective(self):
        from backend.aix_core.runtime.observability.control_room import (
            ControlRoom, ObjectiveStatusV1,
        )
        cr = ControlRoom()
        cr.update_objective(ObjectiveStatusV1(
            objective_id="obj-1", tenant_id="t1", title="Fix bug",
            state="executing", steps_completed=2, steps_total=5,
        ))
        obj = cr.get_objective("obj-1")
        assert obj is not None
        assert obj.state == "executing"

    def test_list_by_state(self):
        from backend.aix_core.runtime.observability.control_room import (
            ControlRoom, ObjectiveStatusV1,
        )
        cr = ControlRoom()
        cr.update_objective(ObjectiveStatusV1(
            objective_id="obj-1", tenant_id="t1", title="A", state="executing",
        ))
        cr.update_objective(ObjectiveStatusV1(
            objective_id="obj-2", tenant_id="t1", title="B", state="completed",
        ))
        active = cr.list_objectives(state="executing")
        assert len(active) == 1

    def test_pause_via_command(self):
        from backend.aix_core.runtime.observability.control_room import (
            ControlRoom, ObjectiveStatusV1, ControlCommandV1,
        )
        cr = ControlRoom()
        cr.update_objective(ObjectiveStatusV1(
            objective_id="obj-1", tenant_id="t1", title="A", state="executing",
        ))
        result = cr.issue_command(ControlCommandV1(
            command_id="cmd-1", action="pause",
            target_type="objective", target_id="obj-1",
            issued_by="admin", reason="need to review",
        ))
        assert result.accepted is True
        assert cr.get_objective("obj-1").state == "paused"

    def test_health_snapshot(self):
        from backend.aix_core.runtime.observability.control_room import (
            ControlRoom, ObjectiveStatusV1,
        )
        cr = ControlRoom()
        cr.update_objective(ObjectiveStatusV1(
            objective_id="obj-1", tenant_id="t1", title="A",
            state="executing", cost_usd=1.50, tokens_used=5000,
        ))
        health = cr.get_health()
        assert health.active_objectives == 1
        assert health.total_cost_usd_24h == 1.50

    def test_timeline_events(self):
        from backend.aix_core.runtime.observability.control_room import (
            ControlRoom, TimelineEventV1,
        )
        cr = ControlRoom()
        cr.record_event(TimelineEventV1(
            timestamp="2026-08-19T10:00:00Z", objective_id="obj-1",
            event_type="step_started", summary="Step 1 started",
        ))
        events = cr.get_timeline(objective_id="obj-1")
        assert len(events) == 1


# ── P21: Budget Ledger ───────────────────────────────────────────────────────

class TestBudgetLedger:
    def test_record_and_check(self):
        from backend.aix_core.runtime.governance.budget_ledger import (
            BudgetLedger, BudgetLimitV1,
        )
        ledger = BudgetLedger()
        ledger.set_limit(BudgetLimitV1(
            scope="objective", scope_id="obj-1",
            max_tokens=10000, max_cost_usd=5.0,
        ))
        result = ledger.record("objective", "obj-1", tokens=3000, cost_usd=1.0)
        assert result.allowed is True
        assert result.usage.tokens_used == 3000

    def test_budget_exhaustion(self):
        from backend.aix_core.runtime.governance.budget_ledger import (
            BudgetLedger, BudgetLimitV1,
        )
        ledger = BudgetLedger()
        ledger.set_limit(BudgetLimitV1(
            scope="objective", scope_id="obj-1", max_tokens=100,
        ))
        ledger.record("objective", "obj-1", tokens=100)
        result = ledger.check("objective", "obj-1")
        assert result.allowed is False
        assert result.exhausted_dimension == "tokens"

    def test_multi_dimension_limits(self):
        from backend.aix_core.runtime.governance.budget_ledger import (
            BudgetLedger, BudgetLimitV1,
        )
        ledger = BudgetLedger()
        ledger.set_limit(BudgetLimitV1(
            scope="tenant", scope_id="t1",
            max_tokens=50000, max_cost_usd=10.0, max_retries=5,
        ))
        ledger.record("tenant", "t1", retries=5)
        result = ledger.check("tenant", "t1")
        assert result.allowed is False
        assert result.exhausted_dimension == "retries"

    def test_no_limit_always_allowed(self):
        from backend.aix_core.runtime.governance.budget_ledger import BudgetLedger
        ledger = BudgetLedger()
        result = ledger.record("project", "p-1", tokens=999999)
        assert result.allowed is True

    def test_reset_clears_usage(self):
        from backend.aix_core.runtime.governance.budget_ledger import (
            BudgetLedger, BudgetLimitV1,
        )
        ledger = BudgetLedger()
        ledger.set_limit(BudgetLimitV1(scope="step", scope_id="s-1", max_tokens=100))
        ledger.record("step", "s-1", tokens=100)
        ledger.reset("step", "s-1")
        result = ledger.check("step", "s-1")
        assert result.allowed is True


# ── P22: Recovery Manager ────────────────────────────────────────────────────

class TestRecoveryManager:
    def test_idempotency_key(self):
        from backend.aix_core.runtime.reliability.recovery_manager import RecoveryManager
        mgr = RecoveryManager()
        assert mgr.check_idempotency("key-1") is None
        mgr.register_idempotency("key-1", "t1:obj-1:s-1:a-1", result_hash="abc")
        existing = mgr.check_idempotency("key-1")
        assert existing is not None
        assert existing.completed is True

    def test_checkpoint_save_restore(self):
        from backend.aix_core.runtime.reliability.recovery_manager import (
            RecoveryManager, RecoveryCheckpointV1,
        )
        mgr = RecoveryManager()
        mgr.save_checkpoint(RecoveryCheckpointV1(
            checkpoint_id="cp-1", objective_id="obj-1",
            tenant_id="t1", step_id="s-1", attempt_id="a-1",
            state_snapshot={"files_modified": ["a.py"]},
            completed_actions=["act-1", "act-2"],
        ))
        cp = mgr.get_latest_checkpoint("obj-1")
        assert cp is not None
        assert cp.completed_actions == ["act-1", "act-2"]

    def test_compensation_reverse_order(self):
        from backend.aix_core.runtime.reliability.recovery_manager import (
            RecoveryManager, CompensationActionV1,
        )
        mgr = RecoveryManager()
        mgr.register_compensation("obj-1", CompensationActionV1(
            compensation_id="c-1", original_action_id="a-1",
            capability="filesystem", operation="delete",
            rollback_args={"path": "new_file.py"},
        ))
        mgr.register_compensation("obj-1", CompensationActionV1(
            compensation_id="c-2", original_action_id="a-2",
            capability="git", operation="checkout",
            rollback_args={"branch": "main"},
        ))
        executed = mgr.execute_compensations("obj-1")
        assert len(executed) == 2
        assert executed[0].compensation_id == "c-2"  # reverse order
        assert executed[0].state == "EXECUTED"

    def test_recovery_lifecycle(self):
        from backend.aix_core.runtime.reliability.recovery_manager import (
            RecoveryManager, RecoveryState,
        )
        mgr = RecoveryManager()
        assert mgr.get_state("obj-1") == RecoveryState.HEALTHY
        mgr.start_recovery("obj-1")
        assert mgr.get_state("obj-1") == RecoveryState.RECOVERING
        mgr.complete_recovery("obj-1", success=True)
        assert mgr.get_state("obj-1") == RecoveryState.RECOVERED

    def test_reconciliation(self):
        from backend.aix_core.runtime.reliability.recovery_manager import (
            RecoveryManager, RecoveryCheckpointV1, RecoveryState,
        )
        mgr = RecoveryManager()
        mgr.save_checkpoint(RecoveryCheckpointV1(
            checkpoint_id="cp-1", objective_id="obj-1",
            tenant_id="t1", step_id="s-1", attempt_id="a-1",
        ))
        result = mgr.reconcile("obj-1")
        assert result.issues_found == 1
        assert result.issues_resolved == 1
        assert result.state_after == RecoveryState.HEALTHY
