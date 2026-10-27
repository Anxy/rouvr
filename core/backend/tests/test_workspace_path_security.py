from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.devexecutor_mutation_tools_service import write_file
from backend.services.devexecutor_readonly_tools_service import WorkspaceSecurityError, list_dir, read_file


def test_path_traversal_blocked_for_read_and_write(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceSecurityError):
        read_file(workspace_root=tmp_path, relative_path="../outside.txt")
    with pytest.raises(WorkspaceSecurityError):
        write_file(workspace_root=tmp_path, relative_path="../outside.txt", content="x")


def test_symlink_escape_blocked_when_symlink_supported(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}_outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("secret", encoding="utf-8")

    link_path = tmp_path / "escape_link"
    try:
        link_path.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is not permitted in this environment.")

    with pytest.raises(WorkspaceSecurityError):
        read_file(workspace_root=tmp_path, relative_path="escape_link/secret.txt")
    with pytest.raises(WorkspaceSecurityError):
        list_dir(workspace_root=tmp_path, relative_path="escape_link")
