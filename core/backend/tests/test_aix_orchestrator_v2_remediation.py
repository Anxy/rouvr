from __future__ import annotations

import pytest

from backend.aix_orchestrator_v2.models import AixOrchestratorV2RunState, ExecutionResult
from backend.aix_orchestrator_v2.service import AixOrchestratorV2Service
from backend.services.remediation_control_layer import (
    build_defect_fingerprint,
    default_remediation_policy,
    stamp_remediation_metadata,
)


class _FakeWorkHub:
    def __init__(self, rows_by_status: dict[str, list[dict]] | None = None) -> None:
        self.rows_by_status = rows_by_status or {}

    def _list_tasks(self, *, project_id: str, limit: int, status: str | None = None) -> list[dict]:  # noqa: ARG002
        return list(self.rows_by_status.get(str(status or ""), []))

    async def list_tasks(self, *, project_id: str, limit: int, status: str | None = None) -> list[dict]:  # noqa: ARG002
        return list(self.rows_by_status.get(str(status or ""), []))


def _state() -> AixOrchestratorV2RunState:
    return AixOrchestratorV2RunState(
        task_id="task-2754",
        run_id="aixv2-2754",
        task_payload={
            "task_number": 2754,
            "title": "Activate remediation controls",
            "metadata_json": {"parent_task_number": 2701},
        },
        packet={"task_ref": {"project_id": "bh-migrated-7-aix", "task_number": 2754}},
        recon={
            "status": "ready",
            "entity_query": "backend/aix_orchestrator_v2/service.py",
            "execution_contract": {},
            "codeintel": {},
            "target_file": "backend/aix_orchestrator_v2/service.py",
            "target_files": ["backend/aix_orchestrator_v2/service.py"],
            "anchor": {},
            "symbol_ranges": [],
            "anchor_ranges": [],
            "validation_target": "pytest backend/tests/test_aix_orchestrator_v2_remediation.py -q",
            "related_tests": [],
            "dependency_context": {},
            "slim_packet_inputs": {"task_number": 2754},
        },
        validator_ledger={"last_validated_sha": {}, "events": []},
    )


def test_apply_remediation_control_attaches_to_equivalent_open_blocker() -> None:
    service = AixOrchestratorV2Service(workhub_client=_FakeWorkHub())
    state = _state()
    fingerprint = build_defect_fingerprint(
        family_root_task_number=2701,
        family_key="bh-migrated-7-aix:2701",
        defect_class="plan_invalid",
        reason_code="PLANNER_BLOCKED",
        phase="planner",
        title="AIX v2 remediation: PLANNER_BLOCKED family #2701",
        target_files=["backend/aix_orchestrator_v2/service.py"],
    )
    existing_metadata = stamp_remediation_metadata(
        metadata_json={
            "task_intent": "system_issue_auto_submit",
            "target_files": ["backend/aix_orchestrator_v2/service.py"],
            "validation_commands": ["pytest backend/tests/test_aix_orchestrator_v2_remediation.py -q"],
            "parent_task_number": 2701,
        },
        fingerprint=fingerprint,
        policy=default_remediation_policy(),
        event_name="child_created",
    )
    service._workhub = _FakeWorkHub(  # noqa: SLF001
        {
            "running": [
                {
                    "id": "task-existing",
                    "task_number": 9911,
                    "status": "running",
                    "title": "AIX v2 remediation: PLANNER_BLOCKED family #2701",
                    "metadata_json": existing_metadata,
                }
            ]
        }
    )
    state.fail(
        classification="plan_invalid",
        phase="planner",
        code="PLANNER_BLOCKED",
        message="planner blocked",
        status="blocked",
    )

    payload = service._apply_remediation_control(state)  # noqa: SLF001

    assert payload is not None
    assert payload["effective_action"] == "attach_existing"
    assert payload["effective_reason_code"] == "EQUIVALENT_OPEN_BLOCKER_EXISTS"
    assert payload["existing_blocker"]["task_number"] == 9911
    assert state.artifacts[-1].name == "remediation_control"
    assert state.validator_ledger["events"][-1]["event"] == "remediation_control_decision"


