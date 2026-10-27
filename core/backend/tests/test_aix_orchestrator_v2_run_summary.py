from __future__ import annotations

from backend.aix_orchestrator_v2.run_summary import build_run_summary, classify_phase_failure, classify_run_bucket


def test_classify_run_bucket_maps_success_and_planning_blocked() -> None:
    assert (
        classify_run_bucket(
            {
                "status": "writeback_complete",
                "verification": {"classification": "success"},
            }
        )
        == "success"
    )
    assert (
        classify_run_bucket(
            {
                "status": "blocked",
                "failure": {"phase": "planner"},
                "verification": {"classification": "blocked"},
            }
        )
        == "planning_blocked"
    )


def test_build_run_summary_reports_missing_execution_artifacts_when_execution_absent() -> None:
    summary = build_run_summary(
        {
            "task_id": "task-1",
            "run_id": "aixv2-test",
            "status": "blocked",
            "current_phase": "writeback",
            "failure": {"phase": "planner", "message": "planner blocked"},
            "verification": {"classification": "blocked", "summary": "planner blocked"},
            "artifacts": [
                {"name": "recon"},
                {"name": "planner_input_summary"},
                {"name": "planner_raw"},
                {"name": "normalized_plan"},
                {"name": "verifier_result"},
                {"name": "writeback"},
            ],
        }
    )

    assert summary["classification"] == "planning_blocked"
    assert summary["final_phase"] == "writeback"
    assert {"artifact": "execution_summary", "reason": "execution did not run"} in summary["artifact_gaps"]
    assert {"artifact": "diff_refs", "reason": "execution did not run"} in summary["artifact_gaps"]


def test_build_run_summary_reports_execution_artifacts_for_success() -> None:
    summary = build_run_summary(
        {
            "task_id": "task-2",
            "run_id": "aixv2-success",
            "status": "writeback_complete",
            "current_phase": "writeback",
            "verification": {"classification": "success", "summary": "ok"},
            "execution": {
                "changed_files": ["backend/services/example.py"],
                "commands": [{"command_text": "pytest tests/test_example.py -q"}],
                "diff_refs": ["local://diff"],
            },
            "artifacts": [
                {"name": "execution_summary"},
                {"name": "recon"},
                {"name": "planner_input_summary"},
                {"name": "planner_raw"},
                {"name": "normalized_plan"},
                {"name": "verifier_result"},
                {"name": "writeback"},
                {"name": "changed_files"},
                {"name": "diff_refs", "ref": "local://diff"},
            ],
        }
    )

    assert summary["classification"] == "success"
    assert summary["changed_files"] == ["backend/services/example.py"]
    assert summary["commands_run"] == ["pytest tests/test_example.py -q"]
    assert summary["artifact_refs"] == ["local://diff"]
    assert summary["artifact_gaps"] == []


def test_build_run_summary_reports_missing_persisted_execution_artifacts_on_success() -> None:
    summary = build_run_summary(
        {
            "task_id": "task-3",
            "run_id": "aixv2-success-missing-artifacts",
            "status": "writeback_complete",
            "current_phase": "writeback",
            "verification": {"classification": "success", "summary": "ok"},
            "execution": {
                "changed_files": ["backend/services/example.py"],
                "commands": [{"command_text": "pytest tests/test_example.py -q"}],
                "diff_refs": ["local://diff"],
            },
            "artifacts": [
                {"name": "recon"},
                {"name": "planner_input_summary"},
                {"name": "planner_raw"},
                {"name": "normalized_plan"},
                {"name": "verifier_result"},
                {"name": "writeback"},
            ],
        }
    )

    assert summary["classification"] == "success"
    assert summary["artifact_gaps"] == [
        {
            "artifact": "execution_summary",
            "reason": "execution ran but summary artifact was not persisted",
        },
        {
            "artifact": "changed_files",
            "reason": "execution changed files but changed_files artifact was not persisted",
        },
        {
            "artifact": "diff_refs",
            "reason": "execution produced diff refs but diff_refs artifact was not persisted",
        },
    ]


