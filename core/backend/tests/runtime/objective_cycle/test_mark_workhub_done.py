"""001P-W13: mark WorkHub task done after successful real execute."""

from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.objective.mark_workhub_done import MarkWorkHubDoneError, mark_workhub_task_done
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult


class _FakeClient:
    def __init__(self, *, status: str = "queued") -> None:
        self.status = status
        self.transitions: list[str] = []
        self.evidence_calls = 0
        self.verify_calls = 0

    async def get_task(self, **kwargs: Any) -> dict[str, Any]:
        return {"id": kwargs["task_id"], "status": self.status, "project_id": kwargs["project_id"]}

    async def transition_task(self, **kwargs: Any) -> dict[str, Any]:
        self.status = str(kwargs["status_value"])
        self.transitions.append(self.status)
        return {"id": kwargs["task_id"], "status": self.status}

    async def add_task_evidence(self, **kwargs: Any) -> dict[str, Any]:
        self.evidence_calls += 1
        return {"id": "ev-1", "task_id": kwargs["task_id"]}

    async def evaluate_task_verification(self, **kwargs: Any) -> dict[str, Any]:
        self.verify_calls += 1
        return {"decision": "pass", "eligible_for_done": True}


@pytest.mark.asyncio
async def test_mark_workhub_task_done_walks_legal_path() -> None:
    client = _FakeClient(status="queued")
    result = await mark_workhub_task_done(
        client=client,  # type: ignore[arg-type]
        project_id="proj-1",
        task_id="task-1",
        cycle_id="cyc_1",
        evidence_ref="file://workspace/evidence/execution_report.json",
        artifact_ref="file://workspace/proposed/index.html",
    )
    assert result.ok is True
    assert result.code == "MARKED_DONE"
    assert result.from_status == "queued"
    assert result.to_status == "done"
    assert client.transitions == ["running", "needs_review", "done"]
    assert client.evidence_calls == 1
    assert client.verify_calls == 1
    assert "transition:done" in result.steps


@pytest.mark.asyncio
async def test_mark_workhub_task_done_noop_when_already_done() -> None:
    client = _FakeClient(status="done")
    result = await mark_workhub_task_done(
        client=client,  # type: ignore[arg-type]
        project_id="proj-1",
        task_id="task-1",
        cycle_id="cyc_1",
        evidence_ref="file://x",
    )
    assert result.code == "ALREADY_DONE"
    assert client.transitions == []
    assert client.evidence_calls == 0


@pytest.mark.asyncio
async def test_backend_marks_done_after_real_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(status="queued")

    async def _run_real_execute(**kwargs: Any) -> RealExecuteResult:
        return RealExecuteResult(
            ok=True,
            code="REAL_EXECUTE_OK",
            workspace=r"D:\tmp\ws",
            evidence_refs=["file://ws/evidence/execution_report.json"],
            workhub_loaded=True,
            artifacts={
                "execution_report": "file://ws/evidence/execution_report.json",
                "proposed_html": "file://ws/proposed/index.html",
            },
            execution_status="COMPLETED",
            verification_status="PASSED",
            objective_status="COMPLETED",
            real_operations=1,
            target_files_modified=1,
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        _run_real_execute,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.build_workhub_client",
        lambda _settings=None, **_kwargs: fake,
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        create_workhub_tasks=False,
        mark_workhub_done=True,
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "task-101",
            "objective": "Create login page",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix-frontend",
                "allowed_files": ("src/login.tsx",),
            "project_id": "proj-1",
            "task_number": 101,
        },
        idempotency_key="admit-1",
        expected_version=None,
    )
    assert admit["ok"] is True
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-1",
        expected_version=None,
    )
    assert started["ok"] is True
    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/local-test"},
        idempotency_key="approve-1",
        expected_version=started["version"],
    )
    assert approved["ok"] is True, approved
    assert approved["phase"] == "COMPLETED_FOR_REVIEW"
    real = approved["real_execute"]
    assert real is not None
    mark = real.get("workhub_mark_done") or {}
    assert mark.get("ok") is True
    assert mark.get("to_status") == "done"
    assert mark.get("exception") == "001P-W13"
    assert fake.transitions[-1] == "done"
    assert approved.get("aix_marks_workhub_done") is True
    assert approved.get("exception") == "001P-W13"
    assert approved.get("workhub_lifecycle_authority") is True


