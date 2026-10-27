"""AIX-E2E-CYCLE-001G-W05 workspace isolation tests."""

from __future__ import annotations

import os
import json
import tempfile
from pathlib import Path

import pytest

from backend.aix_core.runtime.executor.workspace_isolation import (
    WorkspaceIsolationBoundary,
    WorkspaceIsolationError,
    WorkspaceScopeV1,
)


def _can_symlink() -> bool:
    """Windows requires SeCreateSymbolicLinkPrivilege (admin or Developer Mode) to
    create symlinks; a non-elevated CI/sandbox session cannot. Probe once rather than
    assuming — the workspace_isolation.py *logic* for detecting an escaping symlink is
    still exercised elsewhere in this file when symlinks are available; on platforms/
    sessions without that privilege, these two tests skip instead of failing on an
    environment limitation unrelated to the code under test."""
    probe_dir = Path(tempfile.mkdtemp(prefix="aix-symlink-probe-"))
    target = probe_dir / "target"
    link = probe_dir / "link"
    target.mkdir()
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except OSError:
        return False


requires_symlink_privilege = pytest.mark.skipif(
    not _can_symlink(),
    reason="platform/session cannot create symlinks (Windows without admin/Developer Mode)",
)


@pytest.fixture
def boundary(tmp_path: object) -> WorkspaceIsolationBoundary:
    return WorkspaceIsolationBoundary(tmp_path)  # type: ignore[arg-type]


@pytest.fixture
def scope() -> WorkspaceScopeV1:
    return WorkspaceScopeV1(tenant_id="tenant-1", cycle_id="cycle-1", step_id="step-1", attempt_id="attempt-1")


class TestScopeComponentValidation:
    def test_rejects_empty_component(self) -> None:
        with pytest.raises(WorkspaceIsolationError):
            WorkspaceScopeV1(tenant_id="", cycle_id="c", step_id="s", attempt_id="a").relative_path()

    def test_relative_path_includes_date_key(self) -> None:
        scope = WorkspaceScopeV1(
            tenant_id="tenant-1",
            cycle_id="cycle-1",
            step_id="step-1",
            attempt_id="attempt-1",
            date_key="2026-08-12",
        )
        assert scope.relative_path().as_posix() == "tenant-1/2026-08-12/cycle-1/step-1/attempt-1"

    @pytest.mark.parametrize("bad", ["..", ".", "a/b", "a\\b", "a\x00b"])
    def test_rejects_traversal_and_separators(self, bad: str) -> None:
        with pytest.raises(WorkspaceIsolationError):
            WorkspaceScopeV1(tenant_id=bad, cycle_id="c", step_id="s", attempt_id="a").relative_path()


