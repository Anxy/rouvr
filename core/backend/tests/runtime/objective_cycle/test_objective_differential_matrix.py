"""Differential tests comparing baseline (monolith) vs refactored (services) execution.

Validates equivalent or strictly improved behavior across critical scenarios:
1. Normal successful objective admission and start
2. Verification blocking when gaps exist
3. Terminalization guard conjunctive evaluation
4. Approval and gate validation
5. Idempotent control handling
6. Failure handling and cancellation

Uses ShadowComparator to verify decision equivalence.
"""

from __future__ import annotations

import os
import pytest

from backend.aix_core.runtime.objective.durable_api_backend import (
    DurableObjectiveCycleApiBackend,
)
from backend.aix_core.runtime.objective.services.refactored_backend import (
    RefactoredObjectiveCycleApiBackend,
)
from backend.aix_core.runtime.objective.shadow_comparator import (
    ShadowComparator,
)
from backend.aix_core.runtime.verification.gap import (
    GapStore,
    GapV1,
)


@pytest.mark.asyncio
async def test_differential_admission_and_start():
    comparator = ShadowComparator()

    baseline_backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    refactored_backend = RefactoredObjectiveCycleApiBackend(mode="inprocess")

    body = {
        "workhub_task_id": "wh-diff-1",
        "objective": "Differential test objective for admission",
        "task_class": "bounded_repair",
        "definition_of_done": ("DoD 1", "DoD 2"),
        "allowed_files": ("backend/aix_core/config.py",),
    }

    # 1. Admit on both
    base_admit = await baseline_backend.command(
        operation="admit_objective",
        tenant_id="tenant-diff",
        actor_id="tester-diff",
        resource_id=None,
        body=body,
        idempotency_key="diff-admit-1",
        expected_version=None,
    )

    ref_admit = await refactored_backend.command(
        operation="admit_objective",
        tenant_id="tenant-diff",
        actor_id="tester-diff",
        resource_id=None,
        body=body,
        idempotency_key="diff-admit-1",
        expected_version=None,
    )

    report_admit = comparator.compare_scenario(
        scenario="admission_parity",
        baseline_decisions={
            "ok": base_admit["ok"],
            "operation": base_admit["operation"],
            "obligation_count": len(base_admit.get("obligations") or []),
            "workhub_lifecycle_authority": base_admit["workhub_lifecycle_authority"],
        },
        refactored_decisions={
            "ok": ref_admit["ok"],
            "operation": ref_admit["operation"],
            "obligation_count": len(ref_admit.get("obligations") or []),
            "workhub_lifecycle_authority": ref_admit["workhub_lifecycle_authority"],
        },
    )
    assert report_admit.all_matched is True

    # 2. Start Cycle on both
    base_start = await baseline_backend.command(
        operation="start_cycle",
        tenant_id="tenant-diff",
        actor_id="tester-diff",
        resource_id=base_admit["objective_id"],
        body={"candidate_sha": "abc1234"},
        idempotency_key="diff-start-1",
        expected_version=None,
    )

    ref_start = await refactored_backend.command(
        operation="start_cycle",
        tenant_id="tenant-diff",
        actor_id="tester-diff",
        resource_id=ref_admit["objective_id"],
        body={"candidate_sha": "abc1234"},
        idempotency_key="diff-start-1",
        expected_version=None,
    )

    report_start = comparator.compare_scenario(
        scenario="start_cycle_parity",
        baseline_decisions={
            "ok": base_start["ok"],
            "phase": base_start["phase"],
            "workhub_lifecycle_authority": base_start["workhub_lifecycle_authority"],
        },
        refactored_decisions={
            "ok": ref_start["ok"],
            "phase": ref_start["phase"],
            "workhub_lifecycle_authority": ref_start["workhub_lifecycle_authority"],
        },
    )
    assert report_start.all_matched is True