@pytest.mark.asyncio
async def test_mark_done_failure_fail_closes_without_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """001P-W13 / AIX-CH-001: close failure fail-closes cycle control.

    Mutation artifacts stay. The command does not raise (resume must not 500).
    """

    async def _run_real_execute(**kwargs: Any) -> RealExecuteResult:
        return RealExecuteResult(
            ok=True,
            code="REAL_EXECUTE_OK",
            workspace=r"D:\tmp\ws",
            evidence_refs=["file://ws/evidence/execution_report.json"],
            artifacts={"execution_report": "file://ws/evidence/execution_report.json"},
            execution_status="COMPLETED",
            verification_status="PASSED",
            objective_status="COMPLETED",
            real_operations=1,
            target_files_modified=1,
        )

    async def _boom(**kwargs: Any):
        raise MarkWorkHubDoneError("DONE_TRANSITION_FAILED", "nope")

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        _run_real_execute,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.mark_workhub_task_done",
        _boom,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.build_workhub_client",
        lambda _settings=None, **_kwargs: object(),
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
            "workhub_task_id": "task-101",
            "objective": "Create login page",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix-frontend",
                "allowed_files": ("src/login.tsx",),
            "project_id": "proj-1",
        },
        idempotency_key="admit-2",
        expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-2",
        expected_version=None,
    )
    control = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/local-test"},
        idempotency_key="approve-2",
        expected_version=started["version"],
    )
    assert control["ok"] is False
    assert control["phase"] == "BLOCKED"
    assert control["terminal_reason"] == "DONE_TRANSITION_FAILED"
    assert control["error"]["code"] == "DONE_TRANSITION_FAILED"
    assert control["error"]["detail"] == "nope"
    real = control["real_execute"] or {}
    assert real["ok"] is True
    mark = real.get("workhub_mark_done") or {}
    assert mark["ok"] is False
    assert mark["code"] == "DONE_TRANSITION_FAILED"
    assert mark["detail"] == "nope"
    assert mark["exception"] == "001P-W13"

    events = await backend.query(
        operation="get_events",
        tenant_id="tenant-1",
        resource_id=started["cycle_id"],
        child_id=None,
        filters={},
    )
    kinds = {
        str(item.get("event_type") or item.get("event") or "")
        for item in (events.get("events") or [])
    }
    assert "cycle.mark_workhub_done_failed" in kinds

    snapshot = await backend.query(
        operation="get_cycle",
        tenant_id="tenant-1",
        resource_id=started["cycle_id"],
        child_id=None,
        filters={},
    )
    assert snapshot["ok"] is True
    item = snapshot["item"]
    assert item["phase"] == "BLOCKED"
    assert item["terminal_reason"] == "DONE_TRANSITION_FAILED"
    assert (item.get("real_execute") or {}).get("ok") is True
    assert (item.get("real_execute") or {}).get("workhub_mark_done", {}).get("ok") is False

    resumed = await backend.command(
        operation="resume_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"reason": "retry-close"},
        idempotency_key="resume-2",
        expected_version=None,
    )
    assert resumed["ok"] is False
    assert resumed["phase"] == "BLOCKED"
    assert resumed["error"]["code"] == "DONE_TRANSITION_FAILED"
    assert (resumed.get("real_execute") or {}).get("ok") is True


@pytest.mark.asyncio
async def test_mark_done_skipped_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(status="queued")

    async def _run_real_execute(**kwargs: Any) -> RealExecuteResult:
        return RealExecuteResult(
            ok=True,
            code="REAL_EXECUTE_OK",
            workspace=r"D:\tmp\ws",
            evidence_refs=["file://ws/evidence/execution_report.json"],
            artifacts={"execution_report": "file://ws/evidence/execution_report.json"},
            execution_status="COMPLETED",
            verification_status="PASSED",
            objective_status="COMPLETED",
            real_operations=1,
            target_files_modified=1,
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        _run_real_execute,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.build_workhub_client",
        lambda _settings=None, **_kwargs: fake,
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        mark_workhub_done=False,
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "task-101",
            "objective": "Create login page",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix-frontend",
            "allowed_files": ("src/login.tsx",),
            "project_id": "proj-1",
        },
        idempotency_key="admit-off",
        expected_version=None,
    )
    assert admit.get("workhub_lifecycle_authority") is False
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-off",
        expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/local-test"},
        idempotency_key="approve-off",
        expected_version=started["version"],
    )
    assert approved["ok"] is True
    assert approved.get("workhub_lifecycle_authority") is False
    assert approved.get("aix_marks_workhub_done") is False
    real = approved.get("real_execute") or {}
    assert real.get("ok") is True
    assert not real.get("workhub_mark_done")
    assert fake.transitions == []

