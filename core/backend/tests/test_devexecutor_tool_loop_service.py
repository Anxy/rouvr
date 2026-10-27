from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import backend.services.devexecutor_tool_loop_service as tool_loop_module
from backend.services.devexecutor_tool_loop_service import (
    _strip_mutation_only_transport_args,
    _strip_replace_function_execution_args,
    execute_react_tool_loop,
    execute_tool_loop,
    execute_tool_step,
)


@pytest.fixture(autouse=True)
def _disable_legacy_mutation_tool_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIX_ENABLE_EDIT_SYMBOL_FALLBACK", raising=False)
    monkeypatch.delenv("AIX_ENABLE_REPLACE_IN_FUNCTION_FALLBACK", raising=False)
    monkeypatch.delenv("AIX_ENABLE_UPDATE_CALL_KWARG_FALLBACK", raising=False)
    monkeypatch.delenv("AIX_ENABLE_INSERT_AFTER_SYMBOL_FALLBACK", raising=False)
    monkeypatch.delenv("AIX_ENABLE_INSERT_BEFORE_RETURN_FALLBACK", raising=False)


@pytest.fixture
def enable_legacy_mutation_tool_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIX_ENABLE_EDIT_SYMBOL_FALLBACK", "1")
    monkeypatch.setenv("AIX_ENABLE_REPLACE_IN_FUNCTION_FALLBACK", "1")
    monkeypatch.setenv("AIX_ENABLE_UPDATE_CALL_KWARG_FALLBACK", "1")
    monkeypatch.setenv("AIX_ENABLE_INSERT_AFTER_SYMBOL_FALLBACK", "1")
    monkeypatch.setenv("AIX_ENABLE_INSERT_BEFORE_RETURN_FALLBACK", "1")


def test_tool_loop_happy_path_with_patch_and_command(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "backend.services.devexecutor_patch_engine_service._run_pyright_on_files",
        lambda **_kwargs: (True, ""),
    )
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    (tmp_path / "hello.txt").write_text("hello\n", encoding="utf-8")
    patch = """diff --git a/hello.txt b/hello.txt
index ce01362..94954ab 100644
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-hello
+hello world
"""

    steps = [
        {"tool": "list_dir", "args": {"relative_path": "."}},
        {"tool": "read_file", "args": {"relative_path": "hello.txt"}},
        {"tool": "apply_patch", "args": {"patch_text": patch}},
        {"tool": "search", "args": {"pattern": "world"}},
        {"tool": "cmd.run", "args": {"command": [sys.executable, "-c", "print('ok')"], "timeout_seconds": 5.0}},
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[[sys.executable], ["git"]])
    assert len(transcript) == 5
    assert all(item["ok"] for item in transcript)
    assert all("diagnostics" in item for item in transcript)
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hello world\n"


def test_tool_loop_stops_on_denied_command(tmp_path: Path) -> None:
    steps = [
        {"tool": "write_file", "args": {"relative_path": "x.txt", "content": "x"}},
        {"tool": "cmd.run", "args": {"command": ["whoami"], "timeout_seconds": 2.0}},
        {"tool": "list_dir", "args": {"relative_path": "."}},
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])
    assert transcript[0]["ok"] is True
    assert transcript[1]["ok"] is False
    assert "COMMAND_DENIED" in transcript[1]["error"]
    assert len(transcript) == 2


@settings(max_examples=30, deadline=None)
@given(
    payload=st.dictionaries(
        keys=st.text(
            alphabet=st.characters(min_codepoint=97, max_codepoint=122),
            min_size=1,
            max_size=12,
        ).filter(lambda key: key not in {"resolved_path", "read_source"} and not key.startswith("__aix_")),
        values=st.one_of(st.integers(), st.text(max_size=20), st.booleans()),
        max_size=6,
    ),
    transport_value=st.one_of(st.integers(), st.text(max_size=20), st.booleans()),
)
def test_strip_mutation_only_transport_args_property_removes_transport_fields(payload, transport_value) -> None:
    args = dict(payload)
    args["resolved_path"] = "C:/tmp/demo.py"
    args["read_source"] = {"step_idx": 0, "file": "demo.py"}
    args["__aix_anchor"] = transport_value
    args["__aix_exact_anchor_buffer"] = "buffer"

    cleaned = _strip_mutation_only_transport_args(args)

    assert "resolved_path" not in cleaned
    assert "read_source" not in cleaned
    assert "__aix_anchor" not in cleaned
    assert "__aix_exact_anchor_buffer" not in cleaned
    for key, value in payload.items():
        assert cleaned[key] == value


def test_strip_replace_function_execution_args_removes_rewrite_spec_fields() -> None:
    args = {
        "relative_path": "fixture.py",
        "qualified_symbol": "FixtureService.list_items",
        "replacement": "def list_items(self):\n    return {'items': []}\n",
        "function_name": "FixtureService.list_items",
        "edit_intent": "Return empty items payload.",
        "preserve_constraints": ["preserve exact function name"],
        "line_targets": ["return []"],
        "rewrite_generation": {"source": "grounded_symbol_rewrite"},
        "resolved_path": "C:/tmp/fixture.py",
        "__aix_anchor": "anchor",
    }

    cleaned = _strip_replace_function_execution_args(args)

    assert cleaned == {
        "relative_path": "fixture.py",
        "qualified_symbol": "FixtureService.list_items",
        "replacement": "def list_items(self):\n    return {'items': []}\n",
    }


def test_estimate_text_tokens_prefers_cl100k_encoder(monkeypatch) -> None:
    class _FakeEncoder:
        def encode(self, text: str, disallowed_special=()):  # noqa: ARG002
            return [piece for piece in text.split() if piece]

    monkeypatch.setattr(tool_loop_module, "_CL100K_ENCODER", _FakeEncoder())
    assert tool_loop_module._estimate_text_tokens("alpha beta gamma") == 3


def test_execute_tool_step_normalizes_string_cmd_run_command(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run_command(**kwargs):
        captured["command"] = kwargs["command"]
        return {
            "command": kwargs["command"],
            "returncode": 0,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)

    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "cmd.run",
            "args": {
                "command": "pytest -v services/hyeve-codeintel-service/tests/test_codeintel_indexing_service.py",
                "timeout_seconds": 5.0,
            },
        },
        allow_prefixes=[["pytest"]],
        attempt_tracker={},
    )

    assert result["ok"] is True
    assert captured["command"] == [
        "pytest",
        "-v",
        "services/hyeve-codeintel-service/tests/test_codeintel_indexing_service.py",
    ]


def test_execute_tool_step_prefers_command_text_for_quoted_pytest_k_expression(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run_command(**kwargs):
        captured["command"] = kwargs["command"]
        captured["has_command_text"] = "command_text" in kwargs
        return {
            "command": kwargs["command"],
            "returncode": 0,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)

    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "cmd.run",
            "args": {
                "command_text": "python -m pytest backend/tests/aix_core/bootstrap -q --tb=short",
                "command": [
                    "python",
                    "-m",
                    "pytest",
                    "backend/tests/aix_core/bootstrap",
                    "-q",
                    "--tb=short",
                    "-k",
                    "PLANNER_PACKET_REQUIRED",
                    "or",
                    "recovery_task_auto_submit",
                ],
                "timeout_seconds": 5.0,
            },
        },
        allow_prefixes=[["python"]],
        attempt_tracker={},
    )

    assert result["ok"] is True
    assert captured["command"] == [
        "python",
        "-m",
        "pytest",
        "backend/tests/aix_core/bootstrap",
        "-q",
        "--tb=short",
        "-k",
        "PLANNER_PACKET_REQUIRED or recovery_task_auto_submit",
    ]
    assert captured["has_command_text"] is False


def test_execute_tool_step_applies_pytest_timeout_floor(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run_command(**kwargs):
        captured["timeout_seconds"] = kwargs["timeout_seconds"]
        captured["command"] = kwargs["command"]
        return {
            "command": kwargs["command"],
            "returncode": 0,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
            "timeout_seconds": kwargs["timeout_seconds"],
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)

    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "cmd.run",
            "args": {
                "command": ["python", "-m", "pytest", "backend/tests/aix_core/bootstrap", "-q"],
                "timeout_seconds": 30.0,
            },
        },
        allow_prefixes=[["python"]],
        attempt_tracker={},
    )

    assert result["ok"] is True
    assert captured["command"] == [
        "python",
        "-m",
        "pytest",
        "backend/tests/aix_core/bootstrap",
        "-q",
    ]
    assert captured["timeout_seconds"] == 120.0


def test_execute_tool_step_caps_pytest_timeout_ceiling(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_run_command(**kwargs):
        captured["timeout_seconds"] = kwargs["timeout_seconds"]
        return {
            "command": kwargs["command"],
            "returncode": 0,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
            "timeout_seconds": kwargs["timeout_seconds"],
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)

    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "cmd.run",
            "args": {
                "command": ["python", "-m", "pytest", "backend/tests/aix_core/bootstrap", "-q"],
                "timeout_seconds": 1200.0,
            },
        },
        allow_prefixes=[["python"]],
        attempt_tracker={},
    )

    assert result["ok"] is True
    assert captured["timeout_seconds"] == 180.0