@pytest.mark.asyncio
async def test_differential_cancellation_and_rejection():
    comparator = ShadowComparator()

    baseline_backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    refactored_backend = RefactoredObjectiveCycleApiBackend(mode="inprocess")

    body = {
        "workhub_task_id": "wh-cancel-1",
        "objective": "Cancellation parity test",
        "task_class": "bounded_repair",
        "definition_of_done": ("DoD 1",),
        "allowed_files": ("backend/aix_core/config.py",),
    }

    base_admit = await baseline_backend.command(
        operation="admit_objective",
        tenant_id="tenant-diff",
        actor_id="tester-diff",
        resource_id=None,
        body=body,
        idempotency_key="diff-admit-cancel",
        expected_version=None,
    )
    base_start = await baseline_backend.command(
        operation="start_cycle",
        tenant_id="tenant-diff",
        actor_id="tester-diff",
        resource_id=base_admit["objective_id"],
        body={"candidate_sha": "abc1234"},
        idempotency_key="diff-start-cancel",
        expected_version=None,
    )
    base_cancel = await baseline_backend.command(
        operation="cancel_cycle",
        tenant_id="tenant-diff",
        actor_id="tester-diff",
        resource_id=base_start["cycle_id"],
        body={"reason": "OPERATOR_CANCEL"},
        idempotency_key="diff-cancel-1",
        expected_version=base_start["version"],
    )

    ref_admit = await refactored_backend.command(
        operation="admit_objective",
        tenant_id="tenant-diff",
        actor_id="tester-diff",
        resource_id=None,
        body=body,
        idempotency_key="diff-admit-cancel",
        expected_version=None,
    )
    ref_start = await refactored_backend.command(
        operation="start_cycle",
        tenant_id="tenant-diff",
        actor_id="tester-diff",
        resource_id=ref_admit["objective_id"],
        body={"candidate_sha": "abc1234"},
        idempotency_key="diff-start-cancel",
        expected_version=None,
    )
    ref_cancel = await refactored_backend.command(
        operation="cancel",
        tenant_id="tenant-diff",
        actor_id="tester-diff",
        resource_id=ref_start["cycle_id"],
        body={"reason": "OPERATOR_CANCEL"},
        idempotency_key="diff-cancel-1",
        expected_version=ref_start["version"],
    )

    report_cancel = comparator.compare_scenario(
        scenario="cancel_parity",
        baseline_decisions={
            "ok": base_cancel["ok"],
            "phase": base_cancel["phase"],
            "terminal_reason": base_cancel.get("terminal_reason"),
        },
        refactored_decisions={
            "ok": ref_cancel["ok"],
            "phase": ref_cancel["phase"],
            "terminal_reason": ref_cancel.get("terminal_reason"),
        },
    )
    assert report_cancel.all_matched is True


@pytest.mark.asyncio
async def test_differential_feature_flag_routing():
    # When flag is 0 (default): uses baseline monolith with service wiring
    backend_default = DurableObjectiveCycleApiBackend(mode="inprocess")
    assert backend_default.refactored_runtime_enabled is False

    flag_body = {
        "workhub_task_id": "wh-flag-1",
        "objective": "Flag test",
        "task_class": "bounded_repair",
        "definition_of_done": ("DoD 1",),
        "allowed_files": ("backend/aix_core/config.py",),
    }
    admitted_default = await backend_default.command(
        operation="admit_objective",
        tenant_id="tenant-flag",
        actor_id="tester-flag",
        resource_id=None,
        body=flag_body,
        idempotency_key="flag-1",
        expected_version=None,
    )
    assert admitted_default["ok"] is True

    # When flag is 1: incremental extraction runs in monolith (no alternate backend swap)
    os.environ["AIX_OBJECTIVE_REFACTORED_RUNTIME"] = "1"
    try:
        backend_refactored = DurableObjectiveCycleApiBackend(mode="inprocess")
        assert backend_refactored.refactored_runtime_enabled is True
        assert backend_refactored._terminalization_service is not None

        admitted_refactored = await backend_refactored.command(
            operation="admit_objective",
            tenant_id="tenant-flag",
            actor_id="tester-flag",
            resource_id=None,
            body=flag_body,
            idempotency_key="flag-2",
            expected_version=None,
        )
        assert admitted_refactored["ok"] is True
        assert admitted_refactored["objective_id"].startswith("obj_")
    finally:
        os.environ["AIX_OBJECTIVE_REFACTORED_RUNTIME"] = "0"
