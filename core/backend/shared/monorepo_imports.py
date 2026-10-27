from __future__ import annotations

import sys
from pathlib import Path


def discover_hyeve_root(start: Path) -> Path | None:
    """Find the nearest parent that exposes the top-level hyeve_core package."""

    resolved_start = start.resolve()
    search_roots = [resolved_start, *resolved_start.parents]
    for candidate in search_roots:
        hyeve_core_dir = candidate / "hyeve_core"
        if hyeve_core_dir.is_dir():
            return candidate
        nested_dir = candidate / "hyeve" / "hyeve_core"
        if nested_dir.is_dir():
            # Sibling-checkout layout (<AIX_ROOT>/../hyeve): the package dir
            # itself holds task_lifecycle_registry.py, so the import root is
            # its parent (the monorepo root). Returning nested_dir would
            # resolve `hyeve_core` to the nested legacy hyeve_core/hyeve_core
            # subpackage and shadow the real modules.
            if (nested_dir / "task_lifecycle_registry.py").is_file():
                return candidate / "hyeve"
            return nested_dir
        hyeve_sub = candidate / "hyeve"
        if (hyeve_sub / "hyeve_core").is_dir():
            return hyeve_sub
        platform_sub = candidate / "hyeve-platform"
        if (platform_sub / "hyeve_core").is_dir():
            return platform_sub
    return None


def ensure_hyeve_core_import_path() -> None:
    """Make the monorepo-root hyeve_core package importable in service contexts."""

    hyeve_root = discover_hyeve_root(Path(__file__))
    if hyeve_root is None:
        return

    hyeve_core_package_root = hyeve_root / "hyeve_core"
    hyeve_root_text = str(hyeve_root)
    hyeve_core_package_root_text = str(hyeve_core_package_root)

    if hyeve_root_text in sys.path:
        sys.path.remove(hyeve_root_text)
    sys.path.insert(0, hyeve_root_text)

    if hyeve_core_package_root_text not in sys.path:
        sys.path.insert(1, hyeve_core_package_root_text)

    try:
        import hyeve_core as hyeve_core_package
    except ModuleNotFoundError:
        return

    package_search_path = getattr(hyeve_core_package, "__path__", None)
    if package_search_path is not None and hyeve_core_package_root_text not in package_search_path:
        package_search_path.append(hyeve_core_package_root_text)
