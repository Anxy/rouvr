from __future__ import annotations

from backend.services.remediation_control_layer import (
    build_defect_fingerprint,
    build_family_blocker_ledger,
    child_task_contract_validator,
    default_remediation_policy,
    should_create_remedial_task,
    stamp_remediation_metadata,
)


class _FakeTaskClient:
    def __init__(self, rows_by_status: dict[str, list[dict]] | None = None) -> None:
        self.rows_by_status = rows_by_status or {}

    def _list_tasks(self, *, project_id: str, limit: int, status: str | None = None):
        return list(self.rows_by_status.get(str(status or ""), []))


def _candidate_metadata(*, target_files: list[str] | None = None, task_intent: str = "mutation_required") -> dict:
    return {
        "task_intent": task_intent,
        "target_files": list(target_files or ["backend/example.py"]),
        "validation_commands": ["python -m pytest backend/tests/test_example.py -q"],
    }


def test_should_create_remedial_task_allows_valid_contract_when_family_is_clear() -> None:
    fingerprint = build_defect_fingerprint(
        family_root_task_number=101,
        family_key="proj:101",
        defect_class="recovery:test_failure",
        reason_code="TEST_FAILURE",
        phase="verifier",
        title="Recovery: #101 TEST_FAILURE",
    )
    ledger = build_family_blocker_ledger(
        task_client=_FakeTaskClient(),
        project_id="proj",
        family_root_task_number=101,
        family_key="proj:101",
    )
    contract_validation = child_task_contract_validator(
        title="Recovery: #101 TEST_FAILURE",
        description="Recover bounded test failure.",
        metadata_json=_candidate_metadata(),
    )
    decision = should_create_remedial_task(
        fingerprint=fingerprint,
        contract_validation=contract_validation,
        ledger=ledger,
        policy=default_remediation_policy(),
    )
    assert decision["action"] == "create"
    assert decision["audit_event"] == "child_created"


def test_should_create_remedial_task_attaches_to_equivalent_open_blocker() -> None:
    fingerprint = build_defect_fingerprint(
        family_root_task_number=101,
        family_key="proj:101",
        defect_class="recovery:test_failure",
        reason_code="TEST_FAILURE",
        phase="verifier",
        title="Recovery: #101 TEST_FAILURE",
    )
    existing_metadata = stamp_remediation_metadata(
        metadata_json=_candidate_metadata(),
        fingerprint=fingerprint,
        policy=default_remediation_policy(),
        event_name="child_created",
    )
    ledger = build_family_blocker_ledger(
        task_client=_FakeTaskClient(
            {
                "queued": [
                    {
                        "id": "task-1",
                        "task_number": 9001,
                        "status": "queued",
                        "title": "Recovery: #101 TEST_FAILURE",
                        "metadata_json": existing_metadata,
                    }
                ]
            }
        ),
        project_id="proj",
        family_root_task_number=101,
        family_key="proj:101",
    )
    decision = should_create_remedial_task(
        fingerprint=fingerprint,
        contract_validation=child_task_contract_validator(
            title="Recovery: #101 TEST_FAILURE",
            description="Recover bounded test failure.",
            metadata_json=_candidate_metadata(),
        ),
        ledger=ledger,
        policy=default_remediation_policy(),
    )
    assert decision["action"] == "attach_existing"
    assert decision["audit_event"] == "attached_to_existing_blocker"
    assert decision["existing_blocker"].task_number == 9001


