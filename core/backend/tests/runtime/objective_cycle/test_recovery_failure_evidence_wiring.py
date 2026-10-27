from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.runtime.executor.real_execute import RealExecuteError
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


@pytest.mark.asyncio
async def test_typed_executor_failure_is_queryable_before_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _skip_exploration(**kwargs: Any) -> None:
        return None

    async def _empty_edit(**kwargs: Any) -> None:
        raise RealExecuteError(
            "MODEL_RETURNED_NO_EDIT",
            "model returned an empty edits array",
            events=[
                {
                    "event_type": "tool.file_read",
                    "payload_refs": {"path": "service.py", "artifact_ref": "evidence://raw-model"},
                }
            ],
            raw_model_payload={"edits": [], "reason": "empty test double"},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", _empty_edit,
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False,
    )
    monkeypatch.setattr(backend, "_run_exploration_gate", _skip_exploration)
    admitted = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={
            "workhub_task_id": "failure-evidence-1",
            "objective": "Update service.py with a real edit",
            "task_class": "single_file_helper",
            "definition_of_done": ("service.py changes",),
            "repository_id": "aix-frontend",
            "allowed_files": ("service.py",),
        },
        idempotency_key="admit-failure-evidence", expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1",
        resource_id=admitted["objective_id"], body={"candidate_sha": "abc1234"},
        idempotency_key="start-failure-evidence", expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1",
        resource_id=started["cycle_id"], body={"approval_reference": "workhub://approvals/test"},
        idempotency_key="approve-failure-evidence", expected_version=started["version"],
    )
    assert approved["ok"] is False
    assert approved["phase"] == "BLOCKED"
    evidence = await backend.query(
        operation="get_evidence", tenant_id="tenant-1", resource_id=started["cycle_id"],
        child_id=None, filters={},
    )
    assert evidence["failure_evidence"][0]["failure_type"] == "MODEL_RETURNED_NO_EDIT"
    assert evidence["failure_evidence"][0]["changed_files"] == ["service.py"]
    assert evidence["repair_hypotheses"][0]["root_cause_evidence_refs"]
    assert evidence["failure_artifacts"][0]["payload"]["raw_model_payload"] == {
        "edits": [],
        "reason": "empty test double",
    }
    assert any(event["event_type"] == "evidence.failure_recorded" for event in evidence["events"])