def test_declared_validation_timeout_does_not_retry_cpu_bound_pytest(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run_command(**kwargs):
        calls.append(dict(kwargs))
        return {
            "command": kwargs["command"],
            "returncode": -1,
            "timed_out": True,
            "stdout": "",
            "stderr": "timed out",
            "timeout_seconds": kwargs["timeout_seconds"],
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)
    command = ["python", "-m", "pytest", "tests/test_runner.py", "-q"]
    command_text = "python -m pytest tests/test_runner.py -q"

    result = execute_tool_step(
        workspace_root=tmp_path,
        step={"tool": "cmd.run", "args": {"command": command, "command_text": command_text, "timeout_seconds": 1200}},
        allow_prefixes=[["python"]],
        attempt_tracker={},
        execution_context={"declared_validation_command": {"command_text": command_text}},
    )

    assert result["ok"] is False
    assert len(calls) == 1
    assert calls[0]["timeout_seconds"] == 180.0
    assert result["result"]["contract_validation"]["attempts_used"] == 1


def test_execute_tool_step_passes_mutation_runtime_to_write_file(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_write_file(**kwargs):
        captured.update(kwargs)
        return {"operation": "write_file", "changed_files": ["a.txt"]}

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.write_file", fake_write_file)
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={"tool": "write_file", "args": {"relative_path": "a.txt", "content": "x"}},
        allow_prefixes=[["git"]],
        attempt_tracker={},
        mutation_runtime={"mode": "docker", "container_required": True, "image": "runtime:latest"},
    )
    assert result["ok"] is True
    assert captured["runtime"] == {"mode": "docker", "container_required": True, "image": "runtime:latest"}


def test_execute_tool_step_passes_execution_context_to_edit_file(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_edit_file(**kwargs):
        captured.update(kwargs)
        return {"operation": "edit_file", "changed_files": ["a.py"]}

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.edit_file", fake_edit_file)
    execution_context: dict[str, object] = {
        "ayrpack": {"spine": []},
        "last_read_buffer": {
            "tool": "read_file",
            "relative_path": "a.py",
            "content": "x",
            "step_idx": 0,
        },
    }

    result = execute_tool_step(
        workspace_root=tmp_path,
        step={"tool": "edit_file", "args": {"relative_path": "a.py", "search_replace_blocks": "x"}},
        allow_prefixes=[["git"]],
        attempt_tracker={},
        execution_context=execution_context,
    )

    assert result["ok"] is True
    assert captured["execution_context"] == execution_context


def test_execute_tool_step_passes_mutation_runtime_to_insert_at_anchor(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_insert_at_anchor(**kwargs):
        captured.update(kwargs)
        return {"operation": "insert_at_anchor", "changed_files": ["a.py"]}

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.insert_at_anchor", fake_insert_at_anchor)

    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "insert_at_anchor",
            "args": {"relative_path": "a.py", "anchor_text": "value = 1\n", "new_content": "value = 2\n"},
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
        mutation_runtime={"mode": "docker", "container_required": True, "image": "runtime:latest"},
    )

    assert result["ok"] is True
    assert captured["runtime"] == {"mode": "docker", "container_required": True, "image": "runtime:latest"}


def test_execute_tool_step_structures_insert_at_anchor_precheck_failure(monkeypatch, tmp_path: Path) -> None:
    def fake_insert_at_anchor(**_kwargs):
        raise RuntimeError(
            "INSERT_AT_ANCHOR_PATCH_APPLY_FAILED attempt=1 reason=pyright_precheck_failed: demo.py:1:7 - error"
        )

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.insert_at_anchor", fake_insert_at_anchor)

    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "insert_at_anchor",
            "args": {"relative_path": "demo.py", "anchor_text": "value = 1\n", "new_content": "broken = name\n"},
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )

    assert result["ok"] is False
    assert result["result"]["code"] == "insert_at_anchor_patch_apply_failed"
    assert result["result"]["relative_path"] == "demo.py"


def test_execute_tool_step_structures_search_replace_mismatch_from_runtime(monkeypatch, tmp_path: Path) -> None:
    def fake_edit_file(**_kwargs):
        raise RuntimeError(
            "MUTATION_RUNTIME_FAILED tool=edit_file detail=SEARCH_REPLACE_NOT_FOUND: "
            "SEARCH text for block #1 was not matchable in the target file\n"
            "Target file: demo.py\n"
            "Attempted SEARCH text:\n"
            "def old_name():\n"
            "    return 'legacy'\n"
        )

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.edit_file", fake_edit_file)

    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "edit_file",
            "args": {
                "relative_path": "demo.py",
                "search_replace_blocks": (
                    "<<<<<<< SEARCH\n"
                    "def old_name():\n"
                    "    return 'legacy'\n"
                    "=======\n"
                    "def old_name():\n"
                    "    return 'normalized'\n"
                    ">>>>>>> REPLACE"
                ),
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )

    assert result["ok"] is False
    assert result["result"]["code"] == "search_replace_mismatch"
    assert result["result"]["reason_code"] == "SEARCH_REPLACE_NOT_FOUND"
    assert result["result"]["relative_path"] == "demo.py"
    assert "def old_name():" in str(result["result"]["search_text"] or "")


def test_execute_tool_step_edit_file_reports_fresh_buffer_snippet_on_grounding_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "demo.py"
    target.write_text("def current_name():\n    return 'live'\n", encoding="utf-8")
    execution_context = {
        "last_read_buffer": {
            "relative_path": "demo.py",
            "content": "def old_name():\n    return 'legacy'\n",
        }
    }
    edit_called = False

    def fake_edit_file(**_kwargs):
        nonlocal edit_called
        edit_called = True
        return {"operation": "edit_file", "changed_files": ["demo.py"]}

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.edit_file", fake_edit_file)

    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "edit_file",
            "args": {
                "relative_path": "demo.py",
                "search_replace_blocks": (
                    "<<<<<<< SEARCH\n"
                    "def old_name():\n"
                    "    return 'legacy'\n"
                    "=======\n"
                    "def old_name():\n"
                    "    return 'normalized'\n"
                    ">>>>>>> REPLACE\n"
                ),
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
        execution_context=execution_context,
    )

    assert edit_called is False
    assert result["ok"] is False
    assert result["result"]["code"] == "SEARCH_REPLACE_MISMATCH"
    assert "def old_name():" in str(result["result"]["search_text"] or "")
    assert "pre_loaded buffer differs from live file" in str(result["result"]["failure_analysis"] or "")
    assert result["result"]["reason_code"] == "SEARCH_REPLACE_MISMATCH"


def test_execute_tool_step_edit_file_accepts_normalized_newline_grounding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "demo.py"
    target.write_text("def current_name():\n    return 'live'", encoding="utf-8")
    execution_context = {
        "last_read_buffer": {
            "relative_path": "demo.py",
            "content": "def current_name():\n    return 'live'",
        }
    }
    captured: dict[str, Any] = {}

    def fake_edit_file(**kwargs):
        captured.update(kwargs)
        return {"operation": "edit_file", "changed_files": ["demo.py"]}

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.edit_file", fake_edit_file)

    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "edit_file",
            "args": {
                "relative_path": "demo.py",
                "search_replace_blocks": (
                    "<<<<<<< SEARCH\n"
                    "def current_name():\n"
                    "    return 'live'\n"
                    "=======\n"
                    "def current_name():\n"
                    "    return 'normalized'\n"
                    ">>>>>>> REPLACE\n"
                ),
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
        execution_context=execution_context,
    )

    assert result["ok"] is True
    assert captured["relative_path"] == "demo.py"


def test_execute_tool_loop_rejects_edit_file_without_exact_anchor_window_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "demo.py"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    monkeypatch.setattr(
        "backend.services.devexecutor_tool_loop_service.edit_file",
        lambda **_kwargs: {"operation": "edit_file", "changed_files": ["demo.py"]},
    )

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=[
            {
                "tool": "read_range",
                "args": {"relative_path": "demo.py", "start_line": 1, "end_line": 1},
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 2,
                    "end_line": 3,
                    "symbol": "demo.target",
                    "signature": "def target() -> None:",
                },
            },
            {
                "tool": "edit_file",
                "args": {
                    "relative_path": "demo.py",
                    "search_replace_blocks": "<<<<<<< SEARCH\nline2\n=======\nline2 changed\n>>>>>>> REPLACE",
                },
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 2,
                    "end_line": 3,
                    "symbol": "demo.target",
                    "signature": "def target() -> None:",
                },
            },
        ],
        allow_prefixes=[["git"]],
    )

    assert transcript[-1]["ok"] is False
    assert transcript[-1]["result"]["code"] == "PLANNER_SKIPPED_ANCHOR_WINDOW_READ"
    assert transcript[-1]["result"]["required_start_line"] == 2
    assert transcript[-1]["result"]["required_end_line"] == 3
    assert transcript[-1]["result"]["last_read_anchor_window_matched"] is False


def test_execute_tool_loop_allows_edit_file_after_exact_anchor_window_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "demo.py"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    def fake_edit_file(**kwargs):
        captured.update(kwargs)
        return {"operation": "edit_file", "changed_files": ["demo.py"]}

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.edit_file", fake_edit_file)

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=[
            {
                "tool": "read_range",
                "args": {"relative_path": "demo.py", "start_line": 2, "end_line": 3},
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 2,
                    "end_line": 3,
                    "symbol": "demo.target",
                },
            },
            {
                "tool": "edit_file",
                "args": {
                    "relative_path": "demo.py",
                    "search_replace_blocks": (
                        "<<<<<<< SEARCH\n" "line2\n" "line3\n" "=======\n" "line2 changed\n" "line3\n" ">>>>>>> REPLACE"
                    ),
                    "__aix_exact_anchor_buffer": "line2\nline3\n",
                    "read_source": {"step_idx": 0, "file": "demo.py"},
                },
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 2,
                    "end_line": 3,
                    "symbol": "demo.target",
                },
            },
        ],
        allow_prefixes=[["git"]],
    )

    edit_entry = next(entry for entry in transcript if entry["tool"] == "edit_file")
    assert edit_entry["ok"] is True
    assert captured["relative_path"] == "demo.py"
    assert edit_entry["result"]["contract_validation"]["status"] == "passed"
    assert edit_entry["result"]["contract_validation"]["mode"] == "exact_anchor_buffer"
    assert edit_entry["result"]["contract_validation"]["exact_match_with_last_read"] is True


def test_execute_tool_loop_rejects_partial_search_after_exact_anchor_window_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "demo.py"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    monkeypatch.setattr(
        "backend.services.devexecutor_tool_loop_service.edit_file",
        lambda **_kwargs: {"operation": "edit_file", "changed_files": ["demo.py"]},
    )

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=[
            {
                "tool": "read_range",
                "args": {"relative_path": "demo.py", "start_line": 2, "end_line": 3},
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 2,
                    "end_line": 3,
                    "symbol": "demo.target",
                },
            },
            {
                "tool": "edit_file",
                "args": {
                    "relative_path": "demo.py",
                    "search_replace_blocks": "<<<<<<< SEARCH\nline2\n=======\nline2 changed\n>>>>>>> REPLACE",
                    "read_source": {"step_idx": 0, "file": "demo.py"},
                },
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 2,
                    "end_line": 3,
                    "symbol": "demo.target",
                },
            },
        ],
        allow_prefixes=[["git"]],
    )

    assert transcript[-1]["ok"] is False
    assert transcript[-1]["result"]["code"] == "GROUNDING_VIOLATION_BUFFER_MISMATCH"
    assert transcript[-1]["result"]["contract_validation"]["status"] == "failed"
    assert transcript[-1]["result"]["contract_validation"]["mode"] == "exact_anchor_buffer"
    assert transcript[-1]["result"]["contract_validation"]["exact_match_with_last_read"] is False


def test_execute_tool_loop_classifies_stale_anchor_when_workspace_changes_after_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "demo.py"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    original_read_file = tool_loop_module.read_file
    mutated = False

    def fake_read_file(**kwargs):
        nonlocal mutated
        relative_path = str(kwargs.get("relative_path") or "")
        if relative_path == "demo.py" and not mutated:
            mutated = True
            target.write_text("line1\nline2 changed\nline3\n", encoding="utf-8")
        return original_read_file(**kwargs)

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.read_file", fake_read_file)
    monkeypatch.setattr(
        "backend.services.devexecutor_tool_loop_service.edit_file",
        lambda **_kwargs: {"operation": "edit_file", "changed_files": ["demo.py"]},
    )

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=[
            {
                "tool": "read_range",
                "args": {"relative_path": "demo.py", "start_line": 2, "end_line": 3},
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 2,
                    "end_line": 3,
                    "symbol": "demo.target",
                },
            },
            {
                "tool": "edit_file",
                "args": {
                    "relative_path": "demo.py",
                    "search_replace_blocks": (
                        "<<<<<<< SEARCH\n" "line2\n" "line3\n" "=======\n" "line2 changed\n" "line3\n" ">>>>>>> REPLACE"
                    ),
                    "__aix_exact_anchor_buffer": "line2\nline3\n",
                    "read_source": {"step_idx": 0, "file": "demo.py"},
                },
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 2,
                    "end_line": 3,
                    "symbol": "demo.target",
                },
            },
        ],
        allow_prefixes=[["git"]],
    )

    assert transcript[-1]["ok"] is False
    assert transcript[-1]["result"]["code"] == "GROUNDING_VIOLATION_BUFFER_MISMATCH"
    assert transcript[-1]["result"]["reason_code"] == "STALE_ANCHOR_WORKSPACE_MUTATED"
    assert "STALE_ANCHOR_WORKSPACE_MUTATED" in transcript[-1]["result"]["message"]
    assert transcript[-1]["result"]["contract_validation"]["status"] == "failed"
    assert transcript[-1]["result"]["contract_validation"]["exact_match_with_last_read"] is True
    assert transcript[-1]["result"]["contract_validation"]["exact_match_with_current_anchor_buffer"] is False
    assert transcript[-1]["result"]["contract_validation"]["stale_anchor_workspace_mutated"] is True


def test_execute_tool_loop_runs_declared_validation_after_anchor_edit_and_cmd_run_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "demo.py"
    target.write_text("def demo():\n    return 1\n", encoding="utf-8")

    def fake_edit_file(**_kwargs):
        target.write_text("def demo():\n    return 2\n", encoding="utf-8")
        return {"operation": "edit_file", "changed_files": ["demo.py"]}

    commands: list[list[str]] = []

    def fake_run_command(**kwargs):
        command = [str(item) for item in kwargs["command"]]
        commands.append(command)
        return {
            "command": command,
            "returncode": 0,
            "timed_out": False,
            "stdout": "1 PASSED\n",
            "stderr": "",
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.edit_file", fake_edit_file)
    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=[
            {
                "tool": "read_range",
                "args": {"relative_path": "demo.py", "start_line": 1, "end_line": 2},
                "output_content": "def demo():\n    return 1\n",
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 1,
                    "end_line": 2,
                    "symbol": "demo.target",
                },
            },
            {
                "tool": "edit_file",
                "args": {
                    "relative_path": "demo.py",
                    "search_replace_blocks": (
                        "<<<<<<< SEARCH\n"
                        "def demo():\n"
                        "    return 1\n"
                        "=======\n"
                        "def demo():\n"
                        "    return 2\n"
                        ">>>>>>> REPLACE"
                    ),
                    "__aix_exact_anchor_buffer": "def demo():\n    return 1\n",
                    "read_source": {"step_idx": 0, "file": "demo.py"},
                },
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 1,
                    "end_line": 2,
                    "symbol": "demo.target",
                },
            },
            {
                "tool": "cmd.run",
                "args": {
                    "command_text": "python -m pytest tests/test_demo.py -q --tb=short",
                    "timeout_seconds": 5.0,
                },
            },
        ],
        allow_prefixes=[["python"], ["pytest"], ["git"]],
    )

    assert transcript[0]["tool"] == "read_range"
    assert transcript[1]["tool"] == "edit_file"
    assert transcript[-1]["tool"] == "cmd.run"
    assert transcript[2]["auto_injected"] is True
    assert transcript[2]["tool"] == "cmd.run"
    assert transcript[3]["tool"] == "validation.preflight"
    assert transcript[3]["ok"] is True
    assert all(entry["ok"] for entry in transcript)
    assert transcript[1]["result"]["contract_validation"]["declared_validation"]["status"] == "pending"
    assert transcript[1]["result"]["contract_validation"]["declared_validation"]["enforced_at_tool"] == "cmd.run"
    assert transcript[-1]["result"]["contract_validation"]["status"] == "passed"
    assert commands[0] == ["python", "-c", "import os; print(os.getenv('PYTHONPATH', ''))"]
    assert commands[1] == ["python", "-m", "pytest", "--collect-only", "tests/test_demo.py", "-q", "--tb=short"]
    assert commands[2] == ["python", "-m", "pytest", "tests/test_demo.py", "-q", "--tb=short"]


