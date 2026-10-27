from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.services.aix_enveloped_run_service import execute_enveloped_run


def _base_packet(*, idempotency_key: str = "idem-default") -> dict:
    return {
        "schema_name": "aix_execution_packet_current",
        "schema_version": "v1",
        "packet_id": "pkt-1",
        "correlation_id": "corr-1",
        "causation_id": "pkt-0",
        "tenant_id": "default",
        "project_id": "bh-migrated-7-aix",
        "idempotency_key": idempotency_key,
        "prompt_profile": "default",
        "sender": {"agent_id": "planner-1", "role": "planner"},
        "target": {"role": "coder"},
        "step": {
            "index": 1,
            "status": "queued",
            "summary": "run execution",
            "evidence_refs": [],
        },
    }


def _cloud_event_payload(
    *, packet: dict[str, Any], execution: dict[str, Any], trace_id: str = "corr-1"
) -> dict[str, Any]:
    return {
        "envelope": {
            "specversion": "1.0",
            "id": f"evt-test-{uuid4().hex[:24]}",
            "type": "com.aix.task.state.queued",
            "source": "tests.aix_enveloped_run_service",
            "time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "datacontenttype": "application/json",
            "trace_id": trace_id,
            "data": {
                "packet": dict(packet),
                "execution": dict(execution),
            },
        }
    }


def test_execute_enveloped_run_rejects_unknown_execution_schema(tmp_path: Path) -> None:
    message = _base_packet(idempotency_key=f"idem-{tmp_path.name}-status")
    message["schema_name"] = "unknown_execution_packet_current"
    result = execute_enveloped_run(
        _cloud_event_payload(packet=message, execution={"workspace_root": str(tmp_path)}),
        ingress="http",
    )
    assert result.status == "failed"
    assert result.executable is False
    assert result.run_id is None
    assert result.schema_id == "unknown_execution_packet_current"


def test_execute_enveloped_run_rejects_legacy_envelope_with_typed_boundary_event(capsys) -> None:
    payload = {
        "packet": _base_packet(idempotency_key="idem-legacy-envelope"),
        "execution": {
            "task_id": "task-2316",
            "task_number": 2448,
            "workspace_root": "C:/tmp/nonexistent",
            "steps": [{"tool": "get_status", "args": {}}],
        },
    }
    result = execute_enveloped_run(payload, ingress="http")
    captured = capsys.readouterr()

    emitted: list[dict[str, Any]] = []
    for line in captured.out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            emitted.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    boundary_event = next(event for event in emitted if event.get("type") == "com.aix.task.boundary.rejected")
    expected_details = {
        "rejection_code": "ENVELOPE_FORMAT_INVALID",
        "expected_format": "cloudevents_current",
        "received_format": "ayrpack_current",
        "task_id": "task-2316",
        "trace_id": "corr-1",
        "rejected_at_utc": boundary_event["data"]["rejected_at_utc"],
    }

    assert result.status == "failed"
    assert result.executable is False
    assert result.error is not None
    assert result.error.code == "ENVELOPE_FORMAT_INVALID"
    assert result.error.details == expected_details
    assert result.task_id == "task-2316"
    assert result.task_number == 2448
    assert result.project_id == "bh-migrated-7-aix"
    assert result.packet_id == "pkt-1"
    assert boundary_event["data"]["expected_format"] == "cloudevents_current"
    assert boundary_event["data"]["received_format"] == "ayrpack_current"
    assert boundary_event["data"]["task_id"] == "task-2316"
    assert boundary_event["data"]["trace_id"] == "corr-1"
    assert isinstance(boundary_event["data"]["rejected_at_utc"], str)
    assert boundary_event["data"]["rejected_at_utc"]
    assert boundary_event["data"] == expected_details


def test_execute_enveloped_run_fails_on_missing_workspace() -> None:
    payload = _cloud_event_payload(
        packet=_base_packet(idempotency_key="idem-missing-workspace"),
        execution={
            "workspace_root": "Z:/definitely/missing/workspace/root",
            "steps": [{"tool": "get_status", "args": {}}],
        },
    )
    result = execute_enveloped_run(payload, ingress="http")
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "workspace_missing"
    assert result.run_id is None


