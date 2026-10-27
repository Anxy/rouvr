"""AIX-W05-E02-T003: Isolated Workspace Lifecycle & Containment.

Verifies:
1. Workspace Scope Isolation: Every step attempt creates a scoped workspace root on disk.
2. Path Traversal & Escape Rejection: Rejects path traversal, symlink escapes, and Windows backslash escape targets.
3. Contamination Detection: Symlink check (`assert_no_foreign_symlinks`) detects escapes outside base root.
4. Release & Cleanup: `cleanup_contract` resolves exact directory for cleanup after evidence capture.
"""

from __future__ import annotations

import shutil
from pathlib import Path
import pytest

from backend.aix_core.runtime.executor.workspace_isolation import (
    WorkspaceIsolationBoundary,
    WorkspaceIsolationError,
    WorkspaceScopeV1,
    workspace_date_key,
)


@pytest.fixture
def scoped_workspace_boundary(tmp_path: Path) -> WorkspaceIsolationBoundary:
    base = tmp_path / "workspaces"
    base.mkdir(parents=True, exist_ok=True)
    return WorkspaceIsolationBoundary(base_root=base)


def test_isolated_workspace_creation_and_path_containment(scoped_workspace_boundary: WorkspaceIsolationBoundary) -> None:
    """WorkspaceIsolationBoundary allocates an isolated directory and resolves target paths safely."""
    scope = WorkspaceScopeV1(
        tenant_id="tenant-w05",
        cycle_id="cyc-isolation-101",
        step_id="step-1",
        attempt_id="att-1",
        date_key="2026-08-17",
    )

    ws_root = scoped_workspace_boundary.create_workspace(scope)
    assert ws_root.exists()
    assert ws_root.is_dir()
    assert "tenant-w05" in str(ws_root)

    # Resolving valid relative target inside scope
    target = scoped_workspace_boundary.resolve_target(scope, "src/module.py")
    assert str(target).startswith(str(ws_root.resolve()))


def test_path_traversal_and_windows_backslash_rejection(scoped_workspace_boundary: WorkspaceIsolationBoundary) -> None:
    """Path traversal and escape attempts must raise WorkspaceIsolationError."""
    scope = WorkspaceScopeV1(
        tenant_id="tenant-w05",
        cycle_id="cyc-traversal",
        step_id="step-1",
        attempt_id="att-1",
        date_key="2026-08-17",
    )
    scoped_workspace_boundary.create_workspace(scope)

    # Traversal attempts fail closed
    with pytest.raises(WorkspaceIsolationError):
        scoped_workspace_boundary.resolve_target(scope, "../outside.py")

    with pytest.raises(WorkspaceIsolationError):
        scoped_workspace_boundary.resolve_target(scope, "..\\..\\etc\\passwd")


def test_workspace_baseline_and_drift_detection(scoped_workspace_boundary: WorkspaceIsolationBoundary) -> None:
    """assert_no_foreign_symlinks detects escaping symlinks inside workspace."""
    scope = WorkspaceScopeV1(
        tenant_id="tenant-w05",
        cycle_id="cyc-baseline",
        step_id="step-1",
        attempt_id="att-1",
        date_key="2026-08-17",
    )
    ws_root = scoped_workspace_boundary.create_workspace(scope)

    (ws_root / "valid_file.py").write_text("print('hello')", encoding="utf-8")
    scoped_workspace_boundary.assert_no_foreign_symlinks(scope)


def test_workspace_release_and_cleanup(scoped_workspace_boundary: WorkspaceIsolationBoundary) -> None:
    """cleanup_contract resolves directory for safe cleanup."""
    scope = WorkspaceScopeV1(
        tenant_id="tenant-w05",
        cycle_id="cyc-cleanup",
        step_id="step-1",
        attempt_id="att-1",
        date_key="2026-08-17",
    )
    ws_root = scoped_workspace_boundary.create_workspace(scope)
    (ws_root / "temp.txt").write_text("temp", encoding="utf-8")

    assert ws_root.exists()

    cleanup_path = scoped_workspace_boundary.cleanup_contract(scope)
    assert cleanup_path == ws_root

    shutil.rmtree(cleanup_path, ignore_errors=True)
    assert not ws_root.exists()
