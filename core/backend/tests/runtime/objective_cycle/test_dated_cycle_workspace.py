from __future__ import annotations

from pathlib import Path

from backend.aix_core.runtime.objective.durable_api_backend import _latest_attempt_workspace
from backend.aix_core.runtime.executor.workspace_isolation import (
    WorkspaceIsolationBoundary,
    WorkspaceScopeV1,
)


def test_latest_attempt_workspace_finds_dated_layout(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "workspaces"
    base.mkdir()
    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_WORKSPACE_BASE", str(base))
    scope = WorkspaceScopeV1(
        tenant_id="tenant-1",
        cycle_id="cyc_test",
        step_id="step-1",
        attempt_id="attempt-1",
        date_key="2026-08-12",
    )
    boundary = WorkspaceIsolationBoundary(base)
    created = boundary.create_workspace(scope)
    (created / "evidence").mkdir()
    (created / "evidence" / "objective.json").write_text("{}", encoding="utf-8")

    found = _latest_attempt_workspace(tenant_id="tenant-1", cycle_id="cyc_test")
    assert found == created


def test_latest_attempt_workspace_finds_legacy_layout(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "workspaces"
    base.mkdir()
    legacy = base / "tenant-1" / "cyc_legacy" / "step-1" / "attempt-1"
    legacy.mkdir(parents=True)
    (legacy / "marker.txt").write_text("legacy", encoding="utf-8")

    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_WORKSPACE_BASE", str(base))
    found = _latest_attempt_workspace(tenant_id="tenant-1", cycle_id="cyc_legacy")
    assert found == legacy