def test_execute_enveloped_run_executes_execution_step(tmp_path: Path) -> None:
    payload = _cloud_event_payload(
        packet=_base_packet(idempotency_key=f"idem-{tmp_path.name}-ok-{uuid4().hex[:8]}"),
        execution={
            "workspace_root": str(tmp_path),
            "task_number": 42,
            "steps": [{"tool": "get_status", "args": {}}],
        },
    )
    result = execute_enveloped_run(payload, ingress="http")
    assert result.status in {"completed", "failed"}
    assert result.executable is True
    assert result.run_id is not None
    assert result.task_number == 42
    assert result.schema_id == "aix_execution_packet_current"
    assert result.evidence.get("transcript_count", 0) >= 1
    assert result.evidence.get("contract_version") == "evidence_bundle.current"
    assert result.evidence.get("transcript_ref")
    assert "transcript_ref" in result.evidence
    assert "diff_ref" in result.evidence
    assert "logs_ref" in result.evidence
    assert "tests_ref" in result.evidence
    assert "telemetry" in result.to_dict()
    assert result.telemetry["model"]["agent_id"] == "planner-1"
    assert result.telemetry["runtime"]["step_count"] >= 1


def test_execute_enveloped_run_emits_executor_boundary_event(tmp_path: Path, capsys) -> None:
    payload = _cloud_event_payload(
        packet=_base_packet(idempotency_key=f"idem-{tmp_path.name}-boundary-{uuid4().hex[:8]}"),
        execution={
            "workspace_root": str(tmp_path),
            "task_number": 42,
            "steps": [{"tool": "get_status", "args": {}}],
        },
    )

    result = execute_enveloped_run(payload, ingress="http")
    captured = capsys.readouterr()

    boundary_events = []
    for line in captured.out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if parsed.get("event") == "aix_boundary":
            boundary_events.append(parsed)

    assert result.run_id is not None
    executor_event = next(event for event in boundary_events if event.get("layer") == "executor")
    assert executor_event["code"] == "EXECUTION_STARTED"
    assert executor_event["decision"] == "started"
    assert executor_event["input_summary"]["task_number"] == 42


def test_execute_enveloped_run_replay_sets_replayed_flag(tmp_path: Path) -> None:
    first = _cloud_event_payload(
        packet=_base_packet(idempotency_key=f"idem-{tmp_path.name}-replay"),
        execution={
            "workspace_root": str(tmp_path),
            "task_number": 5,
            "steps": [{"tool": "get_status", "args": {}}],
        },
    )
    second = _cloud_event_payload(
        packet=_base_packet(idempotency_key=f"idem-{tmp_path.name}-replay"),
        execution={
            "workspace_root": str(tmp_path),
            "task_number": 5,
            "steps": [{"tool": "get_status", "args": {}}],
        },
    )
    one = execute_enveloped_run(first, ingress="http")
    two = execute_enveloped_run(second, ingress="http")
    assert one.run_id is not None
    assert two.run_id == one.run_id
    assert two.replayed is True


def test_execute_enveloped_run_surfaces_terminal_transcript_failure(tmp_path: Path) -> None:
    payload = _cloud_event_payload(
        packet=_base_packet(idempotency_key=f"idem-{tmp_path.name}-terminal-fail-{uuid4().hex[:8]}"),
        execution={
            "workspace_root": str(tmp_path),
            "task_number": 77,
            "steps": [{"tool": "read_file", "args": {"relative_path": "missing.txt"}}],
        },
    )
    result = execute_enveloped_run(payload, ingress="http")
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "readonly_target_not_found"
    assert "missing.txt" in result.error.message
    terminal = result.evidence.get("terminal_failure")
    assert isinstance(terminal, dict)
    assert terminal["code"] == "readonly_target_not_found"
    assert terminal["details"]["tool"] == "read_file"


