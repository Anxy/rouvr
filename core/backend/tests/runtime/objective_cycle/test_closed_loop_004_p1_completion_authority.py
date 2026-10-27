"""AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P1 -- Eliminate False Completion.

P0's live audit found two concrete false-completion routes on the
`DurableObjectiveCycleApiBackend` hot path:

1. `_maybe_real_execute` returning `None` was the ONLY signal `_control` used to
   decide it was safe to call `engine.continue_after_approval()` (which always
   reaches COMPLETED_FOR_REVIEW via stub activities) -- but `None` was returned
   unconditionally once `run_real_execute` didn't raise, even when the result's own
   `objective_status` was BLOCKED/FAILED (not COMPLETED, and not the intentionally
   terminal AWAITING_REVIEW/PROPOSAL_READY proposal-for-review path).
2. `retry_step` while the engine was stuck at RUNNING_STEP (because an earlier
   `_maybe_real_execute` call returned an error without moving the engine off
   RUNNING_STEP) called `engine.continue_after_approval()` directly, bypassing
   `_maybe_real_execute` entirely -- a known AL-12 leftover.

These tests pin both fixes directly against the real `command()`/`_control` path.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult


async def _admit_and_start(backend: DurableObjectiveCycleApiBackend) -> dict[str, Any]:
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "task-401",
            "objective": "Refactor CronJobController.php",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "hyeve-integration-service",
            "allowed_files": ("CronJobController.php",),
            "project_id": "proj-1",
            "task_number": 401,
        },
        idempotency_key="admit-1",
        expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-1",
        expected_version=None,
    )
    return started


@pytest.mark.asyncio
async def test_blocked_objective_status_refuses_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `run_real_execute` result that is mechanically `ok=True` but whose own
    `objective_status` is not COMPLETED (and not the intentional AWAITING_REVIEW
    proposal path) must never reach COMPLETED_FOR_REVIEW -- P0's documented
    false-completion route #5.
    """

    async def _run_real_execute(**kwargs: Any) -> RealExecuteResult:
        return RealExecuteResult(
            ok=True,
            code="REAL_EXECUTE_OK",
            workspace=r"D:\tmp\ws",
            evidence_refs=["file://ws/evidence/execution_report.json"],
            artifacts={"execution_report": "file://ws/evidence/execution_report.json"},
            execution_status="BLOCKED",
            verification_status="NOT_RUN",
            objective_status="BLOCKED",
            reason_code="COSMETIC_PATCH_ONLY",
            real_operations=1,
            target_files_modified=1,
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute
    )
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    started = await _admit_and_start(backend)

    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/local-test"},
        idempotency_key="approve-1",
        expected_version=started["version"],
    )

    assert approved["ok"] is False
    assert approved["error"]["code"] == "OBJECTIVE_NOT_COMPLETE"
    assert approved["phase"] == "BLOCKED"
    assert approved["phase"] != "COMPLETED_FOR_REVIEW"
    assert approved["terminal_reason"] == "COSMETIC_PATCH_ONLY"
    assert approved["real_execute"]["objective_status"] == "BLOCKED"


@pytest.mark.asyncio
async def test_retry_step_at_running_step_reinvokes_real_execute_instead_of_bypassing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old behavior: `retry_step` while phase == RUNNING_STEP called
    `engine.continue_after_approval()` directly, so a cycle stuck at RUNNING_STEP
    because `_maybe_real_execute` refused (e.g. NO_WORK_ORDER/PLAN_CHANGED_SINCE_
    APPROVAL -- errors that don't themselves move the engine off RUNNING_STEP) would
    complete on retry without `_maybe_real_execute` ever running again. Fixed:
    `retry_step` at RUNNING_STEP must route through `_maybe_real_execute` exactly like
    "approval_reference"/"resume" do.
    """
    calls = {"n": 0}

    async def _fake_execute_claimed_step(
        *,
        cycle: Any,
        command_id: str,
        caller_auth: Any,
        actor_id: str,
        plan_only: bool = False,
    ) -> dict[str, Any] | None:
        del plan_only
        calls["n"] += 1
        if calls["n"] == 1:
            # Simulates a real refusal (e.g. NO_WORK_ORDER) that returns an error
            # payload WITHOUT transitioning the engine off RUNNING_STEP -- exactly the
            # stuck state the AL-12 leftover left retry_step unable to recover from.
            return {
                "ok": False,
                "operation": "real_execute",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "error": {"code": "NO_WORK_ORDER"},
            }
        # Canonical success signal: record real_execute then return None (same as
        # production `_maybe_real_execute` after a completed run).
        cycle.real_execute = {
            "ok": True,
            "code": "REAL_EXECUTE_OK",
            "objective_status": "COMPLETED",
            "verification_status": "PASSED",
            "changed_source_files": ["service.py"],
        }
        return None

    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    backend._execute_claimed_step = _fake_execute_claimed_step  # type: ignore[method-assign]
    started = await _admit_and_start(backend)

    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/local-test"},
        idempotency_key="approve-1",
        expected_version=None,
    )
    assert calls["n"] == 1
    assert approved["ok"] is False
    assert approved["error"]["code"] == "NO_WORK_ORDER"

    retried = await backend.command(
        operation="retry_step",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"step_id": "step-1"},
        idempotency_key="retry-1",
        expected_version=None,
    )

    # The fix: retry_step must call _maybe_real_execute a second time rather than
    # jumping straight to completion.
    assert calls["n"] == 2
    assert retried["ok"] is True
    assert retried["phase"] == "COMPLETED_FOR_REVIEW"
