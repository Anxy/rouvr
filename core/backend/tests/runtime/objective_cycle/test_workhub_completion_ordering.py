"""Required ordering: RUNNING -> source edits -> verification -> diff generated ->
COMPLETED_FOR_REVIEW -> review/approval -> WorkHub completion. `mark_workhub_done`
must never fire before the source-edit branch's completion gates have actually
passed — `real_execute.ok=True` alone is not enough once gates exist."""

from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult


class _FakeWorkHubClient:
    def __init__(self) -> None:
        self.transitions: list[str] = []

    async def get_task(self, **kwargs: Any) -> dict[str, Any]:
        return {"id": kwargs["task_id"], "status": "queued", "project_id": kwargs["project_id"]}

    async def transition_task(self, **kwargs: Any) -> dict[str, Any]:
        self.transitions.append(str(kwargs["status_value"]))
        return {"id": kwargs["task_id"], "status": kwargs["status_value"]}

    async def add_task_evidence(self, **kwargs: Any) -> dict[str, Any]:
        return {"id": "ev-1", "task_id": kwargs["task_id"]}

    async def evaluate_task_verification(self, **kwargs: Any) -> dict[str, Any]:
        return {"decision": "pass", "eligible_for_done": True}


async def _run_cycle_with_completion_gates(
    monkeypatch: pytest.MonkeyPatch,
    *,
    completion_gates: dict[str, Any] | None,
    objective_status: str = "COMPLETED",
    execution_status: str = "COMPLETED",
    verification_status: str = "PASSED",
    real_operations: int = 1,
) -> tuple[dict[str, Any], _FakeWorkHubClient]:
    fake = _FakeWorkHubClient()

    async def _run_real_execute(**kwargs: Any) -> RealExecuteResult:
        return RealExecuteResult(
            ok=True,
            code="REAL_EXECUTE_OK",
            workspace=r"D:\tmp\ws",
            evidence_refs=["file://ws/evidence/execution_report.json"],
            artifacts={"execution_report": "file://ws/evidence/execution_report.json"},
            changed_source_files=[{"path": "config.py", "additions": 1, "deletions": 0}],
            repository_root=r"D:\tmp\repo",
            diff_text="diff --git a/config.py b/config.py\n",
            completion_gates=completion_gates,
            execution_status=execution_status,
            verification_status=verification_status,
            objective_status=objective_status,
            real_operations=real_operations,
            target_files_modified=1,
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _run_real_execute
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.build_workhub_client",
        lambda _settings=None, **_kwargs: fake,
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        mark_workhub_done=True,
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "task-201",
            "objective": "Add SMTP_PASSWORD support",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "hyeve-integration-service",
            "allowed_files": ("backend/config.py",),
            "project_id": "proj-1",
            "task_number": 201,
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
    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/local-test"},
        idempotency_key="approve-1",
        expected_version=started["version"],
    )
    return approved, fake


@pytest.mark.asyncio
async def test_mark_workhub_done_blocked_when_completion_gates_have_not_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved, fake = await _run_cycle_with_completion_gates(
        monkeypatch,
        completion_gates={
            "passed": False,
            "gates": {"verification_ran": False},
            "reasons": ["no configuration/build/lint/test verification was run"],
            "verification_verdict": "FAILED",
        },
    )
    assert approved["ok"] is False
    assert approved["error"]["code"] == "OBJECTIVE_NOT_COMPLETE"
    assert approved["phase"] == "BLOCKED"
    real = approved["real_execute"]
    assert real["ok"] is True  # execute itself succeeded...
    # Final objective proof now blocks before a WorkHub completion attempt is
    # attached to the result at all.
    assert not real.get("workhub_mark_done")
    assert fake.transitions == [], "WorkHub must never be touched before completion gates pass"


@pytest.mark.asyncio
async def test_mark_workhub_done_proceeds_once_completion_gates_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    approved, fake = await _run_cycle_with_completion_gates(
        monkeypatch,
        completion_gates={
            "passed": True,
            "gates": {"verification_ran": True, "verification_passed": True},
            "reasons": [],
            "verification_verdict": "PASSED",
        },
    )
    real = approved["real_execute"]
    mark = real.get("workhub_mark_done") or {}
    assert mark.get("ok") is True
    assert mark.get("to_status") == "done"
    assert fake.transitions[-1] == "done"


@pytest.mark.asyncio
async def test_proposal_only_result_cannot_mark_workhub_done(monkeypatch: pytest.MonkeyPatch) -> None:
    approved, fake = await _run_cycle_with_completion_gates(
        monkeypatch,
        completion_gates=None,
        objective_status="AWAITING_REVIEW",
        execution_status="NOT_STARTED",
        verification_status="NOT_RUN",
        real_operations=0,
    )

    real = approved["real_execute"]
    assert real["objective_status"] == "AWAITING_REVIEW"
    assert real["real_operations"] == 0
    assert real["workhub_mark_done"]["code"] == "NO_REAL_OPERATION_PERFORMED"
    assert fake.transitions == []
