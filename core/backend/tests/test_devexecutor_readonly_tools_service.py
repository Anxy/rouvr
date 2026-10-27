from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.services.devexecutor_readonly_tools_service import (
    WorkspaceSecurityError,
    get_status,
    list_dir,
    read_file,
    read_range,
    search,
)


def test_list_dir_and_read_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello world\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()

    listed = list_dir(workspace_root=tmp_path, relative_path=".")
    names = {e["name"] for e in listed["entries"]}
    assert {"a.txt", "sub"} <= names

    read = read_file(workspace_root=tmp_path, relative_path="a.txt")
    assert "hello world" in read["content"]
    assert read["truncated"] is False
    assert read["encoding"]
    assert read["source"] in {"filesystem", "git_blob_head"}


def test_path_escape_is_blocked(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceSecurityError):
        list_dir(workspace_root=tmp_path, relative_path="..")


def test_search_finds_hits(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    result = search(workspace_root=tmp_path, pattern="beta")
    assert len(result["hits"]) >= 1
    assert any("beta" in h["text"] for h in result["hits"])
    assert result["engine"] in {"rg_json", "python_fallback"}


def test_get_status_non_git_workspace(tmp_path: Path) -> None:
    status = get_status(workspace_root=tmp_path)
    assert status["is_git_repo"] is False
    assert status["changes"] == []


def test_list_dir_respects_gitignore_when_repo_exists(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("nope", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("ok", encoding="utf-8")
    listed = list_dir(workspace_root=tmp_path, relative_path=".")
    names = {e["name"] for e in listed["entries"]}
    assert "visible.txt" in names
    assert "ignored.txt" not in names
    assert listed["gitignore_applied"] is True


def test_get_status_git_repo_returns_porcelain_v2_shape(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    (tmp_path / "demo.txt").write_text("hello", encoding="utf-8")
    status = get_status(workspace_root=tmp_path)
    assert status["is_git_repo"] is True
    assert status["format"] == "porcelain_v2"
    assert isinstance(status["changes"], list)


def test_read_range_returns_bounded_slice(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    result = read_range(
        workspace_root=tmp_path,
        relative_path="notes.txt",
        start_line=2,
        end_line=4,
        max_chars=100,
    )
    assert result["line_count"] == 3
    assert result["total_lines"] == 5
    assert result["content"] == "b\nc\nd\n"
    assert result["truncated"] is False


def test_read_range_resolves_symbol_to_line_window(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text(
        "def helper() -> None:\n    return None\n\n\ndef run() -> None:\n    value = 1\n    return None\n",
        encoding="utf-8",
    )
    result = read_range(
        workspace_root=tmp_path,
        relative_path="sample.py",
        symbol="run",
    )

    assert result["start_line"] == 5
    assert result["end_line"] == 7
    assert "value = 1" in result["content"]
    assert result["symbol"] == "run"
