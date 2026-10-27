"""Hermetic tests: monorepo root discovery across checkout layouts."""

from pathlib import Path

from backend.shared.monorepo_imports import discover_hyeve_root


def _layout(root: Path, *dirs: str) -> Path:
    for d in dirs:
        (root / d).mkdir(parents=True)
    return root / "aix" / "hyeve-aix-service" / "backend" / "shared"


def test_platform_layout_found(tmp_path) -> None:
    start = _layout(
        tmp_path, "hyeve-platform/hyeve_core", "aix/hyeve-aix-service/backend/shared"
    )
    assert discover_hyeve_root(start) == tmp_path / "hyeve-platform"


def test_legacy_nested_layout_still_found(tmp_path) -> None:
    nested = tmp_path / "hyeve" / "hyeve_core"
    nested.mkdir(parents=True)
    (nested / "task_lifecycle_registry.py").write_text("# legacy")
    start = _layout(tmp_path, "aix/hyeve-aix-service/backend/shared")
    assert discover_hyeve_root(start) == tmp_path / "hyeve"


def test_no_layout_returns_none(tmp_path) -> None:
    start = _layout(tmp_path, "aix/hyeve-aix-service/backend/shared")
    assert discover_hyeve_root(start) is None
