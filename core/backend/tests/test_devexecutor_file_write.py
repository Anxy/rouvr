"""AIFIRST-4: DooD file write tests.

Focused tests for the write_file tool introduced in AIFIRST-4
(Docker-out-of-Docker workspace file write capability).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.devexecutor_mutation_tools_service import write_file
from backend.services.devexecutor_readonly_tools_service import WorkspaceSecurityError

# ---------------------------------------------------------------------------
# Core happy-path behaviours
# ---------------------------------------------------------------------------


def test_write_file_creates_new_file(tmp_path: Path) -> None:
    result = write_file(
        workspace_root=tmp_path,
        relative_path="output.txt",
        content="hello from aifirst-4\n",
    )
    assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "hello from aifirst-4\n"
    assert result["operation"] == "write_file"
    assert result["relative_path"] == "output.txt"
    assert result["changed_files"] == ["output.txt"]


def test_write_file_creates_intermediate_directories(tmp_path: Path) -> None:
    result = write_file(
        workspace_root=tmp_path,
        relative_path="nested/deep/file.py",
        content="# generated\n",
    )
    assert (tmp_path / "nested" / "deep" / "file.py").exists()
    assert "nested/deep/file.py" in result["changed_files"]


def test_write_file_overwrites_existing_file(tmp_path: Path) -> None:
    (tmp_path / "data.txt").write_text("old content\n", encoding="utf-8")
    write_file(workspace_root=tmp_path, relative_path="data.txt", content="new content\n")
    assert (tmp_path / "data.txt").read_text(encoding="utf-8") == "new content\n"


def test_write_file_atomic_write_flag_true(tmp_path: Path) -> None:
    result = write_file(
        workspace_root=tmp_path,
        relative_path="atomic.txt",
        content="data\n",
    )
    assert result["atomic_write"] is True


# ---------------------------------------------------------------------------
# Policy hook integration
# ---------------------------------------------------------------------------


def test_write_file_emits_pre_and_post_hooks(tmp_path: Path) -> None:
    events: list[tuple[str, dict]] = []

    def hook(evt: str, payload: dict) -> None:
        events.append((evt, payload))

    write_file(
        workspace_root=tmp_path,
        relative_path="hooks.txt",
        content="x\n",
        policy_hook=hook,
    )
    event_names = [e[0] for e in events]
    assert "pre_write_file" in event_names
    assert "post_write_file" in event_names


def test_write_file_hook_payload_contains_path(tmp_path: Path) -> None:
    payloads: list[dict] = []

    def hook(evt: str, payload: dict) -> None:
        payloads.append(payload)

    write_file(
        workspace_root=tmp_path,
        relative_path="src/module.py",
        content="pass\n",
        policy_hook=hook,
    )
    assert any("src/module.py" in str(p) for p in payloads)


# ---------------------------------------------------------------------------
# Security — workspace escape prevention (DooD path boundary)
# ---------------------------------------------------------------------------


def test_write_file_blocks_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceSecurityError):
        write_file(
            workspace_root=tmp_path,
            relative_path="../escape.txt",
            content="escaped",
        )


def test_write_file_blocks_absolute_path(tmp_path: Path) -> None:
    with pytest.raises((WorkspaceSecurityError, ValueError)):
        write_file(
            workspace_root=tmp_path,
            relative_path="/etc/passwd",
            content="x",
        )


# ---------------------------------------------------------------------------
# Formatter metadata
# ---------------------------------------------------------------------------


def test_write_file_result_contains_formatter_field(tmp_path: Path) -> None:
    result = write_file(
        workspace_root=tmp_path,
        relative_path="example.py",
        content="x = 1\n",
    )
    assert "formatter" in result
