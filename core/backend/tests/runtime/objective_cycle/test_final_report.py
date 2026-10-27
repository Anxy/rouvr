"""AIX-REAL-EXECUTION-005 Workstream K: final execution report."""

from __future__ import annotations

from backend.aix_core.runtime.executor.final_report import build_final_report


def test_completed_read_report_matches_canonical_shape() -> None:
    real_execute = {
        "objective_status": "COMPLETED",
        "execution_status": "COMPLETED",
        "verification_status": "PASSED",
        "real_operations": 1,
        "target_files_read": 1,
        "target_files_modified": 0,
        "detail": "real file read: .env (120 bytes)",
        "artifacts": {"objective": "x", "workhub_load": "y", "plan": "z", "file_read": "w"},
        "read_verification": {
            "resource_resolved": True,
            "file_read_non_empty": True,
            "target_resource_accessed": True,
            "secret_values_redacted": True,
        },
        "read_file": {"resource": ".env", "bytes_read": 120, "line_count": 6},
        "reason_code": None,
    }
    report = build_final_report(
        objective_id="obj_1",
        cycle_id="cyc_1",
        trace_id="trc_1",
        objective_text=r"D:\repo\.env read this file",
        repository_id="hyeve-integration-service",
        real_execute=real_execute,
    )
    assert report.final_status == "COMPLETED"
    assert report.target_resource == ".env"
    assert report.real_operations == 1
    assert report.target_files_read == 1
    assert report.blocking_stage is None
    assert report.reason_code is None
    assert len(report.verification_checks) == 4
    assert all(c.status == "PASSED" for c in report.verification_checks)
    d = report.to_dict()
    assert d["final_status"] == "COMPLETED"
    assert "administrative_artifacts" in d
    text = report.to_text()
    assert "COMPLETED" in text
    assert "Files read: 1" in text


def test_blocked_report_never_calls_administrative_artifact_user_work() -> None:
    real_execute = {
        "objective_status": "BLOCKED",
        "execution_status": "BLOCKED",
        "verification_status": "NOT_RUN",
        "real_operations": 0,
        "target_files_read": 0,
        "target_files_modified": 0,
        "artifacts": {"objective": "x", "plan": "z", "workhub_load": "y", "execution_report": "e"},
        "reason_code": "CAPABILITY_NOT_REGISTERED",
        "detail": "no branch matched",
    }
    report = build_final_report(
        objective_id="obj_2",
        cycle_id="cyc_2",
        trace_id="trc_2",
        objective_text="build something vague",
        repository_id="aix-frontend",
        real_execute=real_execute,
    )
    assert report.final_status == "BLOCKED"
    assert report.reason_code == "CAPABILITY_NOT_REGISTERED"
    assert report.recommended_next_action
    assert report.real_operations == 0
    text = report.to_text()
    assert "Blocked at:" in text
    assert "Reason:\nCAPABILITY_NOT_REGISTERED" in text
    # Administrative artifacts are listed under their own section, distinct from
    # "real operations" -- never presented as user-work evidence.
    assert "Administrative artifacts:" in text
    for artifact_key in report.administrative_artifacts:
        assert f"- {artifact_key}" in text


def test_no_secret_leaks_through_report() -> None:
    real_execute = {
        "objective_status": "COMPLETED",
        "real_operations": 1,
        "target_files_read": 1,
        "target_files_modified": 0,
        "artifacts": {},
        "read_verification": {"resource_resolved": True, "secret_values_redacted": True},
        "read_file": {"resource": ".env"},
        "detail": "6 env vars found",
    }
    report = build_final_report(
        objective_id="obj_3",
        cycle_id="cyc_3",
        trace_id="trc_3",
        objective_text=".env read this file",
        repository_id="svc",
        real_execute=real_execute,
    )
    rendered = report.to_text() + str(report.to_dict())
    assert "hunter2" not in rendered
    assert "SMTP_PASSWORD=" not in rendered
