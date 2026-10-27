"""Advisory dependency graph for CodeIntel v2 (Wave 4 CI5, AIX lane).

:func:`adjacent_of` AST-scans the imports of ONE explicit file and resolves
each import to a workspace-relative path that exists on disk. The result is
ADVISORY INFORMATION ONLY with freshness ``unknown``:

- it is never used to widen, narrow, or otherwise change any scope --
  documented here as never-scope;
- there is no transitive walk: only depth-1 neighbours of the explicit
  file are returned, never neighbours-of-neighbours;
- imports that do not resolve to an existing file are dropped, never
  guessed;
- no other file is ever parsed.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

__all__ = ["adjacent_of"]


def _resolve_explicit_file(file_path: Any, workspace_root: str | Path) -> Path | None:
    """Resolve ``file_path`` inside ``workspace_root``; ``None`` if outside."""
    try:
        root = Path(workspace_root).resolve()
        candidate = Path(file_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
    except Exception:
        return None
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _dotted_to_disk(root: Path, dotted: str) -> Path | None:
    """Map ``a.b.c`` to ``a/b/c.py`` or ``a/b/c/__init__.py`` if on disk."""
    parts = [part for part in dotted.split(".") if part]
    if not parts:
        return None
    module_file = root.joinpath(*parts).with_suffix(".py")
    try:
        if module_file.is_file():
            return module_file
    except Exception:
        return None
    package_init = root.joinpath(*parts, "__init__.py")
    try:
        if package_init.is_file():
            return package_init
    except Exception:
        return None
    return None


def _relative_to_disk(
    root: Path, owner: Path, level: int, module: str | None
) -> Path | None:
    """Resolve ``from .[.]*module import ...`` against the owning package."""
    try:
        anchor = owner.parent
        for _ in range(level - 1):
            anchor = anchor.parent
            anchor.relative_to(root)
        dotted = ".".join(anchor.relative_to(root).parts)
        if module:
            dotted = f"{dotted}.{module}" if dotted else module
        if not dotted:
            return None
        return _dotted_to_disk(root, dotted)
    except Exception:
        return None


def adjacent_of(file_path: Any, workspace_root: str | Path) -> dict[str, Any]:
    """Return depth-1 import neighbours of the EXPLICIT file (advisory).

    Only the explicit file is parsed; each of its static imports is
    resolved to a workspace-relative path, and only paths that exist on
    disk are kept. The walk never goes deeper than depth 1.

    Returns:
        ``{"file", "adjacent", "freshness", "advisory_only", "reason"}``
        where ``adjacent`` is a sorted list of workspace-relative paths,
        ``freshness`` is always ``"unknown"``, and ``advisory_only`` is
        always ``True``. This payload is advisory information with
        never-scope semantics: it must never change any caller's scope.
    """
    base: dict[str, Any] = {
        "file": str(file_path),
        "adjacent": [],
        "freshness": "unknown",
        "advisory_only": True,
        "reason": "advisory: depth-1 import neighbours only; never-scope",
    }
    try:
        root = Path(workspace_root)
        if not root.is_dir():
            base["reason"] = "advisory: workspace root is not a directory; never-scope"
            return base
    except Exception:
        base["reason"] = "advisory: workspace root unreadable; never-scope"
        return base
    if not isinstance(file_path, (str, Path)) or not str(file_path).strip():
        base["reason"] = "advisory: no explicit file given; never-scope"
        return base
    resolved = _resolve_explicit_file(file_path, root)
    if resolved is None:
        base["reason"] = "advisory: explicit file is outside the workspace; never-scope"
        return base
    try:
        if not resolved.is_file():
            base["reason"] = "advisory: explicit file does not exist; never-scope"
            return base
    except Exception:
        base["reason"] = "advisory: explicit file unreadable; never-scope"
        return base
    try:
        source = resolved.read_text(encoding="utf-8")
    except Exception:
        base["reason"] = "advisory: explicit file unreadable; never-scope"
        return base
    try:
        tree = ast.parse(source)
    except Exception:
        base["reason"] = "advisory: explicit file unparseable; never-scope"
        return base

    root_resolved = root.resolve()
    rel = resolved.relative_to(root_resolved).as_posix()
    neighbours: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                disk = _dotted_to_disk(root_resolved, alias.name.split(".")[0])
                # Try the full dotted path too (a.b.c -> a/b/c.py).
                full = _dotted_to_disk(root_resolved, alias.name)
                for hit in (full, disk):
                    if hit is not None:
                        neighbours.add(hit.relative_to(root_resolved).as_posix())
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                disk = _relative_to_disk(
                    root_resolved, resolved, node.level, node.module
                )
            elif node.module:
                disk = _dotted_to_disk(root_resolved, node.module)
            else:
                disk = None
            if disk is not None:
                neighbours.add(disk.relative_to(root_resolved).as_posix())
    neighbours.discard(rel)
    return {
        "file": rel,
        "adjacent": sorted(neighbours),
        "freshness": "unknown",
        "advisory_only": True,
        "reason": "advisory: depth-1 import neighbours only; never-scope",
    }
