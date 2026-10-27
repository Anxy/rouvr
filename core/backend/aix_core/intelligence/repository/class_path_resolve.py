"""Resolve PascalCase class hints in objectives to concrete repository files.

Natural engineering requests often name `CronJobController` without a path or
extension. Without this binding, exploration stays unbound and the planner returns
NEEDS_MORE_EVIDENCE with zero file reads (live S6).
"""

from __future__ import annotations

import re
from pathlib import Path

_CLASS_HINT_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9]*(?:"
    r"Controller|Service|Repository|Manager|Handler|Provider|Factory|Helper|"
    r"Job|Command|Middleware|Policy|Seeder|Factory|Listener|Subscriber"
    r"))\b"
)

_SKIP_DIR_PARTS = frozenset(
    {
        "node_modules",
        "vendor",
        ".git",
        ".venv",
        "venv",
        "dist",
        "build",
        "__pycache__",
        ".next",
        "storage",
    }
)

_EXTENSIONS = (".php", ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rb")


def extract_class_name_hints(objective: str) -> list[str]:
    """PascalCase type names that look like code symbols, in mention order."""
    return list(dict.fromkeys(_CLASS_HINT_RE.findall(str(objective or ""))))


def resolve_class_hints_in_repository(
    repository_root: Path,
    hints: list[str] | tuple[str, ...],
    *,
    max_hits: int = 8,
) -> list[str]:
    """Map class hints to repo-relative file paths (first sensible hit per hint)."""
    root = Path(repository_root)
    if not root.is_dir() or not hints:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for hint in hints:
        name = str(hint or "").strip()
        if not name:
            continue
        hit: str | None = None
        for ext in _EXTENSIONS:
            needle = f"{name}{ext}"
            for path in root.rglob(needle):
                if any(part in _SKIP_DIR_PARTS for part in path.parts):
                    continue
                try:
                    rel = path.resolve().relative_to(root.resolve()).as_posix()
                except ValueError:
                    continue
                hit = rel
                break
            if hit:
                break
        if hit and hit not in seen:
            seen.add(hit)
            found.append(hit)
        if len(found) >= max_hits:
            break
    return found


__all__ = [
    "extract_class_name_hints",
    "resolve_class_hints_in_repository",
]
