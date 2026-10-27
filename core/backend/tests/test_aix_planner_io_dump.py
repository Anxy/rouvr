from __future__ import annotations

import json
from pathlib import Path

from backend.scripts.aix_planner_io_dump import build_planner_io_view


def test_build_planner_io_view_reads_live_style_nested_response_and_trace(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts" / "run_fire"
    runlogs_root = tmp_path / "_runlogs"
    run_dir = artifact_root / "20260405-022434-244"
    run_dir.mkdir(parents=True)

    request_payload = {
        "generated_at_utc": "2026-04-04T20:58:38.937758+00:00",
        "project_id": "proj-io",
        "task_number": 244,
        "url": "http://example.test/run-once",
        "payload": {"action": "run_fire"},
    }
    response_payload = {
        "generated_at_utc": "2026-04-04T20:58:38.937779+00:00",
        "project_id": "proj-io",
        "task_number": 244,
        "url": "http://example.test/run-once",
        "http_status": 200,
        "elapsed_ms": 1234,
        "success": True,
        "result": {
            "success": True,
            "data": {
                "invocation_id": "runonce-abc",
                "final_status": "blocked",
                "quality_gate": {"code": "PLANNER_TRANSPORT_EXHAUSTED"},
                "minimal_run_outcome": {
                    "correlation_id": "corr-244",
                    "run_id": "devrun-1",
                    "evidence_refs": ["/app/_runlogs/planner-traces/trace-244.json"],
                },
                "execution_digest": {
                    "reason_summary": "planner failed",
                    "failing_tool": None,
                    "runtime": {
                        "tool_sequence": ["read_range", "confirm_anchor"],
                        "phase_summary": {
                            "entries": [
                                {"phase_name": "plan", "status": "completed"},
                                {"phase_name": "dispatch", "status": "failed"},
                            ]
                        },
                    },
                    "refs": {
                        "transcript_ref": "local://devexecutor/runs/2-steps",
                        "logs_ref": "local://devexecutor/runs/2-steps/commands.log",
                        "tests_ref": None,
                    },
                },
                "planner": {
                    "repair_retry_history": [{"reason_code": "INVALID_TRANSITION"}],
                    "model_lane_attempt_failures": [{"candidate_index": 0, "model": "m1"}],
                },
            },
        },
    }
    (run_dir / "request.json").write_text(json.dumps(request_payload), encoding="utf-8")
    (run_dir / "response.json").write_text(json.dumps(response_payload), encoding="utf-8")

    trace_path = runlogs_root / "planner-traces" / "trace-244.json"
    trace_path.parent.mkdir(parents=True)
    trace_payload = {
        "provider": "openrouter",
        "model": "openrouter/google/gemini-3.1-pro-preview",
        "attempts_used": 2,
        "planner_request_shape": "aix.planner.request_envelope.v2",
        "planner_packet_fingerprint": "fp-244",
        "request_payload_bytes": 12345,
        "request_metadata": {"planner_request_shape": "aix.planner.request_envelope.v2"},
        "planner_prompt": "planner_prompt_shape=slice_compact",
        "planner_packet_snapshot": {"slice_id": "slice-244-001"},
        "raw_output_text": '{"steps":[{"tool":"read_range"}]}',
        "parsed_output": {"steps": [{"tool": "read_range"}]},
        "parse_error": None,
    }
    trace_path.write_text(json.dumps(trace_payload), encoding="utf-8")

    result = build_planner_io_view(
        artifact_dir=artifact_root,
        runlogs_root=runlogs_root,
        project_id="proj-io",
        task_number=244,
    )

    assert result["found"] is True
    assert result["response"]["invocation_id"] == "runonce-abc"
    assert result["response"]["quality_gate_code"] == "PLANNER_TRANSPORT_EXHAUSTED"
    assert result["correlation_id"] == "corr-244"
    assert result["planner_io"]["trace_path"] == str(trace_path)
    assert result["planner_io"]["trace_count"] == 1
    assert result["planner_io"]["planner_request_shape"] == "aix.planner.request_envelope.v2"
    assert result["planner_io"]["planner_packet_fingerprint"] == "fp-244"
    assert result["planner_io"]["request_payload_bytes"] == 12345
    assert result["planner_io"]["planner_prompt"] is None
    assert result["planner_io"]["planner_prompt_preview"]["preview"] == "planner_prompt_shape=slice_compact"
    assert result["planner_io"]["parsed_output_summary"]["tools"] == ["read_range"]
    assert result["planner_io"]["raw_output_preview"]["preview"] == '{"steps":[{"tool":"read_range"}]}'
    assert result["planner_io"]["attempt_traces"][0]["trace_path"] == str(trace_path)
    assert result["repair_loop"]["repair_retry_history"][0]["reason_code"] == "INVALID_TRANSITION"
    assert result["executor_io"]["run_id"] == "devrun-1"
    assert result["executor_io"]["tool_sequence"] == ["read_range", "confirm_anchor"]


def test_build_planner_io_view_returns_not_found_when_missing(tmp_path: Path) -> None:
    result = build_planner_io_view(
        artifact_dir=tmp_path / "artifacts" / "run_fire",
        runlogs_root=tmp_path / "_runlogs",
        project_id="proj-missing",
        task_number=1,
    )

    assert result["found"] is False
    assert result["error"] == "RUN_ARTIFACT_NOT_FOUND"


def test_build_planner_io_view_raw_mode_embeds_full_bodies(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts" / "run_fire"
    runlogs_root = tmp_path / "_runlogs"
    run_dir = artifact_root / "20260405-022434-244"
    run_dir.mkdir(parents=True)
    (run_dir / "request.json").write_text(
        json.dumps({"project_id": "proj-raw", "task_number": 7, "payload": {"action": "run_fire"}}), encoding="utf-8"
    )
    (run_dir / "response.json").write_text(
        json.dumps(
            {
                "project_id": "proj-raw",
                "task_number": 7,
                "result": {
                    "data": {
                        "minimal_run_outcome": {
                            "evidence_refs": ["/app/_runlogs/planner-traces/trace-7.json"],
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    trace_path = runlogs_root / "planner-traces" / "trace-7.json"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text(
        json.dumps(
            {
                "planner_request_shape": "aix.planner.request_envelope.v2",
                "planner_packet_fingerprint": "fp-7",
                "request_payload_bytes": 777,
                "request_metadata": {"planner_request_shape": "aix.planner.request_envelope.v2"},
                "planner_prompt": "full prompt",
                "planner_packet_snapshot": {"slice_id": "slice-7"},
                "raw_output_text": '{"steps":[{"tool":"read_range"}]}',
                "parsed_output": {"steps": [{"tool": "read_range"}]},
                "upstream_payload": {"response_json": {"ok": True}},
            }
        ),
        encoding="utf-8",
    )

    result = build_planner_io_view(
        artifact_dir=artifact_root,
        runlogs_root=runlogs_root,
        project_id="proj-raw",
        task_number=7,
        include_raw=True,
    )

    assert result["request"]["payload_preview"] == {"action": "run_fire"}
    assert result["planner_io"]["planner_prompt"] == "full prompt"
    assert result["planner_io"]["planner_request_shape"] == "aix.planner.request_envelope.v2"
    assert result["planner_io"]["planner_packet_fingerprint"] == "fp-7"
    assert result["planner_io"]["request_payload_bytes"] == 777
    assert result["planner_io"]["planner_packet_snapshot"] == {"slice_id": "slice-7"}
    assert result["planner_io"]["raw_output_text"] == '{"steps":[{"tool":"read_range"}]}'
    assert result["planner_io"]["parsed_output"] == {"steps": [{"tool": "read_range"}]}
    assert result["planner_io"]["upstream_payload"] == {"response_json": {"ok": True}}
    assert result["planner_io"]["request_metadata"]["planner_request_shape"] == "aix.planner.request_envelope.v2"


def test_build_planner_io_view_falls_back_to_correlation_trace_scan(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts" / "run_fire"
    runlogs_root = tmp_path / "_runlogs"
    run_dir = artifact_root / "20260405-022434-244"
    run_dir.mkdir(parents=True)
    (run_dir / "request.json").write_text(
        json.dumps({"project_id": "proj-corr", "task_number": 244, "payload": {"action": "run_fire"}}),
        encoding="utf-8",
    )
    (run_dir / "response.json").write_text(
        json.dumps(
            {
                "project_id": "proj-corr",
                "task_number": 244,
                "result": {
                    "data": {
                        "invocation_id": "runonce-corr",
                        "final_status": "blocked",
                        "quality_gate": {"code": "PLANNER_TRANSPORT_EXHAUSTED"},
                        "minimal_run_outcome": {
                            "correlation_id": "corr-244",
                            "evidence_refs": [],
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    trace_path = runlogs_root / "planner-traces" / "trace-corr-244.json"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text(
        json.dumps(
            {
                "project_id": "proj-corr",
                "correlation_id": "corr-244",
                "planner_prompt": "transport-failed prompt",
                "planner_packet_snapshot": {"slice_id": "slice-244"},
                "raw_output_text": "",
                "parsed_output": None,
                "parse_error": "PLANNER_TRANSPORT_TIMEOUT error=timed out attempts=1",
            }
        ),
        encoding="utf-8",
    )

    result = build_planner_io_view(
        artifact_dir=artifact_root,
        runlogs_root=runlogs_root,
        project_id="proj-corr",
        task_number=244,
    )

    assert result["planner_io"]["trace_path"] == str(trace_path)
    assert result["planner_io"]["trace_source"] == "correlation_scan"
    assert result["planner_io"]["planner_prompt_preview"]["preview"] == "transport-failed prompt"
    assert result["planner_io"]["parse_error"] == "PLANNER_TRANSPORT_TIMEOUT error=timed out attempts=1"


def test_build_planner_io_view_resolves_local_trace_ref_from_attempt_failure_error(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts" / "run_fire"
    runlogs_root = tmp_path / "_runlogs"
    run_dir = artifact_root / "20260405-161510-244"
    run_dir.mkdir(parents=True)
    (run_dir / "request.json").write_text(
        json.dumps({"project_id": "proj-attempt", "task_number": 244, "payload": {"action": "run_fire"}}),
        encoding="utf-8",
    )
    trace_path = runlogs_root / "planner-traces" / "trace-attempt-244.json"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text(
        json.dumps(
            {
                "project_id": "proj-attempt",
                "correlation_id": "corr-attempt",
                "planner_prompt": "attempt failure prompt",
                "raw_output_text": "",
                "parse_error": "PLANNER_TRANSPORT_TIMEOUT error=timed out attempts=1",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "response.json").write_text(
        json.dumps(
            {
                "project_id": "proj-attempt",
                "task_number": 244,
                "result": {
                    "data": {
                        "invocation_id": "runonce-attempt",
                        "final_status": "blocked",
                        "quality_gate": {"code": "PLANNER_TRANSPORT_EXHAUSTED"},
                        "minimal_run_outcome": {"correlation_id": "corr-attempt", "evidence_refs": []},
                        "planner": {
                            "model_lane_attempt_failures": [
                                {
                                    "candidate_index": 0,
                                    "error": (
                                        "PLANNER_TRANSPORT_TIMEOUT error=timed out attempts=1 "
                                        f"local_trace_ref=/app/_runlogs/planner-traces/{trace_path.name}"
                                    ),
                                }
                            ]
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = build_planner_io_view(
        artifact_dir=artifact_root,
        runlogs_root=runlogs_root,
        project_id="proj-attempt",
        task_number=244,
    )

    assert result["planner_io"]["trace_path"] == str(trace_path)
    assert result["planner_io"]["trace_source"] == "attempt_error_ref"
    assert result["planner_io"]["planner_prompt_preview"]["preview"] == "attempt failure prompt"


def test_build_planner_io_view_resolves_direct_local_trace_ref(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts" / "run_fire"
    runlogs_root = tmp_path / "_runlogs"
    run_dir = artifact_root / "20260405-163742-244"
    run_dir.mkdir(parents=True)
    trace_path = runlogs_root / "planner-traces" / "trace-direct-244.json"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text(
        json.dumps(
            {
                "project_id": "proj-direct",
                "correlation_id": "corr-direct",
                "planner_prompt": "direct trace prompt",
                "raw_output_text": '{"steps":[{"tool":"edit_file"}]}',
                "parsed_output": {"steps": [{"tool": "edit_file", "args": {"relative_path": "backend/services/a.py"}}]},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "request.json").write_text(
        json.dumps({"project_id": "proj-direct", "task_number": 244, "payload": {"action": "run_fire"}}),
        encoding="utf-8",
    )
    (run_dir / "response.json").write_text(
        json.dumps(
            {
                "project_id": "proj-direct",
                "task_number": 244,
                "result": {
                    "data": {
                        "invocation_id": "runonce-direct",
                        "final_status": "blocked",
                        "quality_gate": {"code": "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE"},
                        "minimal_run_outcome": {"correlation_id": "corr-direct"},
                        "planner": {"local_trace_ref": f"/app/_runlogs/planner-traces/{trace_path.name}"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = build_planner_io_view(
        artifact_dir=artifact_root,
        runlogs_root=runlogs_root,
        project_id="proj-direct",
        task_number=244,
    )

    assert result["planner_io"]["trace_path"] == str(trace_path)
    assert result["planner_io"]["trace_source"] == "direct_trace_ref"
    assert result["planner_io"]["planner_prompt_preview"]["preview"] == "direct trace prompt"


def test_build_planner_io_view_surfaces_unresolved_container_trace_refs(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts" / "run_fire"
    runlogs_root = tmp_path / "_runlogs"
    run_dir = artifact_dir / "20260405-163742-244"
    run_dir.mkdir(parents=True)
    (run_dir / "request.json").write_text(
        json.dumps({"project_id": "bh-migrated-7-aix", "task_number": 244, "payload": {"task_number": 244}}),
        encoding="utf-8",
    )
    (run_dir / "response.json").write_text(
        json.dumps(
            {
                "project_id": "bh-migrated-7-aix",
                "task_number": 244,
                "result": {
                    "planner": {
                        "local_trace_ref": "/app/_runlogs/planner-traces/ad401f78-9b93-5ad2-9694-3402caa600ff.json"
                    },
                    "quality_gate": {"code": "PLANNER_TRANSPORT_EXHAUSTED"},
                },
            }
        ),
        encoding="utf-8",
    )

    result = build_planner_io_view(
        artifact_dir=artifact_dir,
        runlogs_root=runlogs_root,
        project_id="bh-migrated-7-aix",
        task_number=244,
    )

    assert result["planner_io"]["trace_path"] is None
    assert result["planner_io"]["trace_count"] == 0
    assert str(runlogs_root) in result["planner_io"]["trace_resolution_roots"]
    assert result["planner_io"]["unresolved_trace_refs"] == [
        "/app/_runlogs/planner-traces/ad401f78-9b93-5ad2-9694-3402caa600ff.json"
    ]
    assert result["planner_io"]["trace_resolution_warnings"] == [
        "planner trace refs were present but could not be resolved under any scanned host runlogs root; these refs may still be container-scoped or missing from the host workspace"
    ]


def test_build_planner_io_view_resolves_container_trace_via_workspace_backed_host_root(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts" / "run_fire"
    runlogs_root = tmp_path / "_runlogs"
    run_dir = artifact_dir / "20260405-200000-244"
    run_dir.mkdir(parents=True)
    workspace_trace_root = (
        tmp_path / "projects" / "AIX" / "hyeve-aix-service" / "backend" / "_runlogs" / "planner-traces"
    )
    workspace_trace_root.mkdir(parents=True)
    trace_path = workspace_trace_root / "da134c7d-0000-5000-8000-123456789abc.json"
    trace_path.write_text(
        json.dumps(
            {
                "project_id": "bh-migrated-7-aix",
                "correlation_id": "corr-trace-map",
                "planner_prompt": "workspace-backed trace prompt",
                "parse_error": "PLANNER_TRANSPORT_TIMEOUT error=timed out attempts=1",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "request.json").write_text(
        json.dumps({"project_id": "bh-migrated-7-aix", "task_number": 244, "payload": {"task_number": 244}}),
        encoding="utf-8",
    )
    (run_dir / "response.json").write_text(
        json.dumps(
            {
                "project_id": "bh-migrated-7-aix",
                "task_number": 244,
                "result": {
                    "data": {
                        "minimal_run_outcome": {"correlation_id": "corr-trace-map"},
                        "planner": {"local_trace_ref": f"/app/_runlogs/planner-traces/{trace_path.name}"},
                        "quality_gate": {"code": "PLANNER_TRANSPORT_EXHAUSTED"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = build_planner_io_view(
        artifact_dir=artifact_dir,
        runlogs_root=runlogs_root,
        project_id="bh-migrated-7-aix",
        task_number=244,
    )

    assert result["planner_io"]["trace_path"] == str(trace_path)
    assert result["planner_io"]["trace_source"] == "direct_trace_ref"
    assert str(workspace_trace_root.parent) in result["planner_io"]["trace_resolution_roots"]
    assert result["planner_io"]["trace_resolution_warnings"] == []


def test_build_planner_io_view_collects_multiple_attempt_traces(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts" / "run_fire"
    runlogs_root = tmp_path / "_runlogs"
    run_dir = artifact_root / "20260405-170613-244"
    run_dir.mkdir(parents=True)
    trace_root = runlogs_root / "planner-traces"
    trace_root.mkdir(parents=True)
    direct_trace = trace_root / "trace-direct.json"
    attempt_trace = trace_root / "trace-attempt.json"
    direct_trace.write_text(
        json.dumps({"planner_prompt": "direct prompt", "project_id": "proj-multi", "correlation_id": "corr-multi"}),
        encoding="utf-8",
    )
    attempt_trace.write_text(
        json.dumps({"planner_prompt": "attempt prompt", "project_id": "proj-multi", "correlation_id": "corr-multi"}),
        encoding="utf-8",
    )
    (run_dir / "request.json").write_text(
        json.dumps({"project_id": "proj-multi", "task_number": 244, "payload": {"action": "run_fire"}}),
        encoding="utf-8",
    )
    (run_dir / "response.json").write_text(
        json.dumps(
            {
                "project_id": "proj-multi",
                "task_number": 244,
                "result": {
                    "data": {
                        "minimal_run_outcome": {"correlation_id": "corr-multi"},
                        "planner": {
                            "local_trace_ref": str(direct_trace),
                            "model_lane_attempt_failures": [
                                {"error": f"PLANNER_TRANSPORT_TIMEOUT error=timed out local_trace_ref={attempt_trace}"}
                            ],
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = build_planner_io_view(
        artifact_dir=artifact_root,
        runlogs_root=runlogs_root,
        project_id="proj-multi",
        task_number=244,
    )

    assert result["planner_io"]["trace_count"] == 2
    prompts = [item["planner_prompt_preview"]["preview"] for item in result["planner_io"]["attempt_traces"]]
    assert prompts == ["direct prompt", "attempt prompt"]
