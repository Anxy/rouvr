from __future__ import annotations

import json
from pathlib import Path

from backend.scripts.aix_run_chain_dump import _default_artifact_dir, build_run_chain


def test_build_run_chain_loads_latest_matching_artifact_and_linked_logs(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts" / "run_fire"
    runlogs_root = tmp_path / "_runlogs"
    older = artifact_root / "older-run"
    latest = artifact_root / "latest-run"
    older.mkdir(parents=True)
    latest.mkdir(parents=True)

    common_response = {
        "result": {
            "project_id": "proj-1",
            "task_number": 244,
            "invocation_id": "runonce-abc",
            "final_status": "blocked",
            "quality_gate": {"code": "PLANNER_TRANSPORT_EXHAUSTED"},
            "execution_digest": {
                "refs": {"transcript_ref": None, "logs_ref": None, "tests_ref": None},
                "runtime": {"phase_summary": {"entries": [{"phase_name": "plan", "status": "completed"}]}},
            },
            "minimal_run_outcome": {
                "correlation_id": "corr-244",
                "evidence_refs": [
                    "local:///app/_runlogs/proposal-review/proposal-244/diff.patch",
                    "/app/_runlogs/planner-traces/trace-244.json",
                    "trace-uuid-ignored-because-path-exists",
                ],
            },
        }
    }

    (older / "request.json").write_text(json.dumps({"artifact": "older"}), encoding="utf-8")
    (older / "response.json").write_text(json.dumps(common_response), encoding="utf-8")
    (latest / "request.json").write_text(json.dumps({"artifact": "latest"}), encoding="utf-8")
    (latest / "response.json").write_text(json.dumps(common_response), encoding="utf-8")

    ledger = runlogs_root / "aix-run-ledger" / "corr-244.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({"planner": {"error": "boom"}}), encoding="utf-8")

    phase_dir = runlogs_root / "aix-execution-phase-ledger" / "proj-1" / "task-244" / "invocation-corr-244"
    phase_dir.mkdir(parents=True)
    (phase_dir / "plan.attempt-1.json").write_text('{"phase":"plan"}', encoding="utf-8")

    trace = runlogs_root / "planner-traces" / "trace-244.json"
    trace.parent.mkdir(parents=True)
    trace.write_text('{"packet_id":"trace-244"}', encoding="utf-8")

    proposal = runlogs_root / "proposal-review" / "proposal-244"
    proposal.mkdir(parents=True)
    (proposal / "diff.patch").write_text("diff --git a/x b/x", encoding="utf-8")
    (proposal / "changed_files.json").write_text('["x.py"]', encoding="utf-8")

    result = build_run_chain(
        artifact_dir=artifact_root,
        runlogs_root=runlogs_root,
        project_id="proj-1",
        task_number=244,
    )

    assert result["found"] is True
    assert result["artifact_run_dir"] == str(latest)
    assert result["response"]["summary"]["top_level_keys"] == ["result"]
    assert result["request"]["json"] is None
    assert result["response"]["json"] is None
    assert result["run_ledger"]["path"] == str(ledger)
    assert result["phase_ledgers"][0]["name"] == "plan.attempt-1.json"
    assert "content" not in result["phase_ledgers"][0]
    assert result["planner_traces"][0]["path"] == str(trace)
    assert result["proposal_review"][0]["directory"] == str(proposal)
    assert result["proposal_review"][0]["files"][0]["name"] == "diff.patch"
    assert "content" not in result["proposal_review"][0]["files"][0]


def test_build_run_chain_matches_when_project_and_task_exist_only_in_request_payload(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts" / "run_fire"
    runlogs_root = tmp_path / "_runlogs"
    run_dir = artifact_root / "request-matched-run"
    run_dir.mkdir(parents=True)

    request_payload = {
        "action": "run_fire",
        "project_id": "proj-request",
        "task_number": 321,
    }
    response_payload = {"status": "ok", "result": {"message": "no direct ids here"}}
    (run_dir / "request.json").write_text(json.dumps(request_payload), encoding="utf-8")
    (run_dir / "response.json").write_text(json.dumps(response_payload), encoding="utf-8")

    result = build_run_chain(
        artifact_dir=artifact_root,
        runlogs_root=runlogs_root,
        project_id="proj-request",
        task_number=321,
    )

    assert result["found"] is True
    assert result["artifact_run_dir"] == str(run_dir)


def test_build_run_chain_raw_mode_embeds_file_contents(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts" / "run_fire"
    runlogs_root = tmp_path / "_runlogs"
    run_dir = artifact_root / "raw-run"
    run_dir.mkdir(parents=True)
    request_payload = {"project_id": "proj-raw", "task_number": 7}
    response_payload = {
        "project_id": "proj-raw",
        "task_number": 7,
        "result": {
            "minimal_run_outcome": {
                "correlation_id": "corr-7",
                "evidence_refs": ["/app/_runlogs/planner-traces/trace-7.json"],
            }
        },
    }
    (run_dir / "request.json").write_text(json.dumps(request_payload), encoding="utf-8")
    (run_dir / "response.json").write_text(json.dumps(response_payload), encoding="utf-8")
    ledger = runlogs_root / "aix-run-ledger" / "corr-7.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"ok":true}', encoding="utf-8")
    trace = runlogs_root / "planner-traces" / "trace-7.json"
    trace.parent.mkdir(parents=True)
    trace.write_text('{"trace":7}', encoding="utf-8")

    result = build_run_chain(
        artifact_dir=artifact_root,
        runlogs_root=runlogs_root,
        project_id="proj-raw",
        task_number=7,
        include_raw=True,
    )

    assert result["request"]["json"]["project_id"] == "proj-raw"
    assert result["request"]["raw"]
    assert result["run_ledger"]["raw"] == '{"ok":true}'
    assert result["planner_traces"][0]["content"] == '{"trace":7}'


def test_build_run_chain_reads_live_style_nested_result_data_envelope(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts" / "run_fire"
    runlogs_root = tmp_path / "_runlogs"
    run_dir = artifact_root / "live-run"
    run_dir.mkdir(parents=True)
    (run_dir / "request.json").write_text(
        json.dumps({"project_id": "proj-live", "task_number": 244, "payload": {"action": "run_fire"}}),
        encoding="utf-8",
    )
    (run_dir / "response.json").write_text(
        json.dumps(
            {
                "project_id": "proj-live",
                "task_number": 244,
                "http_status": 200,
                "result": {
                    "success": True,
                    "data": {
                        "invocation_id": "runonce-live",
                        "final_status": "blocked",
                        "quality_gate": {"code": "executor_terminal_step_failed"},
                        "minimal_run_outcome": {
                            "correlation_id": "corr-live",
                            "evidence_refs": ["/app/_runlogs/planner-traces/trace-live.json"],
                        },
                        "execution_digest": {
                            "refs": {"transcript_ref": "local://devexecutor/runs/6-steps"},
                            "runtime": {"phase_summary": {"entries": [{"phase_name": "plan", "status": "completed"}]}},
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    ledger = runlogs_root / "aix-run-ledger" / "corr-live.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"planner":{"error":"boom"}}', encoding="utf-8")
    trace = runlogs_root / "planner-traces" / "trace-live.json"
    trace.parent.mkdir(parents=True)
    trace.write_text('{"packet_id":"trace-live"}', encoding="utf-8")

    result = build_run_chain(
        artifact_dir=artifact_root,
        runlogs_root=runlogs_root,
        project_id="proj-live",
        task_number=244,
    )

    assert result["found"] is True
    assert result["derived"]["invocation_id"] == "runonce-live"
    assert result["derived"]["correlation_id"] == "corr-live"
    assert result["derived"]["final_status"] == "blocked"
    assert result["derived"]["quality_gate_code"] == "executor_terminal_step_failed"
    assert result["derived"]["refs"]["transcript_ref"] == "local://devexecutor/runs/6-steps"
    assert result["planner_traces"][0]["path"] == str(trace)


def test_build_run_chain_returns_not_found_when_no_matching_artifact(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts" / "run_fire"
    artifact_root.mkdir(parents=True)

    result = build_run_chain(
        artifact_dir=artifact_root,
        runlogs_root=tmp_path / "_runlogs",
        project_id="proj-missing",
        task_number=999,
    )

    assert result["found"] is False
    assert result["error"] == "RUN_ARTIFACT_NOT_FOUND"


def test_default_artifact_dir_prefers_existing_parent_artifacts_directory(tmp_path: Path, monkeypatch) -> None:
    service_root = tmp_path / "projects" / "AIX" / "hyeve-aix-service"
    script_path = service_root / "backend" / "scripts" / "aix_run_chain_dump.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("# stub\n", encoding="utf-8")
    expected = tmp_path / "artifacts" / "run_fire"
    expected.mkdir(parents=True)

    monkeypatch.setattr("backend.scripts.aix_run_chain_dump.Path.resolve", lambda self: script_path)

    assert _default_artifact_dir() == expected