class TestWorkspaceIsolationBoundary:
    def test_base_root_must_exist(self, tmp_path: object) -> None:
        missing = os.path.join(str(tmp_path), "does-not-exist")
        with pytest.raises(WorkspaceIsolationError):
            WorkspaceIsolationBoundary(missing)

    def test_create_and_resolve_roundtrip(
        self, boundary: WorkspaceIsolationBoundary, scope: WorkspaceScopeV1
    ) -> None:
        created = boundary.create_workspace(scope)
        assert created.is_dir()
        resolved = boundary.resolve_workspace(scope, must_exist=True)
        assert resolved == created
        assert scope.resolved_date_key() in created.parts

    def test_create_writes_current_pointer(
        self, boundary: WorkspaceIsolationBoundary, scope: WorkspaceScopeV1
    ) -> None:
        created = boundary.create_workspace(scope)
        pointer = boundary.base_root / scope.tenant_id / scope.resolved_date_key() / "CURRENT.json"
        assert pointer.is_file()
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        assert payload["cycle_id"] == scope.cycle_id
        assert payload["workspace"] == str(created)

    def test_resolve_without_creation_does_not_require_existence(
        self, boundary: WorkspaceIsolationBoundary, scope: WorkspaceScopeV1
    ) -> None:
        path = boundary.resolve_workspace(scope, must_exist=False)
        assert path.relative_to(boundary.base_root)

    def test_must_exist_raises_when_absent(
        self, boundary: WorkspaceIsolationBoundary, scope: WorkspaceScopeV1
    ) -> None:
        with pytest.raises(WorkspaceIsolationError):
            boundary.resolve_workspace(scope, must_exist=True)

    @requires_symlink_privilege
    def test_symlink_escape_via_ancestor_is_rejected(
        self, boundary: WorkspaceIsolationBoundary, scope: WorkspaceScopeV1, tmp_path: object
    ) -> None:
        # Plant a symlink at the tenant-level ancestor pointing outside base_root.
        outside = boundary.base_root.parent / "outside-target"
        outside.mkdir(exist_ok=True)
        tenant_link = boundary.base_root / scope.tenant_id
        tenant_link.symlink_to(outside, target_is_directory=True)
        with pytest.raises(WorkspaceIsolationError):
            boundary.resolve_workspace(scope, must_exist=False)

    def test_cleanup_contract_matches_resolved_path(
        self, boundary: WorkspaceIsolationBoundary, scope: WorkspaceScopeV1
    ) -> None:
        created = boundary.create_workspace(scope)
        assert boundary.cleanup_contract(scope) == created

    def test_assert_no_foreign_symlinks_passes_on_clean_workspace(
        self, boundary: WorkspaceIsolationBoundary, scope: WorkspaceScopeV1
    ) -> None:
        workspace = boundary.create_workspace(scope)
        (workspace / "file.txt").write_text("ok", encoding="utf-8")
        boundary.assert_no_foreign_symlinks(scope)  # must not raise

    @requires_symlink_privilege
    def test_assert_no_foreign_symlinks_rejects_escaping_symlink_inside_workspace(
        self, boundary: WorkspaceIsolationBoundary, scope: WorkspaceScopeV1
    ) -> None:
        workspace = boundary.create_workspace(scope)
        outside = boundary.base_root.parent / "outside-file.txt"
        outside.write_text("secret elsewhere", encoding="utf-8")
        (workspace / "escape-link").symlink_to(outside)
        with pytest.raises(WorkspaceIsolationError):
            boundary.assert_no_foreign_symlinks(scope)

    def test_different_attempts_get_disjoint_workspaces(
        self, boundary: WorkspaceIsolationBoundary, scope: WorkspaceScopeV1
    ) -> None:
        other = WorkspaceScopeV1(
            tenant_id=scope.tenant_id, cycle_id=scope.cycle_id, step_id=scope.step_id, attempt_id="attempt-2"
        )
        first = boundary.create_workspace(scope)
        second = boundary.create_workspace(other)
        assert first != second


class TestCycleProjectDeliverableRoot:
    def test_objective_folder_slug_from_system_name(self) -> None:
        from backend.aix_core.runtime.executor.workspace_isolation import objective_folder_slug

        slug = objective_folder_slug(
            "Create only a schema.sql file for an Attendance Registration System "
            "supporting person registration"
        )
        assert slug == "attendance-registration-system"

    def test_create_cycle_project_root_under_cycle(
        self, boundary: WorkspaceIsolationBoundary
    ) -> None:
        root = boundary.create_cycle_project_root(
            tenant_id="tenant-1",
            cycle_id="cyc_abc",
            objective="Create only schema.sql for an Attendance Registration System",
            date_key="2026-08-21",
        )
        assert root.is_dir()
        assert root.name == "attendance-registration-system"
        assert "cyc_abc" in root.as_posix()
        pointer = boundary.base_root / "tenant-1" / "2026-08-21" / "cyc_abc" / "DELIVERABLE_ROOT.json"
        assert pointer.is_file()
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        assert payload["objective_slug"] == "attendance-registration-system"
        assert Path(payload["deliverable_root"]) == root

    def test_session_id_nests_cycle_under_discuss_folder(
        self, boundary: WorkspaceIsolationBoundary
    ) -> None:
        scope = WorkspaceScopeV1(
            tenant_id="tenant-1",
            cycle_id="cyc_nested",
            step_id="step-1",
            attempt_id="attempt-1",
            date_key="2026-08-22",
            session_id="disc_session1",
        )
        ws = boundary.create_workspace(scope)
        assert ws.as_posix().endswith("tenant-1/2026-08-22/disc_session1/cyc_nested/step-1/attempt-1")
        root = boundary.create_cycle_project_root(
            tenant_id="tenant-1",
            cycle_id="cyc_nested",
            objective="Create a greenfield pulse board frontend",
            date_key="2026-08-22",
            session_id="disc_session1",
        )
        assert "disc_session1" in root.as_posix()
        assert "cyc_nested" in root.as_posix()
        pointer = (
            boundary.base_root
            / "tenant-1"
            / "2026-08-22"
            / "disc_session1"
            / "cyc_nested"
            / "DELIVERABLE_ROOT.json"
        )
        assert pointer.is_file()
        assert json.loads(pointer.read_text(encoding="utf-8"))["session_id"] == "disc_session1"