def test_apply_remediation_control_suppresses_when_circuit_breaker_is_open() -> None:
    service = AixOrchestratorV2Service(workhub_client=_FakeWorkHub())
    defect_class = service._failure_classification(  # noqa: SLF001
        phase="execution",
        status="failed",
        code="HOST_CHECKOUT_DIRTY_FOR_EXECUTION",
    )
    fingerprint = build_defect_fingerprint(
        family_root_task_number=2701,
        family_key="bh-migrated-7-aix:2701",
        defect_class=defect_class,
        reason_code="HOST_CHECKOUT_DIRTY_FOR_EXECUTION",
        phase="execution",
        title="AIX v2 remediation: HOST_CHECKOUT_DIRTY_FOR_EXECUTION family #2701",
        target_files=["backend/aix_orchestrator_v2/service.py"],
    )
    stamped = stamp_remediation_metadata(
        metadata_json={
            "task_intent": "system_issue_auto_submit",
            "target_files": ["backend/aix_orchestrator_v2/service.py"],
            "validation_commands": ["pytest backend/tests/test_aix_orchestrator_v2_remediation.py -q"],
            "parent_task_number": 2701,
        },
        fingerprint=fingerprint,
        policy=default_remediation_policy(),
        event_name="child_created",
    )
    service = AixOrchestratorV2Service(
        workhub_client=_FakeWorkHub(
            {
                "done": [
                    {
                        "id": "task-done-1",
                        "task_number": 9912,
                        "status": "done",
                        "title": "AIX v2 remediation: HOST_CHECKOUT_DIRTY_FOR_EXECUTION family #2701",
                        "metadata_json": stamped,
                    },
                    {
                        "id": "task-done-2",
                        "task_number": 9913,
                        "status": "done",
                        "title": "AIX v2 remediation: HOST_CHECKOUT_DIRTY_FOR_EXECUTION family #2701",
                        "metadata_json": stamped,
                    },
                ]
            }
        )
    )
    state = _state()
    state.execution = ExecutionResult(
        status="failed",
        run_result={},
        failure={
            "code": "HOST_CHECKOUT_DIRTY_FOR_EXECUTION",
            "message": "workspace checkout leaked changes",
        },
    )

    payload = service._apply_remediation_control(state)  # noqa: SLF001

    assert payload is not None
    assert payload["raw_decision"]["action"] == "suppress"
    assert payload["effective_action"] == "suppress"
    assert payload["effective_reason_code"] == "REMEDIATION_CIRCUIT_BREAKER_OPEN"
    assert payload["ledger_summary"]["history_count"] == 2


@pytest.mark.asyncio
async def test_apply_remediation_control_async_attaches_to_equivalent_open_blocker() -> None:
    service = AixOrchestratorV2Service(workhub_client=_FakeWorkHub())
    state = _state()
    fingerprint = build_defect_fingerprint(
        family_root_task_number=2701,
        family_key="bh-migrated-7-aix:2701",
        defect_class="plan_invalid",
        reason_code="PLANNER_BLOCKED",
        phase="planner",
        title="AIX v2 remediation: PLANNER_BLOCKED family #2701",
        target_files=["backend/aix_orchestrator_v2/service.py"],
    )
    existing_metadata = stamp_remediation_metadata(
        metadata_json={
            "task_intent": "system_issue_auto_submit",
            "target_files": ["backend/aix_orchestrator_v2/service.py"],
            "validation_commands": ["pytest backend/tests/test_aix_orchestrator_v2_remediation.py -q"],
            "parent_task_number": 2701,
        },
        fingerprint=fingerprint,
        policy=default_remediation_policy(),
        event_name="child_created",
    )
    service._workhub = _FakeWorkHub(  # noqa: SLF001
        {
            "running": [
                {
                    "id": "task-existing",
                    "task_number": 9911,
                    "status": "running",
                    "title": "AIX v2 remediation: PLANNER_BLOCKED family #2701",
                    "metadata_json": existing_metadata,
                }
            ]
        }
    )
    state.fail(
        classification="plan_invalid",
        phase="planner",
        code="PLANNER_BLOCKED",
        message="planner blocked",
        status="blocked",
    )

    payload = await service._apply_remediation_control_async(state)  # noqa: SLF001

    assert payload is not None
    assert payload["effective_action"] == "attach_existing"
    assert payload["effective_reason_code"] == "EQUIVALENT_OPEN_BLOCKER_EXISTS"
    assert payload["existing_blocker"]["task_number"] == 9911