def test_execute_tool_loop_retries_declared_validation_once_before_terminal_cmd_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "demo.py"
    target.write_text("def demo():\n    return 1\n", encoding="utf-8")

    def fake_edit_file(**_kwargs):
        target.write_text("def demo():\n    return 2\n", encoding="utf-8")
        return {"operation": "edit_file", "changed_files": ["demo.py"]}

    calls: list[list[str]] = []

    def fake_run_command(**kwargs):
        command = [str(item) for item in kwargs["command"]]
        calls.append(command)
        actual_validation_attempts = sum(
            1 for recorded in calls if recorded[:3] == ["python", "-m", "pytest"] and "--collect-only" not in recorded
        )
        if (
            command[:3] == ["python", "-m", "pytest"]
            and "--collect-only" not in command
            and actual_validation_attempts == 1
        ):
            return {
                "command": command,
                "returncode": 1,
                "timed_out": False,
                "stdout": "FAILED\n",
                "stderr": "assertion failed\n",
            }
        return {
            "command": command,
            "returncode": 0,
            "timed_out": False,
            "stdout": "1 PASSED\n",
            "stderr": "",
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.edit_file", fake_edit_file)
    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=[
            {
                "tool": "read_range",
                "args": {"relative_path": "demo.py", "start_line": 1, "end_line": 2},
                "output_content": "def demo():\n    return 1\n",
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 1,
                    "end_line": 2,
                    "symbol": "demo.target",
                },
            },
            {
                "tool": "edit_file",
                "args": {
                    "relative_path": "demo.py",
                    "search_replace_blocks": (
                        "<<<<<<< SEARCH\n"
                        "def demo():\n"
                        "    return 1\n"
                        "=======\n"
                        "def demo():\n"
                        "    return 2\n"
                        ">>>>>>> REPLACE"
                    ),
                    "__aix_exact_anchor_buffer": "def demo():\n    return 1\n",
                    "read_source": {"step_idx": 0, "file": "demo.py"},
                },
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 1,
                    "end_line": 2,
                    "symbol": "demo.target",
                },
            },
            {
                "tool": "cmd.run",
                "args": {
                    "command_text": "python -m pytest tests/test_demo.py -q --tb=short",
                    "timeout_seconds": 5.0,
                },
            },
        ],
        allow_prefixes=[["python"], ["pytest"], ["git"]],
    )

    assert [entry["tool"] for entry in transcript] == [
        "read_range",
        "edit_file",
        "cmd.run",
        "validation.preflight",
        "cmd.run",
    ]
    assert all(entry["ok"] for entry in transcript)
    assert transcript[1]["result"]["contract_validation"]["declared_validation"]["status"] == "pending"
    assert transcript[2]["auto_injected"] is True
    assert transcript[2]["result"]["stdout"] == "SYNTAX_OK"
    assert transcript[3]["tool"] == "validation.preflight"
    assert transcript[3]["ok"] is True
    assert transcript[4]["ok"] is True
    assert transcript[4]["result"]["contract_validation"]["attempts_used"] == 2
    assert transcript[4]["result"]["stdout"] == "1 PASSED\n"
    assert len(calls) == 4


def test_execute_tool_loop_blocks_terminal_cmd_run_failure_via_declared_validation_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "demo.py"
    target.write_text("def demo():\n    return 1\n", encoding="utf-8")

    def fake_edit_file(**_kwargs):
        target.write_text("def demo():\n    return 2\n", encoding="utf-8")
        return {"operation": "edit_file", "changed_files": ["demo.py"]}

    def fake_run_command(**kwargs):
        command = [str(item) for item in kwargs["command"]]
        if command[:3] == ["python", "-m", "pytest"] and "--collect-only" not in command:
            return {
                "command": command,
                "returncode": 1,
                "timed_out": False,
                "stdout": "FAILED\n",
                "stderr": "assertion failed\n",
            }
        return {
            "command": command,
            "returncode": 0,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.edit_file", fake_edit_file)
    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=[
            {
                "tool": "read_range",
                "args": {"relative_path": "demo.py", "start_line": 1, "end_line": 2},
                "output_content": "def demo():\n    return 1\n",
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 1,
                    "end_line": 2,
                    "symbol": "demo.target",
                },
            },
            {
                "tool": "edit_file",
                "args": {
                    "relative_path": "demo.py",
                    "search_replace_blocks": (
                        "<<<<<<< SEARCH\n"
                        "def demo():\n"
                        "    return 1\n"
                        "=======\n"
                        "def demo():\n"
                        "    return 2\n"
                        ">>>>>>> REPLACE"
                    ),
                    "__aix_exact_anchor_buffer": "def demo():\n    return 1\n",
                    "read_source": {"step_idx": 0, "file": "demo.py"},
                },
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 1,
                    "end_line": 2,
                    "symbol": "demo.target",
                },
            },
            {
                "tool": "cmd.run",
                "args": {
                    "command_text": "python -m pytest tests/test_demo.py -q --tb=short",
                    "timeout_seconds": 5.0,
                },
            },
        ],
        allow_prefixes=[["python"], ["pytest"], ["git"]],
    )

    assert [entry["tool"] for entry in transcript] == [
        "read_range",
        "edit_file",
        "cmd.run",
        "validation.preflight",
        "cmd.run",
    ]
    assert transcript[3]["ok"] is True
    assert transcript[-1]["ok"] is False
    assert transcript[-1]["tool"] == "cmd.run"
    assert transcript[-1]["result"]["contract_validation"]["status"] == "failed"
    assert transcript[-1]["result"]["contract_validation"]["attempts_used"] == 2
    assert len(transcript[-1]["result"]["contract_validation"]["validation_attempts"]) == 2
    assert target.read_text(encoding="utf-8") == "def demo():\n    return 2\n"


def test_execute_tool_loop_accepts_non_pytest_declared_validation_on_exit_code_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "demo.py"
    target.write_text("def demo():\n    return 1\n", encoding="utf-8")

    def fake_edit_file(**_kwargs):
        target.write_text("def demo():\n    return 2\n", encoding="utf-8")
        return {"operation": "edit_file", "changed_files": ["demo.py"]}

    def fake_run_command(**kwargs):
        command = [str(item) for item in kwargs["command"]]
        return {
            "command": command,
            "returncode": 0,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.edit_file", fake_edit_file)
    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=[
            {
                "tool": "read_range",
                "args": {"relative_path": "demo.py", "start_line": 1, "end_line": 2},
                "output_content": "def demo():\n    return 1\n",
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 1,
                    "end_line": 2,
                    "symbol": "demo.target",
                },
            },
            {
                "tool": "edit_file",
                "args": {
                    "relative_path": "demo.py",
                    "search_replace_blocks": (
                        "<<<<<<< SEARCH\n"
                        "def demo():\n"
                        "    return 1\n"
                        "=======\n"
                        "def demo():\n"
                        "    return 2\n"
                        ">>>>>>> REPLACE"
                    ),
                    "__aix_exact_anchor_buffer": "def demo():\n    return 1\n",
                    "read_source": {"step_idx": 0, "file": "demo.py"},
                },
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 1,
                    "end_line": 2,
                    "symbol": "demo.target",
                },
            },
            {
                "tool": "cmd.run",
                "args": {
                    "command_text": "python -c \"print('ok')\"",
                    "timeout_seconds": 5.0,
                },
            },
        ],
        allow_prefixes=[["python"], ["git"]],
    )

    assert transcript[-1]["tool"] == "cmd.run"
    assert transcript[-1]["ok"] is True
    assert transcript[-1]["result"]["contract_validation"]["status"] == "passed"
    assert transcript[-1]["result"]["contract_validation"]["requires_passed_marker"] is False


def test_execute_tool_loop_records_declared_validation_deny_audit_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "demo.py"
    target.write_text("def demo():\n    return 1\n", encoding="utf-8")

    def fake_edit_file(**_kwargs):
        target.write_text("def demo():\n    return 2\n", encoding="utf-8")
        return {"operation": "edit_file", "changed_files": ["demo.py"]}

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.edit_file", fake_edit_file)

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=[
            {
                "tool": "read_range",
                "args": {"relative_path": "demo.py", "start_line": 1, "end_line": 2},
                "output_content": "def demo():\n    return 1\n",
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 1,
                    "end_line": 2,
                    "symbol": "demo.target",
                },
            },
            {
                "tool": "edit_file",
                "args": {
                    "relative_path": "demo.py",
                    "search_replace_blocks": (
                        "<<<<<<< SEARCH\n"
                        "def demo():\n"
                        "    return 1\n"
                        "=======\n"
                        "def demo():\n"
                        "    return 2\n"
                        ">>>>>>> REPLACE"
                    ),
                    "__aix_exact_anchor_buffer": "def demo():\n    return 1\n",
                    "read_source": {"step_idx": 0, "file": "demo.py"},
                },
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 1,
                    "end_line": 2,
                    "symbol": "demo.target",
                },
            },
            {
                "tool": "cmd.run",
                "args": {
                    "command_text": "whoami",
                    "timeout_seconds": 5.0,
                },
            },
        ],
        allow_prefixes=[["python"], ["git"]],
    )

    assert transcript[-1]["tool"] == "cmd.run"
    assert transcript[-1]["ok"] is False
    contract_validation = transcript[-1]["result"]["contract_validation"]
    assert contract_validation["status"] == "failed"
    assert contract_validation["allowlist_compliance"] == "denied"
    assert contract_validation["allowlist_source"] == "allow_prefixes"
    assert contract_validation["classification_mapping"] == {
        "classification": "POLICY_FAIL",
        "reason_code": "COMMAND_DENIED",
    }
    attempt = contract_validation["validation_attempts"][0]
    assert attempt["parser_result"] == "command_denied"
    assert attempt["allowlist_compliance"] == "denied"
    assert attempt["allowlist_source"] == "allow_prefixes"
    assert attempt["classification_mapping"] == {
        "classification": "POLICY_FAIL",
        "reason_code": "COMMAND_DENIED",
    }


def test_execute_tool_loop_appends_declared_validation_cmd_run_when_steps_omit_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "demo.py"
    target.write_text("def demo():\n    return 1\n", encoding="utf-8")

    def fake_edit_file(**_kwargs):
        target.write_text("def demo():\n    return 2\n", encoding="utf-8")
        return {"operation": "edit_file", "changed_files": ["demo.py"]}

    commands: list[list[str]] = []

    def fake_run_command(**kwargs):
        command = [str(item) for item in kwargs["command"]]
        commands.append(command)
        return {
            "command": command,
            "returncode": 0,
            "timed_out": False,
            "stdout": "1 PASSED\n",
            "stderr": "",
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.edit_file", fake_edit_file)
    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=[
            {
                "tool": "read_range",
                "args": {"relative_path": "demo.py", "start_line": 1, "end_line": 2},
                "output_content": "def demo():\n    return 1\n",
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 1,
                    "end_line": 2,
                    "symbol": "demo.target",
                },
            },
            {
                "tool": "edit_file",
                "args": {
                    "relative_path": "demo.py",
                    "search_replace_blocks": (
                        "<<<<<<< SEARCH\n"
                        "def demo():\n"
                        "    return 1\n"
                        "=======\n"
                        "def demo():\n"
                        "    return 2\n"
                        ">>>>>>> REPLACE"
                    ),
                    "__aix_exact_anchor_buffer": "def demo():\n    return 1\n",
                    "read_source": {"step_idx": 0, "file": "demo.py"},
                },
                "planner_anchor_window": {
                    "relative_path": "demo.py",
                    "start_line": 1,
                    "end_line": 2,
                    "symbol": "demo.target",
                },
            },
        ],
        allow_prefixes=[["python"], ["pytest"], ["git"]],
        execution_context={
            "declared_validation_command": {
                "command": ["python", "-m", "pytest", "tests/test_demo.py", "-q", "--tb=short"],
                "command_text": "python -m pytest tests/test_demo.py -q --tb=short",
                "timeout_seconds": 5.0,
            }
        },
    )

    assert [entry["tool"] for entry in transcript] == [
        "read_range",
        "edit_file",
        "cmd.run",
        "validation.preflight",
        "cmd.run",
    ]
    assert transcript[2]["auto_injected"] is True
    assert transcript[3]["ok"] is True
    assert transcript[-1]["ok"] is True
    assert transcript[-1]["result"]["contract_validation"]["status"] == "passed"
    assert commands == [
        ["python", "-c", "import os; print(os.getenv('PYTHONPATH', ''))"],
        ["python", "-m", "pytest", "--collect-only", "tests/test_demo.py", "-q", "--tb=short"],
        ["python", "-m", "pytest", "tests/test_demo.py", "-q", "--tb=short"],
    ]


def test_execute_tool_step_blocks_removed_replace_in_function_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("AIX_ENABLE_REPLACE_IN_FUNCTION_FALLBACK", raising=False)

    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "replace_in_function",
            "args": {
                "relative_path": "fixture.py",
                "function_name": "FixtureService.list_items",
                "replacement": "def list_items() -> dict:\n    return {'items': []}\n",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )

    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    assert result["result"]["code"] == "removed_mutation_tool"
    assert result["result"]["primitive"] == "replace_in_function"


