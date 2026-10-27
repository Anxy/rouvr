from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.services.devexecutor_mutation_tools_service import write_file
from backend.services.devexecutor_readonly_tools_service import WorkspaceSecurityError, read_file
from backend.legacy.orphaned_services.devexecutor_workspace_manager_service import WorkspaceManager


def test_concurrent_workspace_creation_is_isolated(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")

    def _create(idx: int):
        ctx = manager.create_workspace(project_id="piso", job_id=f"j{idx}", task_number=670)
        write_file(
            workspace_root=ctx.workspace_root,
            relative_path=f"output_{idx}.txt",
            content=f"value-{idx}",
        )
        return ctx

    with ThreadPoolExecutor(max_workers=4) as pool:
        contexts = list(pool.map(_create, range(4)))

    roots = {str(c.workspace_root) for c in contexts}
    assert len(roots) == 4
    for i, ctx in enumerate(contexts):
        assert (ctx.workspace_root / f"output_{i}.txt").exists()


def test_cross_workspace_escape_attempt_is_blocked(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    a = manager.create_workspace(project_id="piso", job_id="a", task_number=670)
    b = manager.create_workspace(project_id="piso", job_id="b", task_number=670)
    (b.workspace_root / "secret.txt").write_text("secret", encoding="utf-8")

    # Attempt to traverse from workspace A into workspace B should fail.
    rel = Path("..") / b.workspace_root.name / "secret.txt"
    with pytest.raises(WorkspaceSecurityError):
        read_file(workspace_root=a.workspace_root, relative_path=str(rel))
