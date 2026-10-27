from backend.aix_core.runtime.planner.plan_validation import (
    validate_executable_plan,
    validate_plan_capabilities,
)


def test_execution_objective_rejects_proposal_only_plan() -> None:
    result = validate_executable_plan(
        [
            {"category": "REPORTING", "action": "bounded_propose_artifact"},
            {"category": "GOVERNANCE", "action": "human_review"},
        ],
        execution_required=True,
        target_resource=r"D:\repo\.env",
    )

    assert result.valid is False
    assert result.reason_code == "NO_EXECUTABLE_STEPS"


def test_unrelated_executable_step_does_not_satisfy_target_binding() -> None:
    result = validate_executable_plan(
        [
            {
                "category": "EXECUTABLE",
                "action": "shell.execute",
                "target_resource": "unrelated",
            }
        ],
        execution_required=True,
        target_resource=r"D:\repo\.env",
    )

    assert result.valid is False
    assert result.reason_code == "NO_TARGET_RESOURCE_ACCESSED"


def test_target_bound_executable_step_passes() -> None:
    target = r"D:\repo\.env"
    result = validate_executable_plan(
        [{"category": "EXECUTABLE", "action": "filesystem.read_file", "target_resource": target}],
        execution_required=True,
        target_resource=target,
    )

    assert result.valid is True
    assert result.reason_code is None


def test_registered_capability_plan_is_valid() -> None:
    result = validate_plan_capabilities(
        [
            {"capability": "filesystem.exists"},
            {"capability": "filesystem.stat"},
            {"capability": "filesystem.read_file"},
        ]
    )
    assert result.valid is True


def test_unregistered_capability_is_rejected() -> None:
    result = validate_plan_capabilities(
        [
            {"capability": "filesystem.exists"},
            {"capability": "shell.execute_arbitrary"},
        ]
    )
    assert result.valid is False
    assert result.reason_code == "CAPABILITY_NOT_REGISTERED"


def test_action_field_is_checked_when_capability_is_absent() -> None:
    result = validate_plan_capabilities([{"action": "network.fetch_url"}])
    assert result.valid is False
    assert result.reason_code == "CAPABILITY_NOT_REGISTERED"


def test_empty_plan_has_no_capability_violation() -> None:
    assert validate_plan_capabilities([]).valid is True