def test_execute_tool_step_supports_replace_function(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_replace_function(**kwargs):
        captured.update(kwargs)
        return {"operation": "replace_function", "changed_files": ["fixture.py"]}

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.replace_function", fake_replace_function)

    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "replace_function",
            "args": {
                "relative_path": "fixture.py",
                "function_name": "FixtureService.list_items",
                "qualified_symbol": "FixtureService.list_items",
                "replacement": "def list_items(self):\n    return {'items': []}\n",
                "edit_intent": "Return items payload.",
                "preserve_constraints": ["preserve exact function name"],
                "line_targets": ["return {'items': []}"],
                "rewrite_generation": {"source": "grounded_symbol_rewrite"},
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )

    assert result["ok"] is True
    assert result["result"]["operation"] == "replace_function"
    assert captured["relative_path"] == "fixture.py"
    assert captured["qualified_symbol"] == "FixtureService.list_items"
    assert "function_name" not in captured
    assert "edit_intent" not in captured
    assert "preserve_constraints" not in captured
    assert "line_targets" not in captured
    assert "rewrite_generation" not in captured


def test_execute_tool_step_replace_function_rewrite_only_fields_do_not_reach_runtime_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_replace_function(*, replacement: str, **_kwargs):
        raise AssertionError(f"unexpected replacement payload reached runtime adapter: {replacement}")

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.replace_function", fake_replace_function)

    with pytest.raises(TypeError, match="replacement"):
        execute_tool_step(
            workspace_root=tmp_path,
            step={
                "tool": "replace_function",
                "args": {
                    "relative_path": "fixture.py",
                    "qualified_symbol": "FixtureService.list_items",
                    "edit_intent": "Return items payload.",
                    "preserve_constraints": ["preserve exact function name"],
                    "line_targets": ["return {'items': []}"],
                    "rewrite_generation": {"source": "grounded_symbol_rewrite"},
                },
            },
            allow_prefixes=[["git"]],
            attempt_tracker={},
        )


def test_execute_tool_step_blocks_removed_update_call_kwarg_by_default(tmp_path: Path) -> None:
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "update_call_kwarg",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.answer_items",
                "callee": "self.list_entities",
                "kwarg": "repo_id",
                "new_value": "repo_id",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )

    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    assert result["result"]["code"] == "removed_mutation_tool"
    assert result["result"]["primitive"] == "update_call_kwarg"


def test_tool_loop_stops_on_nonzero_command_returncode(tmp_path: Path) -> None:
    steps = [
        {
            "tool": "cmd.run",
            "args": {"command": [sys.executable, "-c", "import sys; sys.exit(2)"], "timeout_seconds": 2.0},
        },
        {"tool": "write_file", "args": {"relative_path": "after.txt", "content": "should_not_happen"}},
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[[sys.executable], ["git"]])
    assert len(transcript) == 1
    assert transcript[0]["ok"] is False
    assert int(transcript[0]["result"]["returncode"]) == 2
    assert (tmp_path / "after.txt").exists() is False


def test_tool_loop_stops_on_bad_patch(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    bad_patch = "diff --git a/nope.txt b/nope.txt\n--- a/nope.txt\n+++ b/nope.txt\n@@ -1 +1 @@\n-a\n+b\n"
    steps = [
        {"tool": "apply_patch", "args": {"patch_text": bad_patch}},
        {"tool": "list_dir", "args": {"relative_path": "."}},
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])
    assert transcript[0]["ok"] is False
    assert "PATCH_APPLY_FAILED" in transcript[0]["error"]
    assert len(transcript) == 1


def test_tool_loop_runs_pre_pytest_syntax_gate(tmp_path: Path) -> None:
    steps = [
        {"tool": "write_file", "args": {"relative_path": "bad.py", "content": "def broken(:\n    pass\n"}},
        {"tool": "cmd.run", "args": {"command": ["pytest", "-q"], "timeout_seconds": 5.0}},
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["pytest"], ["git"]])
    assert len(transcript) == 1
    assert transcript[0]["ok"] is False
    assert transcript[0]["tool"] == "write_file"
    assert "CONTENT_SYNTAX_ERROR" in transcript[0]["error"]


def test_tool_loop_runs_validation_preflight_before_pytest(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"

[project.optional-dependencies]
dev = ["pytest>=8.0.0"]
""".strip(),
        encoding="utf-8",
    )

    calls: list[list[str]] = []

    def fake_run_command(**kwargs):
        command = [str(item) for item in kwargs["command"]]
        calls.append(command)
        return {
            "command": command,
            "returncode": 0,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=[{"tool": "cmd.run", "args": {"command": ["pytest", "-q"], "timeout_seconds": 5.0}}],
        allow_prefixes=[["python"], ["pytest"]],
    )

    assert [item["tool"] for item in transcript] == ["validation.preflight", "cmd.run"]
    assert transcript[0]["ok"] is True
    assert calls[0] == ["python", "-m", "pip", "install", "-e", ".[dev]"]
    assert calls[1] == ["python", "-c", "import os; print(os.getenv('PYTHONPATH', ''))"]
    assert calls[2] == ["pytest", "--collect-only", "-q"]
    assert calls[3] == ["pytest", "-q"]


def test_tool_loop_runs_validation_preflight_before_python_m_pytest(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run_command(**kwargs):
        command = [str(item) for item in kwargs["command"]]
        calls.append(command)
        return {
            "command": command,
            "returncode": 0,
            "timed_out": False,
            "stdout": "1 passed\n" if "--collect-only" not in command else "",
            "stderr": "",
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=[
            {
                "tool": "cmd.run",
                "args": {"command": ["python", "-m", "pytest", "tests/test_auth.py", "-q"], "timeout_seconds": 5.0},
            }
        ],
        allow_prefixes=[["python"], ["pytest"]],
    )

    assert [item["tool"] for item in transcript] == ["validation.preflight", "cmd.run"]
    assert transcript[0]["ok"] is True
    assert calls[0] == ["python", "-c", "import os; print(os.getenv('PYTHONPATH', ''))"]
    assert calls[1] == ["python", "-m", "pytest", "--collect-only", "tests/test_auth.py", "-q"]
    assert calls[2] == ["python", "-m", "pytest", "tests/test_auth.py", "-q"]


def test_react_tool_loop_runs_pre_pytest_syntax_gate(tmp_path: Path) -> None:
    steps = iter(
        [
            {"tool": "write_file", "args": {"relative_path": "bad.py", "content": "def broken(:\n    pass\n"}},
            {"tool": "cmd.run", "args": {"command": ["pytest", "-q"], "timeout_seconds": 5.0}},
            None,
        ]
    )

    def planner(_transcript, _context):
        return next(steps)

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[["pytest"], ["git"]],
        max_iterations=5,
    )
    assert len(transcript) == 2
    assert transcript[1]["ok"] is False
    assert transcript[1]["tool"] == "validate_syntax"
    assert "CANDIDATE_SYNTAX_INVALID" in transcript[1]["error"]


def test_react_tool_loop_runs_validation_preflight_before_pytest(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
""".strip(),
        encoding="utf-8",
    )

    calls: list[list[str]] = []

    def fake_run_command(**kwargs):
        command = [str(item) for item in kwargs["command"]]
        calls.append(command)
        return {
            "command": command,
            "returncode": 0,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)

    steps = iter([{"tool": "cmd.run", "args": {"command": ["pytest", "-q"], "timeout_seconds": 5.0}}, None])

    def planner(_transcript, _context):
        return next(steps)

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[["python"], ["pytest"]],
        max_iterations=5,
    )

    assert [item["tool"] for item in transcript] == ["validation.preflight", "cmd.run"]
    assert calls[0] == ["python", "-m", "pip", "install", "-e", "."]
    assert calls[1] == ["python", "-c", "import os; print(os.getenv('PYTHONPATH', ''))"]
    assert calls[2] == ["pytest", "--collect-only", "-q"]
    assert calls[3] == ["pytest", "-q"]