def test_build_run_summary_reports_packet_audit_and_runtime_authority() -> None:
    summary = build_run_summary(
        {
            "task_id": "task-4",
            "run_id": "aixv2-packet-audit",
            "status": "writeback_complete",
            "current_phase": "writeback",
            "packet": {"slim_packet": {"task_number": 2751, "target_file": "backend/a.py"}},
            "recon": {"slim_packet_inputs": {"task_number": 2751, "target_file": "backend/a.py"}},
            "plan": {
                "planner_packet": {
                    "target_file": "backend/a.py",
                    "target_files": ["backend/a.py"],
                    "validation_command": "pytest tests/test_a.py -q",
                    "tool_policy": {"allowed_mutation_tools": ["read_range", "edit_file", "cmd.run"]},
                }
            },
            "verification": {"classification": "success", "summary": "ok"},
            "execution": {
                "changed_files": ["backend/a.py"],
                "commands": [{"command_text": "pytest tests/test_a.py -q"}],
                "diff_refs": ["local://diff"],
                "envelope": {
                    "envelope": {
                        "data": {
                            "execution": {
                                "slim_packet": {"task_number": 2751, "target_file": "backend/a.py"},
                                "telemetry": {"slim_packet": {"task_number": 2751, "target_file": "backend/a.py"}},
                            }
                        }
                    }
                },
            },
            "artifacts": [
                {"name": "execution_summary"},
                {"name": "recon"},
                {"name": "planner_input_summary"},
                {"name": "planner_raw"},
                {"name": "normalized_plan"},
                {"name": "verifier_result"},
                {"name": "writeback"},
                {"name": "changed_files"},
                {"name": "diff_refs", "ref": "local://diff"},
            ],
        }
    )

    audit = summary["packet_audit"]
    assert audit["runtime_authority"]["create_matches_dispatch"] is True
    assert audit["runtime_authority"]["dispatch_matches_execute"] is True
    assert audit["runtime_authority"]["live_slim_packet_used"] is True
    assert audit["duplicate_sections"] == [
        {
            "path": "execution.telemetry.slim_packet",
            "matches": "execution.slim_packet",
            "reason": "runtime telemetry duplicates dispatched slim packet",
        }
    ]
    assert audit["planner_packet_metrics"]["validation_command_count"] == 1
    assert audit["transitions"][0]["removed_fields"] == []


def test_build_run_summary_includes_latest_remediation_control() -> None:
    summary = build_run_summary(
        {
            "task_id": "task-5",
            "run_id": "aixv2-remediation",
            "status": "failed",
            "current_phase": "verification",
            "failure": {"phase": "verification", "message": "failed"},
            "verification": {"classification": "failed", "summary": "failed"},
            "artifacts": [
                {"name": "recon"},
                {"name": "planner_input_summary"},
                {"name": "planner_raw"},
                {"name": "normalized_plan"},
                {"name": "verifier_result"},
                {"name": "writeback"},
                {
                    "name": "remediation_control",
                    "payload": {
                        "effective_action": "attach_existing",
                        "effective_reason_code": "EQUIVALENT_OPEN_BLOCKER_EXISTS",
                        "existing_blocker": {"task_number": 9911},
                    },
                },
            ],
        }
    )

    assert summary["remediation_control"]["effective_action"] == "attach_existing"
    assert summary["remediation_control"]["existing_blocker"]["task_number"] == 9911


def test_classify_phase_failure_maps_minimum_sprint2_failure_classes() -> None:
    assert classify_phase_failure({"failure": {"phase": "recon"}}) == "recon_failed"
    assert classify_phase_failure({"failure": {"phase": "planner"}}) == "planning_failed"
    assert (
        classify_phase_failure(
            {
                "failure": {"phase": "planner"},
                "plan": {"validation_failures": ["RECON_TARGET_FILE_REQUIRED"]},
            }
        )
        == "plan_invalid"
    )
    assert classify_phase_failure({"failure": {"phase": "execution"}}) == "execution_failed"
    assert classify_phase_failure({"failure": {"phase": "verification"}}) == "verification_failed"
    assert classify_phase_failure({"failure": {"phase": "writeback"}}) == "writeback_failed"
    assert (
        classify_phase_failure({"failure": {"classification": "blocked_needs_human", "phase": "planner"}})
        == "blocked_needs_human"
    )
