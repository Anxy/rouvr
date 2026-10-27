"""AIFIRST-1: tool loop smoke tests.

Focused tests for the execute_tool_loop and execute_react_tool_loop
entry-points confirming the core contract introduced in AIFIRST-1.
"""

from __future__ import annotations

import sys
from pathlib import Path

from backend.services.devexecutor_tool_loop_service import (
    execute_react_tool_loop,
    execute_tool_loop,
)

# ---------------------------------------------------------------------------
# execute_tool_loop — core step-list variant
# ---------------------------------------------------------------------------


def test_tool_loop_reads_and_lists(tmp_path: Path) -> None:
    (tmp_path / "readme.txt").write_text("hello aifirst\n", encoding="utf-8")
    steps = [
        {"tool": "list_dir", "args": {"relative_path": "."}},
        {"tool": "read_file", "args": {"relative_path": "readme.txt"}},
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[])
    assert len(transcript) == 2
    assert all(item["ok"] for item in transcript)
    assert "aifirst" in transcript[1]["result"]["content"]


def test_tool_loop_write_then_read(tmp_path: Path) -> None:
    steps = [
        {"tool": "write_file", "args": {"relative_path": "out.txt", "content": "written\n"}},
        {"tool": "read_file", "args": {"relative_path": "out.txt"}},
    ]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[])
    assert transcript[0]["ok"] is True
    assert transcript[1]["ok"] is True
    assert "written" in transcript[1]["result"]["content"]


def test_tool_loop_transcript_contains_diagnostics(tmp_path: Path) -> None:
    steps = [{"tool": "list_dir", "args": {"relative_path": "."}}]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[])
    assert "diagnostics" in transcript[0]


def test_tool_loop_unknown_tool_fails_gracefully(tmp_path: Path) -> None:
    steps = [{"tool": "nonexistent_tool_xyz", "args": {}}]
    transcript = execute_tool_loop(workspace_root=tmp_path, steps=steps, allow_prefixes=[])
    assert transcript[0]["ok"] is False
    assert "error" in transcript[0]


# ---------------------------------------------------------------------------
# execute_react_tool_loop — planner-driven variant (AIFIRST-1 ReAct)
# ---------------------------------------------------------------------------


def test_react_loop_planner_returns_none_terminates(tmp_path: Path) -> None:
    """Planner returning None signals done — loop terminates cleanly."""
    calls: list[int] = []

    def planner(transcript, ctx):
        calls.append(ctx["iteration_index"])
        if ctx["iteration_index"] == 0:
            return {"tool": "list_dir", "args": {"relative_path": "."}}
        return None  # done

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[],
    )
    assert len(transcript) == 1
    assert transcript[0]["ok"] is True
    assert len(calls) == 2  # iteration 0 (executed) + iteration 1 (None → stop)


def test_react_loop_respects_max_iterations(tmp_path: Path) -> None:
    """Loop stops after max_iterations even if planner keeps emitting steps."""

    def planner(transcript, ctx):
        return {"tool": "list_dir", "args": {"relative_path": "."}}

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[],
        max_iterations=3,
    )
    # last entry is the budget error (not an ok step)
    assert len(transcript) >= 3


def test_react_loop_denied_command_appended_to_transcript(tmp_path: Path) -> None:
    """Command not in allow_prefixes is denied and recorded in transcript."""

    def planner(transcript, ctx):
        if ctx["iteration_index"] == 0:
            # whoami is not in allow_prefixes → COMMAND_DENIED
            return {"tool": "cmd.run", "args": {"command": ["whoami"], "timeout_seconds": 2.0}}
        return None

    transcript = execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[[sys.executable]],  # python allowed, whoami not
    )
    assert transcript[0]["ok"] is False
    assert "COMMAND_DENIED" in transcript[0]["error"]


def test_react_loop_context_carries_iteration_metadata(tmp_path: Path) -> None:
    captured: list[dict] = []

    def planner(transcript, ctx):
        captured.append(dict(ctx))
        return None

    execute_react_tool_loop(
        workspace_root=tmp_path,
        planner=planner,
        allow_prefixes=[],
    )
    assert captured[0]["iteration_index"] == 0
    assert "max_iterations" in captured[0]
    assert "elapsed_seconds" in captured[0]
    assert "remaining_iterations" in captured[0]
