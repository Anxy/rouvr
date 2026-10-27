"""Map web routes named in an objective onto App Router / Pages Router files.

Fail-closed: returns a repo-relative page path only when exactly one filesystem
match is found (or a single preferred match under src/app). Never invents paths.
"""

from __future__ import annotations

import re
from pathlib import Path

_WEB_ROUTE_RE = re.compile(
    r"(?<![A-Za-z0-9_])(/(?:[A-Za-z0-9_\-]+/)*[A-Za-z0-9_\-]+)(?:\?[^\s\"'`]*)?",
)
_PAGE_EXTS = (".tsx", ".jsx", ".js", ".ts")


def extract_web_routes(objective: str) -> list[str]:
    """Return normalized URL paths (no query) mentioned in free text."""
    text = str(objective or "")
    out: list[str] = []
    for match in _WEB_ROUTE_RE.finditer(text):
        start = match.start(1)
        # Skip `http://...` / `https://...` path tails.
        prefix = text[max(0, start - 8) : start]
        if "://" in prefix or prefix.endswith(":"):
            continue
        route = match.group(1).split("?", 1)[0].rstrip("/")
        # Require at least /segment/segment (skip /create, /run, bare /api).
        if route.count("/") < 2:
            continue
        if route not in out:
            out.append(route)
    return out


def _strip_route_groups(rel_posix: str) -> str:
    return re.sub(r"\([^/]+\)/", "", rel_posix.replace("\\", "/"))


def resolve_app_router_page(repository_root: Path, route: str) -> str | None:
    """Resolve `/objectives/new` → `src/app/(admin)/objectives/new/page.tsx` when unique."""
    cleaned = str(route or "").strip().split("?", 1)[0].strip("/")
    if not cleaned or ".." in cleaned or cleaned.startswith((".", "\\")):
        return None
    root = Path(repository_root)
    if not root.is_dir():
        return None

    matches: list[str] = []
    # Bounded walk: App Router page files only.
    for path in root.rglob("page.*"):
        if path.suffix.lower() not in _PAGE_EXTS:
            continue
        try:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            continue
        stripped = _strip_route_groups(rel)
        suffix = path.suffix.lower()
        for prefix in ("src/app/", "app/"):
            if stripped == f"{prefix}{cleaned}/page{suffix}":
                matches.append(rel)
                break

    # Pages Router: pages/objectives/new.tsx
    for ext in _PAGE_EXTS:
        for base in ("src/pages", "pages"):
            candidate = root / base / f"{cleaned}{ext}"
            if candidate.is_file():
                try:
                    matches.append(candidate.resolve().relative_to(root.resolve()).as_posix())
                except (OSError, ValueError):
                    continue

    # Deduplicate, prefer src/app
    uniq: list[str] = []
    for item in matches:
        if item not in uniq:
            uniq.append(item)
    if not uniq:
        return None
    if len(uniq) == 1:
        return uniq[0]
    preferred = [p for p in uniq if p.startswith("src/app/")]
    if len(preferred) == 1:
        return preferred[0]
    return None


def resolve_page_from_objective(repository_root: Path, objective: str) -> str | None:
    for route in extract_web_routes(objective):
        page = resolve_app_router_page(repository_root, route)
        if page:
            return page
    return None


__all__ = [
    "extract_web_routes",
    "resolve_app_router_page",
    "resolve_page_from_objective",
]