def test_generic_edit_schema_does_not_force_mutation() -> None:
    from backend.aix_core.runtime.executor.real_execute import (
        _contract_forces_edit,
        _step_is_validate_only,
    )
    from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1
    from backend.aix_core.runtime.verification.semantic_verifier import _step_declares_mutation

    step = StepContractV1(
        cycle_id="cyc-1",
        tenant_id="tenant-1",
        step_index=1,
        purpose="Validate the completed schema.sql and attach evidence that the scoped file exists",
        target_scope=("schema.sql",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("Validation output demonstrates absence of CREATE DATABASE",),
        validation_commands=("test -f schema.sql",),
        assigned_role="executor",
        step_budget=BudgetV1(
            time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1
        ),
        status="PENDING",
    )
    assert _contract_forces_edit(step) is False
    assert _step_is_validate_only(step) is True
    assert _step_declares_mutation(step) is False


def test_sticky_date_key_prefers_earliest_existing_cycle_day(tmp_path: Path) -> None:
    from backend.aix_core.runtime.executor.workspace_isolation import (
        WorkspaceIsolationBoundary,
        resolve_sticky_date_key,
    )

    tenant = "tenant-1"
    cycle = "cyc_sticky"
    (tmp_path / tenant / "2026-08-24" / cycle / "proj").mkdir(parents=True)
    (tmp_path / tenant / "2026-08-23" / "disc_s1" / cycle / "proj").mkdir(parents=True)
    assert (
        resolve_sticky_date_key(tmp_path, tenant_id=tenant, cycle_id=cycle, session_id="disc_s1")
        == "2026-08-23"
    )
    boundary = WorkspaceIsolationBoundary(tmp_path)
    # Newer-day file should merge into sticky project root.
    newer = tmp_path / tenant / "2026-08-24" / "disc_s1" / cycle / "attendance-registration-system"
    newer.mkdir(parents=True, exist_ok=True)
    (newer / "api.php").write_text("<?php // new\n", encoding="utf-8")
    root = boundary.create_cycle_project_root(
        tenant_id=tenant,
        cycle_id=cycle,
        objective="Create schema for an Attendance Registration System",
        date_key="2026-08-23",
        session_id="disc_s1",
    )
    assert "2026-08-23" in str(root)
    assert (root / "api.php").is_file()

    from backend.aix_core.runtime.executor.real_execute import _prefers_cycle_project_deliverable

    repo = tmp_path / "repo"
    cycle = tmp_path / "cycle" / "attendance-registration-system"
    repo.mkdir()
    cycle.mkdir(parents=True)
    # Stray same-named file in the registered repo must not steal greenfield CREATE.
    (repo / "schema.sql").write_text("-- stray\n", encoding="utf-8")
    assert (
        _prefers_cycle_project_deliverable(
            intent="CREATE_FILE",
            objective="Create only a schema.sql file for an Attendance Registration System",
            repository_root=repo,
            cycle_root=cycle,
            targets=("schema.sql",),
        )
        is True
    )


def test_proposed_greenfield_prefers_cycle_even_if_dir_exists(tmp_path: Path) -> None:
    from backend.aix_core.runtime.executor.real_execute import _prefers_cycle_project_deliverable

    repo = tmp_path / "repo"
    cycle = tmp_path / "cycle" / "calculator"
    (repo / "proposed" / "calculator").mkdir(parents=True)
    (repo / "proposed" / "calculator" / ".gitkeep").write_text("", encoding="utf-8")
    cycle.mkdir(parents=True)
    assert (
        _prefers_cycle_project_deliverable(
            intent="CREATE_FILE",
            objective="Create calculator under proposed/calculator/. Governance create_root = proposed/.",
            repository_root=repo,
            cycle_root=cycle,
            targets=(
                "proposed/calculator/",
                "proposed/calculator/index.html",
                "proposed/calculator/styles.css",
                "proposed/calculator/app.js",
            ),
        )
        is True
    )