def test_tool_loop_stops_when_validation_preflight_collect_only_fails(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run_command(**kwargs):
        command = [str(item) for item in kwargs["command"]]
        calls.append(command)
        return {
            "command": command,
            "returncode": 1 if "--collect-only" in command else 0,
            "timed_out": False,
            "stdout": "",
            "stderr": "ModuleNotFoundError: No module named 'jwt'" if "--collect-only" in command else "",
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=[
            {"tool": "cmd.run", "args": {"command": ["pytest", "tests/test_auth.py", "-q"], "timeout_seconds": 5.0}}
        ],
        allow_prefixes=[["pytest"]],
    )

    assert len(transcript) == 1
    assert transcript[0]["tool"] == "validation.preflight"
    assert transcript[0]["ok"] is False
    assert "PRECHECK_ENVIRONMENT_FAILED" in transcript[0]["error"]
    assert calls[0] == ["python", "-c", "import os; print(os.getenv('PYTHONPATH', ''))"]
    assert calls[1] == ["pytest", "--collect-only", "tests/test_auth.py", "-q"]


def test_tool_loop_stops_when_validation_preflight_collect_only_fails_for_python_m_pytest(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run_command(**kwargs):
        command = [str(item) for item in kwargs["command"]]
        calls.append(command)
        return {
            "command": command,
            "returncode": 1 if "--collect-only" in command else 0,
            "timed_out": False,
            "stdout": "",
            "stderr": "import error" if "--collect-only" in command else "",
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=[
            {
                "tool": "cmd.run",
                "args": {"command": ["python", "-m", "pytest", "tests/test_auth.py", "-q"], "timeout_seconds": 5.0},
            }
        ],
        allow_prefixes=[["python"], ["pytest"]],
    )

    assert len(transcript) == 1
    assert transcript[0]["tool"] == "validation.preflight"
    assert transcript[0]["ok"] is False
    assert "PRECHECK_ENVIRONMENT_FAILED" in transcript[0]["error"]
    assert calls[0] == ["python", "-c", "import os; print(os.getenv('PYTHONPATH', ''))"]
    assert calls[1] == ["python", "-m", "pytest", "--collect-only", "tests/test_auth.py", "-q"]


def test_tool_loop_continues_when_bootstrap_fails_but_collect_only_passes(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"

[project.optional-dependencies]
dev = ["pytest>=8.0.0"]
""".strip(),
        encoding="utf-8",
    )

    calls: list[list[str]] = []

    def fake_run_command(**kwargs):
        command = [str(item) for item in kwargs["command"]]
        calls.append(command)
        if command[:4] == ["python", "-m", "pip", "install"]:
            return {
                "command": command,
                "returncode": 1,
                "timed_out": False,
                "stdout": "",
                "stderr": "pip install blocked in runtime",
            }
        return {
            "command": command,
            "returncode": 0,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=[{"tool": "cmd.run", "args": {"command": ["pytest", "-q"], "timeout_seconds": 5.0}}],
        allow_prefixes=[["python"], ["pytest"]],
    )

    assert [item["tool"] for item in transcript] == ["validation.preflight", "cmd.run"]
    assert transcript[0]["ok"] is True
    assert transcript[0]["error"] is None
    assert transcript[0]["diagnostics"]["bootstrap_warning"] == "VALIDATION_BOOTSTRAP_FAILED_CONTINUED"
    assert calls[0] == ["python", "-m", "pip", "install", "-e", ".[dev]"]
    assert calls[1] == ["python", "-c", "import os; print(os.getenv('PYTHONPATH', ''))"]
    assert calls[2] == ["pytest", "--collect-only", "-q"]
    assert calls[3] == ["pytest", "-q"]


def test_tool_loop_transcript_is_json_serializable(tmp_path: Path) -> None:
    steps = [{"tool": "write_file", "args": {"relative_path": "a.txt", "content": "x"}}]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])
    payload = json.dumps(transcript)
    assert isinstance(payload, str)


def test_react_loop_happy_path(tmp_path: Path) -> None:
    plan = iter(
        [
            {"tool": "write_file", "args": {"relative_path": "a.txt", "content": "v1"}},
            {"tool": "read_file", "args": {"relative_path": "a.txt"}},
            None,
        ]
    )

    def planner(_transcript, _context):
        return next(plan)

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[["git"]],
        max_iterations=5,
    )
    assert len(transcript) == 2
    assert all(item["ok"] for item in transcript)
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v1"


def test_react_loop_iteration_budget_exceeded(tmp_path: Path) -> None:
    def planner(_transcript, _context):
        return {"tool": "list_dir", "args": {"relative_path": "."}}

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[["git"]],
        max_iterations=2,
    )
    assert len(transcript) == 3
    assert transcript[-1]["ok"] is False
    assert "TOOL_LOOP_ITERATION_BUDGET_EXCEEDED" in transcript[-1]["error"]


def test_react_loop_runtime_budget_exceeded(tmp_path: Path) -> None:
    def planner(_transcript, _context):
        time.sleep(0.03)
        return {"tool": "list_dir", "args": {"relative_path": "."}}

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[["git"]],
        max_iterations=5,
        max_runtime_seconds=0.01,
    )
    assert transcript[-1]["ok"] is False
    assert "TOOL_LOOP_RUNTIME_BUDGET_EXCEEDED" in transcript[-1]["error"]


def test_react_loop_patch_budget_exceeded(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "backend.services.devexecutor_patch_engine_service._run_pyright_on_files",
        lambda **_kwargs: (True, ""),
    )
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    (tmp_path / "hello.txt").write_text("hello\n", encoding="utf-8")
    patch = """diff --git a/hello.txt b/hello.txt
index ce01362..94954ab 100644
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-hello
+hello world
"""
    plan = iter(
        [
            {"tool": "apply_patch", "args": {"patch_text": patch}},
            {"tool": "apply_patch", "args": {"patch_text": patch}},
            None,
        ]
    )

    def planner(_transcript, _context):
        return next(plan)

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[["git"]],
        max_iterations=5,
        max_patch_attempts=1,
    )
    assert transcript[0]["ok"] is True
    assert transcript[1]["ok"] is False
    assert "PATCH_ATTEMPT_LIMIT_EXCEEDED" in transcript[1]["error"]


def test_react_loop_policy_gate_denies_command(tmp_path: Path) -> None:
    def planner(_transcript, _context):
        return {"tool": "cmd.run", "args": {"command": ["whoami"], "timeout_seconds": 2.0}}

    def policy_gate(step, _context):
        if step.get("tool") == "cmd.run":
            return {"allow": False, "code": "command_not_allowed", "message": "manual escalation required"}
        return {"allow": True}

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[["git"]],
        policy_gate=policy_gate,
    )
    assert len(transcript) == 1
    assert transcript[0]["ok"] is False
    assert "TOOL_LOOP_POLICY_DENIED" in transcript[0]["error"]
    assert "command_not_allowed" in transcript[0]["error"]


def test_react_loop_policy_escalation_channel_must_be_approved(tmp_path: Path) -> None:
    def planner(_transcript, _context):
        return {"tool": "cmd.run", "args": {"command": ["whoami"], "timeout_seconds": 2.0}}

    def policy_gate(step, _context):
        if step.get("tool") == "cmd.run":
            return {
                "allow": False,
                "code": "requires_escalation",
                "message": "needs external runtime",
                "escalate": True,
                "escalation_channel": "direct_shell",
            }
        return {"allow": True}

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[["git"]],
        policy_gate=policy_gate,
        approved_escalation_channels={"human_review", "model_gateway"},
    )
    assert len(transcript) == 1
    assert transcript[0]["ok"] is False
    assert "POLICY_ESCALATION_CHANNEL_DENIED" in transcript[0]["error"]


def test_react_loop_policy_escalation_hook_is_called(tmp_path: Path) -> None:
    def planner(_transcript, _context):
        return {"tool": "cmd.run", "args": {"command": ["whoami"], "timeout_seconds": 2.0}}

    def policy_gate(step, _context):
        if step.get("tool") == "cmd.run":
            return {
                "allow": False,
                "code": "requires_escalation",
                "message": "needs human override",
                "escalate": True,
                "escalation_channel": "human_review",
            }
        return {"allow": True}

    escalations: list[dict[str, object]] = []

    def escalation_hook(payload):
        escalations.append(payload)

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[["git"]],
        policy_gate=policy_gate,
        escalation_hook=escalation_hook,
        approved_escalation_channels={"human_review"},
    )
    assert len(transcript) == 1
    assert transcript[0]["ok"] is False
    assert "TOOL_LOOP_POLICY_DENIED" in transcript[0]["error"]
    assert len(escalations) == 1
    assert escalations[0]["channel"] == "human_review"


def test_react_loop_feeds_failed_cmd_run_output_back_into_planner_context(tmp_path: Path) -> None:
    planner_calls: list[dict[str, object]] = []
    state = {"recovered": False}

    def planner(_transcript, context):
        planner_calls.append(dict(context))
        if len(planner_calls) == 1:
            return {
                "tool": "cmd.run",
                "args": {
                    "command": [
                        sys.executable,
                        "-c",
                        "import sys; print('boom-out'); print('boom-err', file=sys.stderr); sys.exit(2)",
                    ],
                    "timeout_seconds": 5.0,
                },
            }
        feedback = context.get("last_cmd_run_feedback")
        assert isinstance(feedback, dict)
        assert feedback["returncode"] == 2
        assert "boom-out" in str(feedback["stdout"])
        assert "boom-err" in str(feedback["stderr"])
        assert context["feedback_rounds_used"] == 1
        assert context["max_feedback_rounds"] == 2
        assert any("boom-out" in str(item) for item in context.get("planner_feedback_messages", []))
        if state["recovered"]:
            return None
        state["recovered"] = True
        return {"tool": "write_file", "args": {"relative_path": "fixed.txt", "content": "ok"}}

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[[sys.executable], ["git"]],
        max_iterations=5,
        max_feedback_rounds=2,
    )

    assert len(transcript) == 2
    assert transcript[0]["tool"] == "cmd.run"
    assert transcript[0]["ok"] is False
    assert transcript[0]["result"]["returncode"] == 2
    assert "boom-out" in transcript[0]["result"]["stdout"]
    assert "boom-err" in transcript[0]["result"]["stderr"]
    assert transcript[1]["tool"] == "write_file"
    assert transcript[1]["ok"] is True
    assert (tmp_path / "fixed.txt").read_text(encoding="utf-8") == "ok"
    assert len(planner_calls) == 3


def test_react_loop_stops_after_cmd_run_feedback_budget_is_exhausted(tmp_path: Path) -> None:
    planner_calls = {"count": 0}

    def planner(_transcript, context):
        planner_calls["count"] += 1
        return {
            "tool": "cmd.run",
            "args": {
                "command": [
                    sys.executable,
                    "-c",
                    (
                        "import sys; "
                        f"print('attempt-{planner_calls['count']}'); "
                        "print('still-bad', file=sys.stderr); "
                        "sys.exit(3)"
                    ),
                ],
                "timeout_seconds": 5.0,
            },
        }

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[[sys.executable], ["git"]],
        max_iterations=5,
        max_feedback_rounds=1,
    )

    assert planner_calls["count"] == 2
    assert len(transcript) == 2
    assert transcript[0]["tool"] == "cmd.run"
    assert transcript[0]["ok"] is False
    assert transcript[1]["tool"] == "cmd.run"
    assert transcript[1]["ok"] is False
    assert transcript[1]["result"]["returncode"] == 3


def test_tool_loop_supports_get_status_and_evidence_attach(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    steps = [
        {"tool": "get_status", "args": {}},
        {
            "tool": "evidence.attach",
            "args": {"evidence_type": "log", "evidence_ref": "artifacts/tests.log", "metadata": {"kind": "smoke"}},
        },
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])
    assert len(transcript) == 2
    assert transcript[0]["ok"] is True
    assert transcript[0]["result"]["is_git_repo"] is True
    assert transcript[1]["ok"] is True
    assert transcript[1]["result"]["operation"] == "evidence.attach"
    assert transcript[1]["result"]["evidence_ref"] == "artifacts/tests.log"


def test_tool_loop_supports_read_range_and_replace_range(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    steps = [
        {"tool": "read_range", "args": {"relative_path": "demo.txt", "start_line": 2, "end_line": 3}},
        {
            "tool": "replace_range",
            "args": {"relative_path": "demo.txt", "start_line": 2, "end_line": 2, "replacement": "two-updated"},
        },
        {"tool": "read_file", "args": {"relative_path": "demo.txt"}},
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])
    assert len(transcript) == 3
    assert all(item["ok"] for item in transcript)
    assert transcript[0]["result"]["content"] == "two\nthree\n"
    assert transcript[1]["result"]["operation"] == "replace_range"


def test_tool_loop_supports_edit_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "backend.services.devexecutor_patch_engine_service._run_pyright_on_files",
        lambda **_kwargs: (True, ""),
    )
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    (tmp_path / "demo.py").write_text("def demo():\n    return 'old'\n", encoding="utf-8")
    steps = [
        {"tool": "read_file", "args": {"relative_path": "demo.py"}},
        {
            "tool": "edit_file",
            "args": {
                "relative_path": "demo.py",
                "search_replace_blocks": (
                    "<<<<<<< SEARCH\n" "    return 'old'\n" "=======\n" "    return 'new'\n" ">>>>>>> REPLACE\n"
                ),
            },
        },
        {"tool": "read_file", "args": {"relative_path": "demo.py", "source": "filesystem"}},
    ]

    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])

    assert transcript[1]["ok"] is True
    assert transcript[1]["result"]["operation"] == "edit_file"
    assert transcript[2]["tool"] == "cmd.run"
    assert transcript[2]["auto_injected"] is True
    assert "return 'new'" in transcript[3]["result"]["content"]


def test_tool_loop_supports_edit_file_with_missing_terminal_newline(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    (tmp_path / "demo.py").write_text("value = 1", encoding="utf-8")
    steps = [
        {"tool": "read_file", "args": {"relative_path": "demo.py"}},
        {
            "tool": "edit_file",
            "args": {
                "relative_path": "demo.py",
                "search_replace_blocks": (
                    "<<<<<<< SEARCH\n" "value = 1\n" "=======\n" "value = 2\n" ">>>>>>> REPLACE\n"
                ),
            },
        },
        {"tool": "read_file", "args": {"relative_path": "demo.py", "source": "filesystem"}},
    ]

    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])

    assert transcript[1]["ok"] is True
    assert transcript[1]["result"]["operation"] == "edit_file"
    assert transcript[1]["result"]["adapter"] == "write_file_fallback"
    assert transcript[3]["result"]["content"] == "value = 2\n"


def test_execute_tool_step_replace_in_function_is_removed(tmp_path: Path) -> None:
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "replace_in_function",
            "args": {
                "relative_path": "service.py",
                "function_name": "CodeintelQueryService.list_relations",
                "replacement": (
                    "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
                    "        return {'items': [entity_id] if entity_id else []}\n"
                ),
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )
    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    assert result["result"]["code"] == "removed_mutation_tool"
    assert result["result"]["primitive"] == "replace_in_function"


def test_execute_tool_step_update_call_kwarg_is_removed(tmp_path: Path) -> None:
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "update_call_kwarg",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.answer_items",
                "callee": "self.list_entities",
                "kwarg": "repo_id",
                "new_value": "repo_id",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )
    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    assert result["result"]["code"] == "removed_mutation_tool"
    assert result["result"]["primitive"] == "update_call_kwarg"


# #1842 – body_insert compat removal
def test_execute_tool_step_edit_symbol_body_insert_is_removed_from_tool_loop(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class SomeService:\n"
        "    def do_work(self, payload):\n"
        "        result = self._process(payload)\n"
        "        return result\n",
        encoding="utf-8",
    )
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "edit_symbol",
            "args": {
                "relative_path": "service.py",
                "symbol": "SomeService.do_work",
                "mode": "body_insert",
                "anchor_pattern": "this_anchor_does_not_exist",
                "position": "after",
                "new_code": "extra = 1",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )
    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    assert result["result"]["code"] == "removed_mutation_tool"
    assert result["result"]["primitive"] == "edit_symbol"


def test_execute_tool_step_edit_symbol_body_insert_without_anchor_is_still_removed(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class SomeService:\n" "    def do_work(self, payload):\n" "        return payload\n",
        encoding="utf-8",
    )
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "edit_symbol",
            "args": {
                "relative_path": "service.py",
                "symbol": "SomeService.do_work",
                "mode": "body_insert",
                "anchor_pattern": "",
                "position": "after",
                "new_code": "extra = 1",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )
    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    assert result["result"]["code"] == "removed_mutation_tool"


def test_execute_tool_step_supports_confirm_anchor(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, relation_type=None):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "confirm_anchor",
            "args": {
                "relative_path": "service.py",
                "symbol_name": "CodeintelQueryService.list_relations",
                "anchor_quote": "async def list_relations(self, tenant_id, project_id, repo_id, relation_type=None):",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )
    assert result["ok"] is True
    assert result["result"]["reason_code"] == "MATCH"


def test_execute_tool_step_edit_file_accepts_confirm_anchor_grounding_buffer(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class ExampleService:\n"
        "    async def mutate_task(self, task_id: str, reason: str | None = None) -> dict[str, str]:\n"
        "        return {'task_id': task_id}\n",
        encoding="utf-8",
    )
    execution_context: dict[str, Any] = {}
    captured: dict[str, object] = {}

    def fake_edit_file(**kwargs):
        captured.update(kwargs)
        return {"changed_files": ["service.py"], "operation": "edit_file"}

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.edit_file", fake_edit_file)

    read_result = execute_tool_step(
        workspace_root=tmp_path,
        step={"tool": "read_range", "args": {"relative_path": "service.py", "start_line": 1, "end_line": 1}},
        allow_prefixes=[["git"]],
        attempt_tracker={},
        execution_context=execution_context,
    )
    assert read_result["ok"] is True

    anchor_result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "confirm_anchor",
            "args": {
                "relative_path": "service.py",
                "symbol_name": "ExampleService.mutate_task",
                "anchor_quote": "async def mutate_task(self, task_id: str, reason: str | None = None)",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
        execution_context=execution_context,
    )
    assert anchor_result["ok"] is True
    assert "async def mutate_task" in str(execution_context["last_read_buffer"]["content"])

    edit_result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "edit_file",
            "args": {
                "relative_path": "service.py",
                "search_replace_blocks": (
                    "<<<<<<< SEARCH\n"
                    "async def mutate_task(self, task_id: str, reason: str | None = None) -> dict[str, str]:\n"
                    "=======\n"
                    "async def mutate_task(self, task_id: str, reason: str | None = None, target_project_id: str | None = None) -> dict[str, str]:\n"
                    ">>>>>>> REPLACE\n"
                ),
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
        execution_context=execution_context,
    )

    assert edit_result["ok"] is True
    assert captured["relative_path"] == "service.py"


def test_execute_tool_step_confirm_anchor_uses_resolved_path_outside_workspace_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "projects" / "AIX" / "hyeve-aix-service"
    workspace_root.mkdir(parents=True)
    target = tmp_path / "scripts" / "wh.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "def _route_run(args: list[str]) -> int:\n" "    return 0\n",
        encoding="utf-8",
    )

    result = execute_tool_step(
        workspace_root=workspace_root,
        step={
            "tool": "confirm_anchor",
            "args": {
                "relative_path": "scripts/wh.py",
                "resolved_path": str(target),
                "symbol_name": "scripts.wh._route_run",
                "anchor_quote": "def _route_run(args: list[str]) -> int:",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )

    assert result["ok"] is True
    assert result["result"]["reason_code"] == "MATCH"


def test_execute_tool_step_edit_symbol_is_removed_from_tool_loop(tmp_path: Path) -> None:
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "edit_symbol",
            "args": {
                "relative_path": "scripts/wh.py",
                "resolved_path": str(tmp_path / "scripts" / "wh.py"),
                "symbol": "scripts.wh._route_run",
                "mode": "signature",
                "new_signature": "def _route_run(args: list[str]) -> int:",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )

    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    assert result["result"]["code"] == "removed_mutation_tool"
    assert result["result"]["primitive"] == "edit_symbol"


def test_execute_tool_step_insert_after_symbol_is_removed_from_tool_loop(tmp_path: Path) -> None:
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "insert_after_symbol",
            "args": {
                "relative_path": "scripts/wh.py",
                "resolved_path": str(tmp_path / "scripts" / "wh.py"),
                "symbol_name": "scripts.wh._route_run",
                "new_content": "\n\ndef helper() -> None:\n    pass\n",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )

    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    assert result["result"]["code"] == "removed_mutation_tool"
    assert result["result"]["primitive"] == "insert_after_symbol"


def test_execute_tool_step_insert_before_return_is_removed_from_tool_loop(tmp_path: Path) -> None:
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "insert_before_return",
            "args": {
                "relative_path": "scripts/wh.py",
                "resolved_path": str(tmp_path / "scripts" / "wh.py"),
                "function_name": "scripts.wh._route_run",
                "new_content": "    value = 1\n",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )

    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    assert result["result"]["code"] == "removed_mutation_tool"
    assert result["result"]["primitive"] == "insert_before_return"


def test_execute_tool_step_replace_in_function_is_removed_before_path_dispatch(tmp_path: Path) -> None:
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "replace_in_function",
            "args": {
                "relative_path": "scripts/wh.py",
                "resolved_path": str(tmp_path / "scripts" / "wh.py"),
                "function_name": "scripts.wh._route_run",
                "replacement": "def _route_run(args: list[str]) -> int:\n    return 0\n",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )
    assert result["ok"] is False
    assert result["result"]["code"] == "removed_mutation_tool"


def test_execute_tool_step_edit_file_strips_resolved_path_before_mutation(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_edit_file(**kwargs):
        captured.update(kwargs)
        return {"changed_files": ["scripts/wh.py"]}

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.edit_file", fake_edit_file)

    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "edit_file",
            "args": {
                "relative_path": "scripts/wh.py",
                "resolved_path": str(tmp_path / "scripts" / "wh.py"),
                "search_replace_blocks": "<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )

    assert result["ok"] is True
    assert "resolved_path" not in captured


def test_execute_tool_step_edit_file_strips_aix_transport_diag_before_mutation(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_edit_file(**kwargs):
        captured.update(kwargs)
        return {"changed_files": ["scripts/wh.py"]}

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.edit_file", fake_edit_file)

    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "edit_file",
            "args": {
                "relative_path": "scripts/wh.py",
                "search_replace_blocks": "<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE",
                "__aix_edit_file_parse_diag": {"source_shape": "canonical"},
                "__aix_extra": "trace-only",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )

    assert result["ok"] is True
    assert "__aix_edit_file_parse_diag" not in captured
    assert "__aix_extra" not in captured


def test_tool_loop_rejects_replace_in_function_even_without_confirm_anchor(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, relation_type=None):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    steps = [
        {
            "tool": "replace_in_function",
            "args": {
                "relative_path": "service.py",
                "function_name": "CodeintelQueryService.list_relations",
                "replacement": (
                    "    async def list_relations(self, tenant_id, project_id, repo_id, relation_type=None):\n"
                    "        if relation_type:\n"
                    "            return {'items': [relation_type]}\n"
                    "        return {'items': []}\n"
                ),
            },
        }
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])
    assert len(transcript) == 1
    assert transcript[0]["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in transcript[0]["error"]


def test_tool_loop_rejects_replace_in_function_even_after_confirm_anchor(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, relation_type=None):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    steps = [
        {
            "tool": "confirm_anchor",
            "args": {
                "relative_path": "service.py",
                "symbol_name": "CodeintelQueryService.list_relations",
                "anchor_quote": "async def list_relations(self, tenant_id, project_id, repo_id, relation_type=None):",
            },
        },
        {
            "tool": "replace_in_function",
            "args": {
                "relative_path": "service.py",
                "function_name": "CodeintelQueryService.list_relations",
                "replacement": (
                    "async def list_relations(self, tenant_id, project_id, repo_id, relation_type=None):\n"
                    "    if relation_type:\n"
                    "        return {'items': [relation_type]}\n"
                    "    return {'items': []}\n"
                ),
            },
        },
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])
    assert len(transcript) == 2
    assert transcript[0]["ok"] is True
    assert transcript[1]["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in transcript[1]["error"]
    assert "if relation_type:" not in target.read_text(encoding="utf-8")


def test_tool_loop_reports_removed_mutation_tool_after_confirm_anchor(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, relation_type=None):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    mutated = {"done": False}

    def policy_gate(step: dict, context: dict) -> dict | None:
        if not mutated["done"] and context["index"] == 1 and str(step.get("tool") or "") == "replace_in_function":
            target.write_text(target.read_text(encoding="utf-8") + "\nHELPER_FLAG = True\n", encoding="utf-8")
            mutated["done"] = True
        return None

    steps = [
        {
            "tool": "confirm_anchor",
            "args": {
                "relative_path": "service.py",
                "symbol_name": "CodeintelQueryService.list_relations",
                "anchor_quote": "async def list_relations(self, tenant_id, project_id, repo_id, relation_type=None):",
            },
        },
        {
            "tool": "replace_in_function",
            "args": {
                "relative_path": "service.py",
                "function_name": "CodeintelQueryService.list_relations",
                "replacement": (
                    "async def list_relations(self, tenant_id, project_id, repo_id, relation_type=None):\n"
                    "    if relation_type:\n"
                    "        return {'items': [relation_type]}\n"
                    "    return {'items': []}\n"
                ),
            },
        },
    ]
    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=steps,
        allow_prefixes=[["git"]],
        policy_gate=policy_gate,
    )
    assert len(transcript) == 2
    assert transcript[0]["ok"] is True
    assert transcript[1]["ok"] is False
    assert transcript[1]["result"]["code"] == "removed_mutation_tool"
    assert "REMOVED_MUTATION_TOOL" in transcript[1]["error"]


def test_tool_loop_rejects_edit_symbol_after_confirm_anchor(tmp_path: Path) -> None:
    target = tmp_path / "fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    steps = [
        {
            "tool": "confirm_anchor",
            "args": {
                "relative_path": "fixture.py",
                "symbol_name": "FixtureService.list_items",
                "anchor_quote": "async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:",
            },
        },
        {
            "tool": "edit_symbol",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.list_items",
                "mode": "signature",
                "new_signature": "async def list_items(self, tenant_id: str, limit: int, offset: int, item_type: str | None = None) -> dict",
            },
        },
        {
            "tool": "edit_symbol",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.list_items",
                "mode": "body_insert",
                "anchor_pattern": "return {'items': []}",
                "position": "before",
                "new_code": "if item_type:\n    pass",
            },
        },
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])
    assert len(transcript) == 2
    assert transcript[0]["ok"] is True
    assert transcript[1]["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in transcript[1]["error"]
    content = target.read_text(encoding="utf-8")
    assert "item_type: str | None = None" not in content
    assert "if item_type:" not in content


def test_tool_loop_allows_extend_python_signature_after_confirm_anchor(tmp_path: Path) -> None:
    target = tmp_path / "fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def mutate_task(self, *, task_id: str, reason: str | None = None) -> dict[str, str]:\n"
        "        return {'task_id': task_id}\n",
        encoding="utf-8",
    )
    steps = [
        {
            "tool": "confirm_anchor",
            "args": {
                "relative_path": "fixture.py",
                "symbol_name": "FixtureService.mutate_task",
                "anchor_quote": "async def mutate_task(self, *, task_id: str, reason: str | None = None) -> dict[str, str]:",
            },
        },
        {
            "tool": "extend_python_signature",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.mutate_task",
                "parameter_name": "target_project_id",
                "parameter_type": "str | None",
                "default_value": "None",
                "parameter_kind": "auto",
            },
        },
    ]

    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])

    assert len(transcript) == 3
    assert transcript[0]["ok"] is True
    assert transcript[1]["ok"] is True
    assert transcript[1]["result"]["operation"] == "extend_python_signature"
    assert transcript[2]["ok"] is True
    assert transcript[2]["auto_injected"] is True
    assert "target_project_id: str | None = None" in target.read_text(encoding="utf-8")


def test_tool_loop_refreshes_grounding_for_sequential_extend_python_signature_steps(tmp_path: Path) -> None:
    target = tmp_path / "fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def mutate_task(self, *, task_id: str, reason: str | None = None) -> dict[str, str]:\n"
        "        return {'task_id': task_id}\n",
        encoding="utf-8",
    )
    steps = [
        {
            "tool": "confirm_anchor",
            "args": {
                "relative_path": "fixture.py",
                "symbol_name": "FixtureService.mutate_task",
                "anchor_quote": "async def mutate_task(self, *, task_id: str, reason: str | None = None) -> dict[str, str]:",
            },
        },
        {
            "tool": "extend_python_signature",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.mutate_task",
                "parameter_name": "target_project_id",
                "parameter_type": "str | None",
                "default_value": "None",
                "parameter_kind": "auto",
            },
        },
        {
            "tool": "extend_python_signature",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.mutate_task",
                "parameter_name": "target_task_id",
                "parameter_type": "str | None",
                "default_value": "None",
                "parameter_kind": "auto",
            },
        },
    ]

    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])

    assert [entry["tool"] for entry in transcript] == [
        "confirm_anchor",
        "extend_python_signature",
        "cmd.run",
        "extend_python_signature",
        "cmd.run",
    ]
    assert all(entry["ok"] is True for entry in transcript)
    content = target.read_text(encoding="utf-8")
    assert "target_project_id: str | None = None" in content
    assert "target_task_id: str | None = None" in content


def test_tool_loop_refreshes_stale_grounding_hashes_before_sequential_extend_python_signature(
    tmp_path: Path,
) -> None:
    target = tmp_path / "fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def mutate_task(self, *, task_id: str, reason: str | None = None) -> dict[str, str]:\n"
        "        return {'task_id': task_id}\n",
        encoding="utf-8",
    )
    steps = [
        {
            "tool": "confirm_anchor",
            "args": {
                "relative_path": "fixture.py",
                "symbol_name": "FixtureService.mutate_task",
                "anchor_quote": "async def mutate_task(self, *, task_id: str, reason: str | None = None) -> dict[str, str]:",
            },
        },
        {
            "tool": "extend_python_signature",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.mutate_task",
                "parameter_name": "target_project_id",
                "parameter_type": "str | None",
                "default_value": "None",
                "parameter_kind": "auto",
            },
        },
        {
            "tool": "extend_python_signature",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.mutate_task",
                "parameter_name": "target_task_id",
                "parameter_type": "str | None",
                "default_value": "None",
                "parameter_kind": "auto",
                "expected_file_blob_sha": "stale-blob",
                "expected_symbol_source_hash": "stale-hash",
            },
        },
    ]

    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])

    assert [entry["tool"] for entry in transcript] == [
        "confirm_anchor",
        "extend_python_signature",
        "cmd.run",
        "extend_python_signature",
        "cmd.run",
    ]
    assert all(entry["ok"] is True for entry in transcript)
    grounded_symbol = transcript[3]["result"]["grounded_symbol"]
    assert grounded_symbol["file_blob_sha"] != "stale-blob"
    assert grounded_symbol["symbol_source_hash"] != "stale-hash"
    content = target.read_text(encoding="utf-8")
    assert "target_project_id: str | None = None" in content
    assert "target_task_id: str | None = None" in content


def test_tool_loop_allows_extend_python_signature_after_confirm_anchor_with_module_qualified_symbol(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "backend" / "services" / "workhub_contract_service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "class WorkhubContractService:\n"
        "    async def mutate_task(self, *, task_id: str, reason: str | None = None) -> dict[str, str]:\n"
        "        return {'task_id': task_id}\n",
        encoding="utf-8",
    )

    def _fake_verify_symbol_anchor(*, file_path, symbol_name, anchor_quote, symbol_type="function"):  # noqa: ARG001
        return {
            "matched": True,
            "reason_code": "MATCH",
            "relative_path": "backend/services/workhub_contract_service.py",
            "symbol_name": "mutate_task",
            "qualified_name": "WorkhubContractService.mutate_task",
            "symbol_source": (
                "async def mutate_task(self, *, task_id: str, reason: str | None = None) -> dict[str, str]:\n"
                "        return {'task_id': task_id}\n"
            ),
        }

    monkeypatch.setattr(tool_loop_module, "verify_symbol_anchor", _fake_verify_symbol_anchor)

    steps = [
        {
            "tool": "read_range",
            "args": {
                "relative_path": "backend/services/workhub_contract_service.py",
                "start_line": 1,
                "end_line": 5,
            },
        },
        {
            "tool": "confirm_anchor",
            "args": {
                "relative_path": "backend/services/workhub_contract_service.py",
                "symbol_name": "backend.services.workhub_contract_service.WorkhubContractService.mutate_task",
                "anchor_quote": "async def mutate_task(self, *, task_id: str, reason: str | None = None) -> dict[str, str]:",
            },
        },
        {
            "tool": "extend_python_signature",
            "args": {
                "relative_path": "backend/services/workhub_contract_service.py",
                "symbol": "backend.services.workhub_contract_service.WorkhubContractService.mutate_task",
                "parameter_name": "target_project_id",
                "parameter_type": "str | None",
                "default_value": "None",
                "parameter_kind": "auto",
            },
        },
    ]

    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])

    assert len(transcript) == 4
    assert transcript[0]["ok"] is True
    assert transcript[1]["ok"] is True
    assert transcript[2]["ok"] is True
    assert transcript[2]["result"]["operation"] == "extend_python_signature"
    assert transcript[3]["ok"] is True
    assert "target_project_id: str | None = None" in target.read_text(encoding="utf-8")


def test_tool_loop_rejects_extend_python_signature_without_confirm_anchor(tmp_path: Path) -> None:
    target = tmp_path / "fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def mutate_task(self, *, task_id: str, reason: str | None = None) -> dict[str, str]:\n"
        "        return {'task_id': task_id}\n",
        encoding="utf-8",
    )
    steps = [
        {
            "tool": "extend_python_signature",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.mutate_task",
                "parameter_name": "target_project_id",
                "parameter_type": "str | None",
                "default_value": "None",
            },
        }
    ]

    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])

    assert len(transcript) == 1
    assert transcript[0]["ok"] is False
    assert "GROUNDING_CONFIRMATION_REQUIRED" in transcript[0]["error"]


def test_tool_loop_rejects_extend_python_signature_after_scope_drift_from_confirmed_anchor(tmp_path: Path) -> None:
    target = tmp_path / "fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def mutate_task(self, *, task_id: str, reason: str | None = None) -> dict[str, str]:\n"
        "        return {'task_id': task_id}\n\n"
        "    async def finalize_task(self, *, task_id: str) -> dict[str, str]:\n"
        "        return {'task_id': task_id}\n",
        encoding="utf-8",
    )
    steps = [
        {
            "tool": "confirm_anchor",
            "args": {
                "relative_path": "fixture.py",
                "symbol_name": "FixtureService.mutate_task",
                "anchor_quote": "async def mutate_task(self, *, task_id: str, reason: str | None = None) -> dict[str, str]:",
            },
        },
        {
            "tool": "extend_python_signature",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.finalize_task",
                "parameter_name": "target_project_id",
                "parameter_type": "str | None",
                "default_value": "None",
            },
        },
    ]

    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])

    assert len(transcript) == 2
    assert transcript[0]["ok"] is True
    assert transcript[1]["ok"] is False
    assert "GROUNDING_CONFIRMATION_REQUIRED" in transcript[1]["error"]
    content = target.read_text(encoding="utf-8")
    assert "target_project_id: str | None = None" not in content


def test_tool_loop_rejects_insert_after_symbol_after_module_qualified_confirm_anchor(tmp_path: Path) -> None:
    target = tmp_path / "app" / "routes" / "health.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n\n"
        "@router.get('/live')\n"
        "async def live() -> dict:\n"
        "    return {'status': 'alive'}\n",
        encoding="utf-8",
    )
    steps = [
        {
            "tool": "confirm_anchor",
            "args": {
                "relative_path": "app/routes/health.py",
                "symbol_name": "app.routes.health.live",
                "anchor_quote": "async def live() -> dict:",
            },
        },
        {
            "tool": "insert_after_symbol",
            "args": {
                "relative_path": "app/routes/health.py",
                "symbol_name": "app.routes.health.live",
                "new_content": (
                    "@router.get('/metrics')\n" "async def metrics() -> dict:\n" "    return {'ok': True}\n"
                ),
            },
        },
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])
    assert len(transcript) == 2
    assert transcript[0]["ok"] is True
    assert transcript[1]["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in transcript[1]["error"]
    content = target.read_text(encoding="utf-8")
    assert "async def metrics() -> dict:" not in content


def test_execute_tool_step_edit_symbol_signature_is_removed_from_tool_loop(tmp_path: Path) -> None:
    target = tmp_path / "fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "edit_symbol",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.list_items",
                "mode": "signature",
                "new_signature": "async def list_items(self, tenant_id: str, limit: int, offset: int, item_type: str | None = None) -> dict",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )
    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    assert "item_type: str | None = None" not in target.read_text(encoding="utf-8")


def test_execute_tool_step_edit_symbol_sql_blocks_is_removed_from_tool_loop(tmp_path: Path) -> None:
    target = tmp_path / "fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, item_type: str | None = None) -> dict:\n"
        '        query = """SELECT id FROM app.items WHERE tenant_id = $1"""\n'
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "edit_symbol",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.list_items",
                "mode": "sql_blocks",
                "new_code": '{"blocks":[{"index":1,"sql":"SELECT id FROM app.items WHERE tenant_id = $1 AND item_type = $2"}]}',
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )
    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    assert "item_type = $2" not in target.read_text(encoding="utf-8")


def test_execute_tool_step_edit_symbol_refactor_symbol_is_removed_from_tool_loop(tmp_path: Path) -> None:
    target = tmp_path / "fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        conn = await self._connect()\n"
        "        try:\n"
        '            count_sql = "SELECT COUNT(1) FROM app.items WHERE tenant_id = $1"\n'
        '            rows_sql = "SELECT id FROM app.items WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3"\n'
        "            count_params = [tenant_id]\n"
        "            rows_params = [tenant_id, limit, offset]\n"
        "            total = await conn.fetchval(count_sql, *count_params)\n"
        "            rows = await conn.fetch(rows_sql, *rows_params)\n"
        "        finally:\n"
        "            await conn.close()\n"
        "        return {'items': [dict(r) for r in rows], 'total': int(total or 0)}\n",
        encoding="utf-8",
    )
    payload = (
        '{"pattern":"optional_filter_asyncpg_query","parameter_name":"item_type",'
        '"column_name":"item_type","sql_variable_names":["count_sql","rows_sql"],'
        '"params_variable_names":["count_params","rows_params"]}'
    )
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "edit_symbol",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.list_items",
                "mode": "refactor_symbol",
                "new_code": payload,
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )
    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    text = target.read_text(encoding="utf-8")
    assert "item_type: str | None = None" not in text
    assert "if item_type is not None:" not in text


def test_execute_tool_step_edit_symbol_dry_run_is_removed_from_tool_loop(tmp_path: Path) -> None:
    target = tmp_path / "fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "edit_symbol",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.list_items",
                "mode": "signature",
                "new_signature": "async def list_items(self, tenant_id: str, item_type: str | None = None, limit: int, offset: int) -> dict",
                "dry_run": True,
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )
    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    assert result["result"]["code"] == "removed_mutation_tool"


def test_execute_tool_step_replace_in_function_dry_run_is_removed(tmp_path: Path) -> None:
    target = tmp_path / "fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "replace_in_function",
            "args": {
                "relative_path": "fixture.py",
                "function_name": "FixtureService.list_items",
                "replacement": "def list_items(tenant_id: str, limit: int, offset: int) -> dict:\n    return {'items': []}\n",
                "dry_run": True,
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )
    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    assert result["result"]["code"] == "removed_mutation_tool"


def test_execute_tool_step_update_call_kwarg_dry_run_is_removed(tmp_path: Path) -> None:
    target = tmp_path / "fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def answer_items(self, tenant_id: str, project_id: str, query: str) -> dict:\n"
        "        result = await self.list_entities(\n"
        "            tenant_id=tenant_id,\n"
        "            project_id=project_id,\n"
        "            query=query,\n"
        "        )\n"
        "        return {'items': result}\n",
        encoding="utf-8",
    )
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "update_call_kwarg",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.answer_items",
                "callee": "self.list_entities",
                "kwarg": "repo_id",
                "new_value": "repo_id",
                "dry_run": True,
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )
    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    assert result["result"]["code"] == "removed_mutation_tool"


def test_execute_tool_step_replace_in_function_is_removed_before_write(tmp_path: Path) -> None:
    target = tmp_path / "fixture.py"
    original = (
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        return {'items': []}\n"
    )
    target.write_text(original, encoding="utf-8")
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "replace_in_function",
            "args": {
                "relative_path": "fixture.py",
                "function_name": "FixtureService.list_items",
                "replacement": "def list_items(tenant_id: str, limit: int, offset: int) -> dict:\n    return {'items': []}\n",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )
    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    assert target.read_text(encoding="utf-8") == original


def test_execute_tool_step_replace_in_function_skips_container_preflight_when_removed(tmp_path: Path) -> None:
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "replace_in_function",
            "args": {
                "relative_path": "fixture.py",
                "function_name": "FixtureService.list_items",
                "replacement": (
                    "def list_items(tenant_id: str, limit: int, offset: int) -> dict:\n" "    return {'items': []}\n"
                ),
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
        mutation_runtime={"mode": "docker", "container_required": True, "image": "runtime:latest"},
    )
    assert result["ok"] is False
    assert result["result"]["code"] == "removed_mutation_tool"


def test_execute_tool_step_replace_in_function_does_not_refresh_grounding_when_removed(tmp_path: Path) -> None:
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "replace_in_function",
            "args": {
                "relative_path": "fixture.py",
                "function_name": "FixtureService.list_items",
                "replacement": (
                    "def list_items(tenant_id: str, limit: int, offset: int) -> dict:\n" "    return {'items': []}\n"
                ),
                "expected_base_commit_sha": "stale-commit",
                "expected_file_blob_sha": "stale-blob",
                "expected_symbol_source_hash": "sha256:stale",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
        mutation_runtime={"mode": "docker", "container_required": True, "image": "runtime:latest"},
    )
    assert result["ok"] is False
    assert result["result"]["code"] == "removed_mutation_tool"


def test_execute_tool_step_edit_symbol_preflight_path_is_removed_from_tool_loop(tmp_path: Path) -> None:
    target = tmp_path / "fixture.py"
    original = (
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        return {'items': []}\n"
    )
    target.write_text(original, encoding="utf-8")
    result = execute_tool_step(
        workspace_root=tmp_path,
        step={
            "tool": "edit_symbol",
            "args": {
                "relative_path": "fixture.py",
                "symbol": "FixtureService.list_items",
                "mode": "signature",
                "new_signature": "async def list_items(self, tenant_id: str, item_type: str | None = None, limit: int, offset: int) -> dict",
            },
        },
        allow_prefixes=[["git"]],
        attempt_tracker={},
    )
    assert result["ok"] is False
    assert "REMOVED_MUTATION_TOOL" in result["error"]
    assert target.read_text(encoding="utf-8") == original


def test_tool_loop_reports_runtime_budget_after_long_mutation_step(monkeypatch, tmp_path: Path) -> None:
    timeline = iter([0.0, 0.0, 0.0, 0.03, 0.03])

    def fake_monotonic() -> float:
        return next(timeline)

    def fake_execute_tool_step(**_kwargs):
        return {"tool": "write_file", "ok": True, "result": {"operation": "write_file", "changed_files": ["a.txt"]}}

    monkeypatch.setattr(tool_loop_module, "monotonic", fake_monotonic)
    monkeypatch.setattr(tool_loop_module, "execute_tool_step", fake_execute_tool_step)

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=[
            {"tool": "write_file", "args": {"relative_path": "a.txt", "content": "x"}},
            {"tool": "cmd.run", "args": {"command": [sys.executable, "-c", "print('ok')"], "timeout_seconds": 5.0}},
        ],
        allow_prefixes=[[sys.executable]],
        max_runtime_seconds=0.01,
    )

    assert len(transcript) == 2
    assert transcript[0]["ok"] is True
    assert transcript[1]["ok"] is False
    assert transcript[1]["tool"] == "write_file"
    assert "TOOL_LOOP_RUNTIME_BUDGET_EXCEEDED" in transcript[1]["error"]


def test_tool_loop_normalizes_planner_style_readonly_args(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    steps = [
        {"tool": "list_dir", "args": {"path": "."}},
        {"tool": "read_file", "args": {"path": "demo.txt"}},
        {"tool": "search", "args": {"query": "beta", "path": ".", "include_patterns": ["*.txt"]}},
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])
    assert len(transcript) == 3
    assert all(item["ok"] for item in transcript)
    assert transcript[0]["result"]["path"].endswith(str(tmp_path))
    assert "alpha" in transcript[1]["result"]["content"]
    assert transcript[1]["_tokens_estimate"] > 0
    assert transcript[1]["_budget_used"] >= transcript[1]["_tokens_estimate"]
    assert transcript[2]["result"]["hits"][0]["line"] == 2


def test_tool_loop_normalizes_rel_path_aliases(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    steps = [
        {"tool": "list_dir", "args": {"rel_path": "."}},
        {"tool": "read_file", "args": {"rel_path": "demo.txt"}},
        {"tool": "search", "args": {"query": "beta", "rel_path": "."}},
        {"tool": "write_file", "args": {"rel_path": "out.txt", "content": "ok"}},
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])
    assert len(transcript) == 4
    assert all(item["ok"] for item in transcript)
    assert transcript[0]["result"]["path"].endswith(str(tmp_path))
    assert "alpha" in transcript[1]["result"]["content"]
    assert transcript[2]["result"]["hits"][0]["line"] == 2
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "ok"


def test_tool_loop_strips_read_trace_metadata_from_readonly_args(tmp_path: Path) -> None:
    from backend.services import devexecutor_tool_loop_service as module

    (tmp_path / "demo.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_read_range(*, workspace_root, **kwargs):
        captured["workspace_root"] = workspace_root
        captured["kwargs"] = dict(kwargs)
        return {"content": "alpha\n", "start_line": 1, "end_line": 1}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(module, "read_range", fake_read_range)
    try:
        transcript = module.execute_tool_loop(
            workspace_root=tmp_path,
            steps=[
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
            allow_prefixes=[["git"]],
        )
    finally:
        monkeypatch.undo()

    assert transcript[0]["ok"] is True
    assert captured["kwargs"] == {"relative_path": "demo.txt", "start_line": 1, "end_line": 1}


def test_tool_loop_strips_apply_patch_path_aliases(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "backend.services.devexecutor_patch_engine_service._run_pyright_on_files",
        lambda **_kwargs: (True, ""),
    )
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    (tmp_path / "hello.txt").write_text("hello\n", encoding="utf-8")
    patch = """diff --git a/hello.txt b/hello.txt
index ce01362..94954ab 100644
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-hello
+hello world
"""
    steps = [
        {"tool": "apply_patch", "args": {"patch_text": patch, "path": "hello.txt"}},
        {"tool": "read_file", "args": {"relative_path": "hello.txt"}},
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])
    assert len(transcript) == 2
    assert transcript[0]["ok"] is True
    assert transcript[1]["ok"] is True
    assert "hello world" in transcript[1]["result"]["content"]


def test_tool_loop_normalizes_edit_tool_aliases_proactively(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "backend.services.devexecutor_patch_engine_service._run_pyright_on_files",
        lambda **_kwargs: (True, ""),
    )
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    (tmp_path / "demo.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (tmp_path / "patchme.txt").write_text("left\nright\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "patchme.txt"], check=True, capture_output=True)
    patch = """diff --git a/patchme.txt b/patchme.txt
index 3dab1d7..9f7392a 100644
--- a/patchme.txt
+++ b/patchme.txt
@@ -1,2 +1,2 @@
 left
-right
+right-updated
"""
    steps = [
        {"tool": "write_file", "args": {"rel_path": "alias.txt", "text": "alias-ok"}},
        {"tool": "read_range", "args": {"path": "demo.txt", "start": 2, "end": 3}},
        {"tool": "replace_range", "args": {"path": "demo.txt", "start": 3, "end": 3, "text": "three-updated"}},
        {"tool": "apply_patch", "args": {"diff": patch}},
        {"tool": "evidence.attach", "args": {"type": "log", "ref": "local://artifact.log"}},
        {"tool": "read_file", "args": {"relative_path": "patchme.txt"}},
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])
    assert len(transcript) == 6
    assert all(item["ok"] for item in transcript)
    assert (tmp_path / "alias.txt").read_text(encoding="utf-8") == "alias-ok"
    assert transcript[1]["result"]["content"] == "two\nthree\n"
    assert transcript[2]["result"]["operation"] == "replace_range"
    assert transcript[3]["result"]["operation"] == "apply_patch"
    assert transcript[4]["result"]["evidence_ref"] == "local://artifact.log"
    assert "right-updated" in transcript[5]["result"]["content"]


def test_tool_loop_tracks_token_estimates_and_cumulative_budget_for_read_steps(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    steps = [
        {"tool": "read_file", "args": {"relative_path": "demo.txt"}},
        {"tool": "read_range", "args": {"relative_path": "demo.txt", "start_line": 2, "end_line": 3}},
    ]

    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])

    assert len(transcript) == 2
    assert transcript[0]["_tokens_estimate"] > 0
    assert transcript[0]["_budget_used"] == transcript[0]["_tokens_estimate"]
    assert transcript[1]["_tokens_estimate"] > 0
    assert transcript[1]["_budget_used"] == transcript[0]["_tokens_estimate"] + transcript[1]["_tokens_estimate"]


def test_tool_loop_tracks_token_estimates_for_cmd_run_output(tmp_path: Path) -> None:
    steps = [
        {
            "tool": "cmd.run",
            "args": {"command": [sys.executable, "-c", "print('alpha beta gamma')"], "timeout_seconds": 10},
        }
    ]

    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[[sys.executable], ["git"]])

    assert len(transcript) == 1
    assert transcript[0]["ok"] is True
    assert transcript[0]["_tokens_estimate"] > 0
    assert transcript[0]["_budget_used"] == transcript[0]["_tokens_estimate"]


def test_react_loop_tracks_cumulative_budget_for_read_transcript(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    plan = iter(
        [
            {"tool": "read_file", "args": {"relative_path": "demo.txt"}},
            {"tool": "read_range", "args": {"relative_path": "demo.txt", "start_line": 2, "end_line": 3}},
            None,
        ]
    )

    def planner(_transcript, _context):
        return next(plan)

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[["git"]],
        max_iterations=5,
    )

    assert len(transcript) == 2
    assert transcript[0]["_tokens_estimate"] > 0
    assert transcript[1]["_tokens_estimate"] > 0
    assert transcript[1]["_budget_used"] == transcript[0]["_tokens_estimate"] + transcript[1]["_tokens_estimate"]


def test_tool_loop_fails_deterministically_when_token_budget_is_exceeded(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
    steps = [
        {"tool": "read_file", "args": {"relative_path": "demo.txt"}},
        {"tool": "get_status", "args": {}},
    ]

    transcript = execute_tool_loop(
        workspace_root=tmp_path,
        steps=steps,
        allow_prefixes=[["git"]],
        token_budget=2,
    )

    assert len(transcript) == 2
    assert transcript[0]["ok"] is True
    assert transcript[1]["ok"] is False
    assert "TOKEN_BUDGET_EXCEEDED" in transcript[1]["error"]
    assert transcript[1]["result"]["code"] == "TOKEN_BUDGET_EXCEEDED"
    assert transcript[1]["result"]["budget"] == 2
    assert transcript[1]["result"]["used"] == transcript[0]["_budget_used"]
    assert transcript[1]["result"]["step_failed"] == 0


def test_react_loop_fails_deterministically_when_token_budget_is_exceeded(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
    plan = iter(
        [
            {"tool": "read_file", "args": {"relative_path": "demo.txt"}},
            {"tool": "get_status", "args": {}},
            None,
        ]
    )

    def planner(_transcript, _context):
        return next(plan)

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[["git"]],
        max_iterations=5,
        token_budget=2,
    )

    assert len(transcript) == 2
    assert transcript[0]["ok"] is True
    assert transcript[1]["ok"] is False
    assert "TOKEN_BUDGET_EXCEEDED" in transcript[1]["error"]
    assert transcript[1]["result"]["code"] == "TOKEN_BUDGET_EXCEEDED"
    assert transcript[1]["result"]["budget"] == 2
    assert transcript[1]["result"]["used"] == transcript[0]["_budget_used"]
    assert transcript[1]["result"]["step_failed"] == 0


def test_tool_loop_normalizes_workspace_root_absolute_paths(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    steps = [
        {"tool": "list_dir", "args": {"path": str(tmp_path)}},
        {"tool": "read_file", "args": {"path": str(tmp_path / "demo.txt")}},
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])
    assert len(transcript) == 2
    assert all(item["ok"] for item in transcript)
    assert transcript[0]["result"]["path"].endswith(str(tmp_path))
    assert "beta" in transcript[1]["result"]["content"]


def test_tool_loop_normalizes_container_app_absolute_paths(tmp_path: Path) -> None:
    workspace_root = tmp_path / "services" / "hyeve-codeintel-service" / "backend"
    workspace_root.mkdir(parents=True)
    (workspace_root / "README.md").write_text("codeintel\n", encoding="utf-8")
    steps = [
        {"tool": "read_file", "args": {"path": "/app/services/hyeve-codeintel-service/backend/README.md"}},
    ]
    transcript = execute_tool_loop(workspace_root=workspace_root, steps=steps, allow_prefixes=[["git"]])
    assert len(transcript) == 1
    assert transcript[0]["ok"] is True
    assert transcript[0]["result"]["path"].endswith("README.md")
    assert "codeintel" in transcript[0]["result"]["content"]


def test_tool_loop_read_file_uses_preflight_resolved_path_outside_workspace_root(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    workspace_root = tmp_path / "projects" / "AIX" / "hyeve-aix-service"
    workspace_root.mkdir(parents=True)
    target_path = tmp_path / "scripts" / "wh.py"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("print('ok')\n", encoding="utf-8")

    transcript = execute_tool_loop(
        workspace_root=workspace_root,
        steps=[
            {
                "tool": "read_file",
                "args": {
                    "relative_path": "scripts/wh.py",
                    "resolved_path": str(target_path),
                },
            }
        ],
        allow_prefixes=[["git"]],
    )

    assert len(transcript) == 1
    assert transcript[0]["ok"] is True
    assert transcript[0]["result"]["path"] == str(target_path)
    assert "print('ok')" in transcript[0]["result"]["content"]


def test_tool_loop_continues_after_recoverable_missing_readonly_target(tmp_path: Path) -> None:
    steps = [
        {"tool": "read_file", "args": {"relative_path": "missing.txt"}},
        {"tool": "write_file", "args": {"relative_path": "created.txt", "content": "ok"}},
        {"tool": "read_file", "args": {"relative_path": "created.txt"}},
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[["git"]])
    assert len(transcript) == 3
    assert transcript[0]["ok"] is False
    assert transcript[0]["recoverable"] is True
    assert transcript[0]["result"]["code"] == "readonly_target_not_found"
    assert transcript[1]["ok"] is True
    assert transcript[2]["ok"] is True
    assert transcript[2]["result"]["content"] == "ok"


def test_react_loop_continues_after_recoverable_missing_readonly_target(tmp_path: Path) -> None:
    plan = iter(
        [
            {"tool": "read_file", "args": {"relative_path": "missing.txt"}},
            {"tool": "write_file", "args": {"relative_path": "created.txt", "content": "ok"}},
            None,
        ]
    )

    def planner(_transcript, _context):
        return next(plan)

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[["git"]],
        max_iterations=5,
    )
    assert len(transcript) == 2
    assert transcript[0]["ok"] is False
    assert transcript[0]["recoverable"] is True
    assert transcript[1]["ok"] is True
    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "ok"


def test_tool_loop_converts_tool_exception_into_stable_terminal_transcript(tmp_path: Path, monkeypatch) -> None:
    from backend.services import devexecutor_tool_loop_service as module

    def _boom(**_kwargs):
        raise FileNotFoundError("missing fixture path")

    monkeypatch.setattr(module, "execute_tool_step", _boom)

    transcript = module.execute_tool_loop(
        workspace_root=tmp_path,
        steps=[{"tool": "write_file", "args": {"relative_path": "created.txt", "content": "ok"}}],
        allow_prefixes=[["git"]],
    )

    assert len(transcript) == 1
    assert transcript[0]["ok"] is False
    assert transcript[0]["recoverable"] is False
    assert transcript[0]["result"]["code"] == "tool_exception_unhandled"
    assert transcript[0]["result"]["exception_type"] == "FileNotFoundError"
    assert transcript[0]["diagnostics"]["exception_type"] == "FileNotFoundError"
