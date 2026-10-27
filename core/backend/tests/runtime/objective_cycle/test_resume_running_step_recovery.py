"""Resume must recover from RUNNING_STEP + null real_execute (S2 desync).

When `_maybe_real_execute` returns an error without parking the engine, the cycle
used to keep `cycle.phase=WAITING_APPROVAL` while `engine.state.phase=RUNNING_STEP`.
A second `resume` then hit `cycle_not_awaiting_approval` and never re-executed.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


async def _admit_and_start(backend: DurableObjectiveCycleApiBackend) -> dict[str, Any]:
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "task-s2-desync",
            "objective": "Update feature_flag.py and test_feature_flag.py together",
            "task_class": "source_edit",
            "definition_of_done": ("done",),
            "repository_id": "aix-backend",
            "allowed_files": (
                "backend/tests/aix_core/objective_cycle/live_fixtures/feature_flag.py",
                "backend/tests/aix_core/objective_cycle/live_fixtures/test_feature_flag.py",
            ),
            "project_id": "proj-1",
            "task_number": 502,
        },
        idempotency_key="admit-s2",
        expected_version=None,
    )
    return await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-s2",
        expected_version=None,
    )


@pytest.mark.asyncio
async def test_resume_after_early_real_execute_refusal_resyncs_and_retries_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            # Refusal that historically left engine RUNNING_STEP and cycle.phase stale.
            return {
                "ok": False,
                "operation": "real_execute",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,  # still WAITING_APPROVAL before sync fix
                "error": {"code": "STEP_CONTRACT_NOT_PROPOSED", "detail": "planner refused"},
            }
        return None

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False
    )
    backend._execute_claimed_step = _fake_execute_claimed_step  # type: ignore[method-assign]
    started = await _admit_and_start(backend)
    cycle_id = started["cycle_id"]

    first = await backend.command(
        operation="resume_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=cycle_id,
        body={"approval_reference": "cli://s2-desync"},
        idempotency_key="resume-1",
        expected_version=None,
    )
    assert first["ok"] is False
    assert first["error"]["code"] == "STEP_CONTRACT_NOT_PROPOSED"
    # Phase must match the engine — not the pre-approval WAITING_APPROVAL stale value.
    assert first["phase"] == "RUNNING_STEP"
    assert first["status"]["phase"] == "RUNNING_STEP"

    cycle = backend._cycles[cycle_id]
    assert cycle.phase == "RUNNING_STEP"
    assert str(cycle.engine.state.phase) == "RUNNING_STEP"
    assert cycle.real_execute is None

    second = await backend.command(
        operation="resume_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=cycle_id,
        body={"approval_reference": "cli://s2-desync"},
        idempotency_key="resume-2",
        expected_version=None,
    )
    assert calls["n"] == 2
    assert second.get("already_processed") is not True
    assert second.get("idempotent_replay_reason") != "cycle_not_awaiting_approval"
    # A retry that still does not produce an ACCEPTED plan must now fail closed;
    # None from _execute_claimed_step is no longer treated as implicit success.
    assert second["ok"] is False
    assert second["error"]["code"] == "NO_ACCEPTED_PLAN"
    assert second["phase"] == "RUNNING_STEP"
