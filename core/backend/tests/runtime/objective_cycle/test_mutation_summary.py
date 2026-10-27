"""Evidence-derived mutation_summary — never invents files/verdicts."""

from __future__ import annotations

from backend.aix_core.runtime.verification.mutation_summary import build_mutation_summary


def test_mutation_summary_from_real_changed_files() -> None:
    summary = build_mutation_summary(
        real_execute={
            "executor": "objective_cycle.real_execute.source.multistep.v1",
            "changed_source_files": [{"path": "README.md", "additions": 1, "deletions": 1}],
            "verification_status": "PASSED",
            "validation_commands": ["python -m pytest tests/test_x.py -q"],
            "events": [],
        },
        repair_count=0,
        replan_count=0,
    )
    assert summary is not None
    assert summary["files_changed"] == ["README.md"]
    assert summary["files_changed_count"] == 1
    assert summary["executor"].startswith("objective_cycle.real_execute")
    assert summary["validation_commands"] == ["python -m pytest tests/test_x.py -q"]
    assert summary["verifier_verdict"] == "PASS"
    assert summary["repair_count"] == 0
    assert summary["replan_count"] == 0


def test_mutation_summary_two_files_count() -> None:
    summary = build_mutation_summary(
        real_execute={
            "executor": "objective_cycle.real_execute.source.multistep.v1",
            "changed_source_files": [
                {"path": "fixture/config.py"},
                {"path": "tests/test_fixture_config.py"},
            ],
            "verification_status": "PASSED",
        },
        verifier_verdict="PASS",
        repair_count=0,
        replan_count=0,
    )
    assert summary is not None
    assert summary["files_changed_count"] == 2
    assert summary["files_changed"] == [
        "fixture/config.py",
        "tests/test_fixture_config.py",
    ]


def test_mutation_summary_demo_executor_claims_no_repo_files() -> None:
    summary = build_mutation_summary(
        real_execute={
            "executor": "objective_cycle.real_execute.demo.v1",
            "changed_source_files": [{"path": "proposed/index.html"}],
            "verification_status": "PASSED",
        }
    )
    assert summary is not None
    assert summary["files_changed"] == []
    assert summary["files_changed_count"] == 0


def test_mutation_summary_failed_verification_is_not_pass() -> None:
    summary = build_mutation_summary(
        real_execute={
            "executor": "objective_cycle.real_execute.source.multistep.v1",
            "changed_source_files": [{"path": "README.md"}],
            "verification_status": "FAILED",
        },
        verifier_verdict="REPAIR",
        repair_count=1,
        replan_count=0,
    )
    assert summary is not None
    assert summary["verifier_verdict"] == "REPAIR"
    assert summary["repair_count"] == 1


def test_mutation_summary_commands_from_tool_events() -> None:
    summary = build_mutation_summary(
        real_execute={
            "executor": "objective_cycle.real_execute.source.multistep.v1",
            "changed_source_files": [{"path": "a.py"}],
            "verification_status": "PASSED",
        },
        events=[
            {
                "event_type": "tool.command_run",
                "payload_refs": {"command": "python -m pytest tests/test_a.py -q"},
            }
        ],
    )
    assert summary is not None
    assert summary["validation_commands"] == ["python -m pytest tests/test_a.py -q"]


def test_mutation_summary_files_from_patch_events_when_changed_list_empty() -> None:
    summary = build_mutation_summary(
        real_execute={
            "executor": "objective_cycle.real_execute.edit_file.multistep.v1",
            "changed_source_files": [],
            "verification_status": "FAILED",
            "events": [
                {
                    "event_type": "patch.application.completed",
                    "payload_refs": {
                        "path": "backend/tests/aix_core/objective_cycle/live_fixtures/feature_flag.py"
                    },
                }
            ],
        },
        verifier_verdict="FAIL",
        repair_count=0,
    )
    assert summary is not None
    assert summary["files_changed"] == [
        "backend/tests/aix_core/objective_cycle/live_fixtures/feature_flag.py"
    ]


def test_aggregate_unions_failed_attempt_and_repair_success() -> None:
    from backend.aix_core.runtime.objective.durable_api_backend import _aggregate_mutation_summary

    history = [
        {
            "executor": "objective_cycle.real_execute.edit_file.multistep.v1",
            "mutation_summary": {
                "files_changed": [
                    "backend/tests/aix_core/objective_cycle/live_fixtures/feature_flag.py"
                ],
                "files_changed_count": 1,
                "executor": "objective_cycle.real_execute.edit_file.multistep.v1",
                "validation_commands": [],
                "verifier_verdict": "FAIL",
                "repair_count": 0,
                "replan_count": 0,
            },
        }
    ]
    current = {
        "executor": "objective_cycle.real_execute.edit_file.multistep.v1",
        "mutation_summary": {
            "files_changed": [
                "backend/tests/aix_core/objective_cycle/live_fixtures/test_feature_flag.py"
            ],
            "files_changed_count": 1,
            "executor": "objective_cycle.real_execute.edit_file.multistep.v1",
            "validation_commands": ["python -m pytest x -q"],
            "verifier_verdict": "PASS",
            "repair_count": 1,
            "replan_count": 0,
        },
    }
    summary = _aggregate_mutation_summary(history=history, current=current)
    assert summary is not None
    assert summary["files_changed_count"] == 2
    assert set(summary["files_changed"]) == {
        "backend/tests/aix_core/objective_cycle/live_fixtures/feature_flag.py",
        "backend/tests/aix_core/objective_cycle/live_fixtures/test_feature_flag.py",
    }
    assert summary["repair_count"] == 1
    assert summary["verifier_verdict"] == "PASS"


def test_aggregate_prefers_history_when_current_cleared() -> None:
    from backend.aix_core.runtime.objective.durable_api_backend import _aggregate_mutation_summary

    history = [
        {
            "executor": "objective_cycle.real_execute.edit_file.multistep.v1",
            "changed_source_files": [{"path": "README.md"}],
            "mutation_summary": {
                "files_changed": ["README.md"],
                "files_changed_count": 1,
                "executor": "objective_cycle.real_execute.edit_file.multistep.v1",
                "validation_commands": [],
                "verifier_verdict": "PASS",
                "repair_count": 0,
                "replan_count": 0,
            },
        }
    ]
    summary = _aggregate_mutation_summary(history=history, current=None)
    assert summary is not None
    assert summary["files_changed"] == ["README.md"]
    assert summary["files_changed_count"] == 1
    assert summary["verifier_verdict"] == "PASS"