def test_execute_enveloped_run_strips_read_trace_metadata_from_read_range_args(tmp_path: Path, monkeypatch) -> None:
    from backend.services import devexecutor_tool_loop_service as tool_loop_module

    (tmp_path / "demo.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    def fake_read_range(*, workspace_root, **kwargs):
        captured["workspace_root"] = workspace_root
        captured["kwargs"] = dict(kwargs)
        return {"content": "alpha\n", "start_line": 1, "end_line": 1}

    monkeypatch.setattr(tool_loop_module, "read_range", fake_read_range)

    payload = _cloud_event_payload(
        packet=_base_packet(idempotency_key=f"idem-{tmp_path.name}-read-strip-{uuid4().hex[:8]}"),
        execution={
            "workspace_root": str(tmp_path),
            "task_number": 2448,
            "mutation_runtime": {"mode": "local", "container_required": False},
            "steps": [
                {
                    "tool": "read_range",
                    "args": {
                        "relative_path": "demo.txt",
                        "start_line": 1,
                        "end_line": 1,
                        "output_content": "alpha\n",
                        "content": "alpha\n",
                        "source": "planner_output_content",
                    },
                }
            ],
        },
    )

    result = execute_enveloped_run(payload, ingress="http")

    assert result.status in {"completed", "failed"}
    assert captured["workspace_root"] == str(tmp_path)
    assert captured["kwargs"] == {"relative_path": "demo.txt", "start_line": 1, "end_line": 1}


def test_execute_enveloped_run_allows_edit_file_grounded_on_planner_buffer_step_zero(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "backend.services.devexecutor_patch_engine_service._run_pyright_on_files",
        lambda **_kwargs: (True, ""),
    )
    target = tmp_path / "demo.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "demo.txt"], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "init",
        ],
        check=True,
        capture_output=True,
    )

    payload = _cloud_event_payload(
        packet=_base_packet(idempotency_key=f"idem-{tmp_path.name}-grounded-edit-{uuid4().hex[:8]}"),
        execution={
            "workspace_root": str(tmp_path),
            "task_number": 2448,
            "mutation_runtime": {"mode": "local", "container_required": False},
            "steps": [
                {
                    "tool": "edit_file",
                    "step_idx": 1,
                    "planner_grounded_buffer": "alpha\nbeta\n",
                    "planner_grounded_source": "grounded_symbol_source",
                    "args": {
                        "relative_path": "demo.txt",
                        "search_replace_blocks": "<<<<<<< SEARCH\nalpha\n=======\nALPHA\n>>>>>>> REPLACE",
                        "read_source": {"step_idx": 0, "file": "demo.txt"},
                    },
                }
            ],
        },
    )

    result = execute_enveloped_run(payload, ingress="http")

    assert result.error is None
    assert result.status == "completed"
    assert target.read_text(encoding="utf-8") == "ALPHA\nbeta\n"


def test_execute_enveloped_run_collects_changed_files_from_write_file(tmp_path: Path, monkeypatch) -> None:
    from backend.services import aix_enveloped_run_service as module

    class _FakeFinishedRun:
        status = "completed"
        run_id = "devrun-write-1"
        task_number = 88
        project_id = "bh-migrated-7-aix"
        correlation_id = "corr-1"
        replayed = False
        diagnostics: dict[str, Any] = {}
        transcript = [
            {
                "tool": "write_file",
                "ok": True,
                "result": {"changed_files": ["src/demo.txt"]},
            }
        ]

    monkeypatch.setattr(module, "execute_run", lambda **_kwargs: _FakeFinishedRun())
    payload = _cloud_event_payload(
        packet=_base_packet(idempotency_key=f"idem-{tmp_path.name}-write-file-{uuid4().hex[:8]}"),
        execution={
            "workspace_root": str(tmp_path),
            "task_number": 88,
            "steps": [{"tool": "write_file", "args": {"relative_path": "src/demo.txt", "content": "hello\n"}}],
        },
    )
    result = execute_enveloped_run(payload, ingress="http")

    assert result.status == "completed"
    assert result.evidence["changed_files"] == ["src/demo.txt"]
    assert result.evidence["changed_files_ref"] is not None


def test_execute_enveloped_run_collects_grounded_symbol_metadata(tmp_path: Path, monkeypatch) -> None:
    from backend.services import aix_enveloped_run_service as module

    class _FakeFinishedRun:
        status = "completed"
        run_id = "devrun-grounded-1"
        task_number = 89
        project_id = "bh-migrated-7-aix"
        correlation_id = "corr-1"
        replayed = False
        diagnostics: dict[str, Any] = {}
        transcript = [
            {
                "tool": "edit_file",
                "ok": True,
                "result": {
                    "changed_files": ["src/demo.py"],
                    "grounded_symbol": {
                        "qualified_name": "DemoService.run",
                        "symbol_source_hash": "sha256:abc123",
                        "commit_sha": None,
                        "file_blob_sha": None,
                    },
                },
            }
        ]

    monkeypatch.setattr(module, "execute_run", lambda **_kwargs: _FakeFinishedRun())
    payload = _cloud_event_payload(
        packet=_base_packet(idempotency_key=f"idem-{tmp_path.name}-grounded-{uuid4().hex[:8]}"),
        execution={
            "workspace_root": str(tmp_path),
            "task_number": 89,
            "steps": [{"tool": "edit_file", "args": {"relative_path": "src/demo.py"}}],
        },
    )

    result = execute_enveloped_run(payload, ingress="http")

    assert result.status == "completed"
    assert result.evidence["grounded_symbols"][0]["qualified_name"] == "DemoService.run"
    assert result.evidence["grounded_symbols"][0]["symbol_source_hash"] == "sha256:abc123"