def test_should_create_remedial_task_suppresses_when_open_budget_is_exhausted() -> None:
    policy = default_remediation_policy()
    existing_fingerprint = build_defect_fingerprint(
        family_root_task_number=101,
        family_key="proj:101",
        defect_class="admission_decomposition:impl",
        reason_code="TASK_NOT_PLANNER_RUNNABLE",
        phase="admission",
        title="Child impl: #101 old slice",
    )
    ledger = build_family_blocker_ledger(
        task_client=_FakeTaskClient(
            {
                "running": [
                    {
                        "id": "task-2",
                        "task_number": 9002,
                        "status": "running",
                        "title": "Child impl: #101 old slice",
                        "metadata_json": stamp_remediation_metadata(
                            metadata_json=_candidate_metadata(),
                            fingerprint=existing_fingerprint,
                            policy=policy,
                            event_name="child_created",
                        ),
                    }
                ]
            }
        ),
        project_id="proj",
        family_root_task_number=101,
        family_key="proj:101",
    )
    new_fingerprint = build_defect_fingerprint(
        family_root_task_number=101,
        family_key="proj:101",
        defect_class="admission_decomposition:impl",
        reason_code="TASK_NOT_PLANNER_RUNNABLE",
        phase="admission",
        title="Child impl: #101 fresh slice",
    )
    decision = should_create_remedial_task(
        fingerprint=new_fingerprint,
        contract_validation=child_task_contract_validator(
            title="Child impl: #101 fresh slice",
            description="Fresh bounded child slice.",
            metadata_json=_candidate_metadata(),
        ),
        ledger=ledger,
        policy=policy,
    )
    assert decision["action"] == "suppress"
    assert decision["audit_event"] == "child_suppressed_budget"
    assert decision["reason_code"] == "REMEDIATION_OPEN_CHILD_BUDGET_EXHAUSTED"


def test_should_create_remedial_task_suppresses_on_circuit_breaker() -> None:
    policy = default_remediation_policy()
    fingerprint = build_defect_fingerprint(
        family_root_task_number=101,
        family_key="proj:101",
        defect_class="system_issue:planner_validation_command_missing",
        reason_code="PLANNER_VALIDATION_COMMAND_MISSING",
        phase="planner",
        title="System issue: PLANNER_VALIDATION_COMMAND_MISSING on task #101",
    )
    stamped = stamp_remediation_metadata(
        metadata_json=_candidate_metadata(task_intent="system_issue_auto_submit"),
        fingerprint=fingerprint,
        policy=policy,
        event_name="child_created",
    )
    ledger = build_family_blocker_ledger(
        task_client=_FakeTaskClient(
            {
                "done": [
                    {
                        "id": "task-3",
                        "task_number": 9003,
                        "status": "done",
                        "title": fingerprint.title_key,
                        "metadata_json": stamped,
                    },
                    {
                        "id": "task-4",
                        "task_number": 9004,
                        "status": "done",
                        "title": fingerprint.title_key,
                        "metadata_json": stamped,
                    },
                ]
            }
        ),
        project_id="proj",
        family_root_task_number=101,
        family_key="proj:101",
    )
    decision = should_create_remedial_task(
        fingerprint=fingerprint,
        contract_validation=child_task_contract_validator(
            title="System issue: PLANNER_VALIDATION_COMMAND_MISSING on task #101",
            description="Auto-created system issue.",
            metadata_json=_candidate_metadata(task_intent="system_issue_auto_submit"),
        ),
        ledger=ledger,
        policy=policy,
    )
    assert decision["action"] == "suppress"
    assert decision["audit_event"] == "child_suppressed_circuit_breaker"
    assert decision["reason_code"] == "REMEDIATION_CIRCUIT_BREAKER_OPEN"


def test_child_task_contract_validator_rejects_missing_target_files() -> None:
    validation = child_task_contract_validator(
        title="Recovery: #101 TEST_FAILURE",
        description="Recover bounded test failure.",
        metadata_json={"task_intent": "mutation_required", "validation_commands": ["pytest -q"]},
    )
    assert validation["valid"] is False
    assert "target_files" in validation["missing_fields"]


def test_child_task_contract_validator_does_not_treat_validation_plan_as_executable_commands() -> None:
    validation = child_task_contract_validator(
        title="Recovery: #101 TEST_FAILURE",
        description="Recover bounded test failure.",
        metadata_json={
            "task_intent": "mutation_required",
            "target_files": ["backend/example.py"],
            "ticket_contract_current": {
                "validation_plan": ["attach evidence note only"],
                "execution_slice": {"validation_commands": []},
            },
        },
    )
    assert validation["valid"] is False
    assert "validation_commands" in validation["missing_fields"]
    assert validation["validation_commands"] == []
