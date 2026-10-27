"""AIX-W02-E02-T003: Centralized Completion Authority.

Verifies:
1. Centralized completion authority: only deterministic completion gates (evaluate_completion_gates) can transition a step or objective to verified status.
2. The executor cannot grade itself or bypass completion gate evaluation.
3. Direct writes from executor, kernel, or MVP attempting to declare completion fail closed.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.verification.completion_gates import (
    evaluate_completion_gates,
    CompletionGateResult,
)


@pytest.mark.asyncio
async def test_executor_cannot_grade_itself_without_completion_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An executor payload where verification gates failed (e.g. objective_status="BLOCKED")
    MUST be rejected and the cycle marked BLOCKED / VERIFICATION_HALT, refusing to advance to COMPLETED_FOR_REVIEW.
    """

    async def _mock_failed_gates_executor(**kwargs: Any) -> RealExecuteResult:
        return RealExecuteResult(
            ok=True,
            code="EXECUTOR_GATE_FAILURE",
            workspace=r"D:\tmp\ws",
            evidence_refs=["file://ws/evidence/report.json"],
            artifacts={"execution_report": "file://ws/evidence/report.json"},
            execution_status="BLOCKED",
            verification_status="NOT_RUN",
            objective_status="BLOCKED",
            reason_code="COSMETIC_PATCH_ONLY",
            real_operations=1,
            target_files_modified=0,
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        _mock_failed_gates_executor,
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False
    )

    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-centralized",
        actor_id="human-tester",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-7001",
            "objective": "Refactor module",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "hyeve-integration-service",
            "allowed_files": (),
            "project_id": "proj-centralized",
            "task_number": 7001,
        },
        idempotency_key="admit-7001",
        expected_version=None,
    )

    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-centralized",
        actor_id="human-tester",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc7001234", "budgets": {"max_steps": 3}},
        idempotency_key="start-7001",
        expected_version=None,
    )

    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-centralized",
        actor_id="human-tester",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/test-7001"},
        idempotency_key="approve-7001",
        expected_version=started["version"],
    )

    # Must fail closed: executor cannot grade itself, completion gate failure blocks cycle
    assert approved["ok"] is False
    assert approved["phase"] == "BLOCKED"
    assert approved["phase"] != "COMPLETED_FOR_REVIEW"


def test_deterministic_completion_gates_sole_authority() -> None:
    """Verifies that evaluate_completion_gates is the sole deterministic function producing CompletionGateResult."""

    class MockDiscovery:
        files_read = 1
        config_files: dict[str, str] = {}

    result = evaluate_completion_gates(
        discovery=MockDiscovery(),  # type: ignore[arg-type]
        diff_stats={"main.py": {"additions": 10, "deletions": 2}},
        diff_text="+def new_feature(): pass\n",
        test_result={"returncode": 0, "stdout": "1 passed"},
        already_satisfied=False,
        task_family="REFACTOR",
        objective="refactor main.py: clean up formatting",
        required_effects=(),
    )

    assert isinstance(result, CompletionGateResult)
    assert result.passed is True
    assert result.gates["source_read"] is True
    assert result.gates["source_changed"] is True