def test_execute_enveloped_run_enriches_runtime_with_run_labels(tmp_path: Path, monkeypatch) -> None:
    from backend.services import aix_enveloped_run_service as module

    captured: dict[str, object] = {}

    class _FakeFinishedRun:
        status = "completed"
        run_id = "devrun-runtime-1"
        task_number = 42
        project_id = "bh-migrated-7-aix"
        correlation_id = "corr-1"
        replayed = False
        diagnostics: dict[str, Any] = {}
        transcript = [{"tool": "get_status", "ok": True, "result": {}}]

    def fake_execute_run(**kwargs):
        captured["command_runtime"] = kwargs["command_runtime"]
        captured["mutation_runtime"] = kwargs["mutation_runtime"]
        return _FakeFinishedRun()

    monkeypatch.setattr(module, "execute_run", fake_execute_run)

    payload = _cloud_event_payload(
        packet=_base_packet(idempotency_key=f"idem-{tmp_path.name}-runtime-labels-{uuid4().hex[:8]}"),
        execution={
            "workspace_root": str(tmp_path),
            "task_number": 42,
            "steps": [{"tool": "get_status", "args": {}}],
        },
    )

    result = execute_enveloped_run(payload, ingress="http")

    assert result.status == "completed"
    command_runtime = captured["command_runtime"]
    mutation_runtime = captured["mutation_runtime"]
    assert isinstance(command_runtime, dict)
    assert isinstance(mutation_runtime, dict)
    assert command_runtime["network"] == "none"
    assert mutation_runtime["network"] == "none"


def test_execute_enveloped_run_carries_runtime_contract_diagnostics_into_evidence(tmp_path: Path, monkeypatch) -> None:
    from backend.services import aix_enveloped_run_service as module

    class _FakeFinishedRun:
        status = "completed"
        run_id = "devrun-runtime-contract-1"
        task_number = 42
        project_id = "bh-migrated-7-aix"
        correlation_id = "corr-1"
        replayed = False
        diagnostics = {
            "runtime_contract": {
                "workspace_root": str(tmp_path),
                "command_runtime": {
                    "mode": "docker",
                    "workspace_mapping": {"container_workspace_root": "/workspace/hyeve"},
                },
            }
        }
        transcript = [{"tool": "get_status", "ok": True, "result": {}}]

    monkeypatch.setattr(module, "execute_run", lambda **_kwargs: _FakeFinishedRun())
    payload = _cloud_event_payload(
        packet=_base_packet(idempotency_key=f"idem-{tmp_path.name}-runtime-contract-{uuid4().hex[:8]}"),
        execution={
            "workspace_root": str(tmp_path),
            "task_number": 42,
            "steps": [{"tool": "get_status", "args": {}}],
        },
    )

    result = execute_enveloped_run(payload, ingress="http")

    assert result.status == "completed"
    assert result.evidence["diagnostics"]["runtime_contract"]["command_runtime"]["mode"] == "docker"


def test_execute_enveloped_run_prunes_managed_runtime_artifacts_in_finally(tmp_path: Path, monkeypatch) -> None:
    from backend.services import aix_enveloped_run_service as module

    class _FakeFinishedRun:
        status = "completed"
        run_id = "devrun-cleanup-1"
        task_number = 42
        project_id = "bh-migrated-7-aix"
        correlation_id = "corr-1"
        replayed = False
        diagnostics: dict[str, Any] = {}
        transcript = [{"tool": "get_status", "ok": True, "result": {}}]

    cleanup_calls: list[tuple[str, dict[str, Any]]] = []

    monkeypatch.setattr(module, "execute_run", lambda **_kwargs: _FakeFinishedRun())
    monkeypatch.setattr(
        module,
        "cleanup_managed_command_runtime_artifacts",
        lambda *, runtime: cleanup_calls.append(("command", dict(runtime or {}))),
    )
    monkeypatch.setattr(
        module,
        "cleanup_managed_mutation_runtime_artifacts",
        lambda *, runtime: cleanup_calls.append(("mutation", dict(runtime or {}))),
    )

    payload = _cloud_event_payload(
        packet=_base_packet(idempotency_key=f"idem-{tmp_path.name}-cleanup-{uuid4().hex[:8]}"),
        execution={
            "workspace_root": str(tmp_path),
            "task_number": 42,
            "steps": [{"tool": "get_status", "args": {}}],
        },
    )

    result = execute_enveloped_run(payload, ingress="http")

    assert result.status == "completed"
    assert cleanup_calls[0][0] == "command"
    assert cleanup_calls[0][1]["mode"] == "docker"
    assert cleanup_calls[1][0] == "mutation"
    assert cleanup_calls[1][1]["mode"] == "docker"
