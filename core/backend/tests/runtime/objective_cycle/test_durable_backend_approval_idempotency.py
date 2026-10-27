"""Approval must be idempotent: a repeated approval/resume call for the same cycle
never re-triggers `_maybe_real_execute`, and VERSION_CONFLICT carries enough state for
a caller to recover instead of spinning against WAITING_APPROVAL."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


async def _admit_and_start(backend: DurableObjectiveCycleApiBackend) -> tuple[str, str, int]:
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": "Prove approval idempotency",
            "task_class": "bounded_repair",
            "definition_of_done": ("tests pass",),
            "repository_id": "aix",
            # Admission must ground Understanding (empty allowed_files → NEED_EVIDENCE).
            # This suite asserts approval idempotency after a successful dry-run complete.
            "allowed_files": ("service.py",),
        },
        idempotency_key="admit-1",
        expected_version=None,
    )
    objective_id = admit["objective_id"]
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=objective_id,
        body={"candidate_sha": "abc1234", "budgets": {}},
        idempotency_key="start-1",
        expected_version=None,
    )
    return objective_id, started["cycle_id"], started["version"]


@pytest.mark.asyncio
async def test_repeat_approval_with_same_request_id_does_not_re_mutate() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=False)
    _objective_id, cycle_id, version = await _admit_and_start(backend)

    first = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=cycle_id,
        body={"approval_reference": "workhub://approvals/a", "approval_request_id": "req-1"},
        idempotency_key="approve-1",
        expected_version=version,
    )
    assert first["ok"] is True
    assert first["phase"] == "COMPLETED_FOR_REVIEW"

    second = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=cycle_id,
        body={"approval_reference": "workhub://approvals/a", "approval_request_id": "req-1"},
        idempotency_key="approve-2",  # a different idempotency key — the extension's
        # own Date.now()-based key changes on every retry, so the guard must not rely
        # on that alone.
        expected_version=first["version"],
    )
    assert second["ok"] is True
    assert second.get("already_processed") is True
    assert second["version"] == first["version"], "an idempotent replay must not bump the cycle version"


@pytest.mark.asyncio
async def test_repeat_resume_without_explicit_request_id_is_still_deduped_by_phase() -> None:
    """The extension does not always send an explicit approval_request_id — the
    fallback derivation (cycle_id + phase) must still catch the exact duplicate-resume
    bug this fix targets."""
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=False)
    _objective_id, cycle_id, version = await _admit_and_start(backend)

    first = await backend.command(
        operation="resume_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=cycle_id,
        body={"approval_reference": "workhub://approvals/aix-studio-local"},
        idempotency_key="resume-1",
        expected_version=version,
    )
    assert first["phase"] == "COMPLETED_FOR_REVIEW"

    second = await backend.command(
        operation="resume_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=cycle_id,
        body={"approval_reference": "workhub://approvals/aix-studio-local"},
        idempotency_key="resume-2",
        expected_version=first["version"],
    )
    assert second.get("already_processed") is True
    assert second["version"] == first["version"]


@pytest.mark.asyncio
async def test_version_conflict_carries_current_phase_for_recovery() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=False)
    _objective_id, cycle_id, version = await _admit_and_start(backend)

    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=cycle_id,
        body={"approval_reference": "workhub://approvals/a"},
        idempotency_key="approve-1",
        expected_version=version,
    )
    assert approved["phase"] == "COMPLETED_FOR_REVIEW"

    conflict = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=cycle_id,
        body={"approval_reference": "workhub://approvals/a"},
        idempotency_key="approve-stale",
        expected_version=version,  # the caller's now-stale cached version
    )
    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "VERSION_CONFLICT"
    # A caller can see the cycle already advanced and stop retrying instead of
    # spinning a resume loop against a phase that no longer exists.
    assert conflict["error"]["current_phase"] == "COMPLETED_FOR_REVIEW"


@pytest.mark.asyncio
async def test_unknown_cycle_returns_not_found_not_a_silent_success() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=False)
    result = await backend.command(
        operation="resume_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id="cyc_does_not_exist",
        body={},
        idempotency_key="resume-x",
        expected_version=None,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "CYCLE_NOT_FOUND"
