from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from backend.services.devexecutor_python_symbol_service import resolve_python_symbol_range


class WorkspaceSecurityError(ValueError):
    pass


def _safe_resolve(workspace_root: str | Path, relative_path: str = ".") -> Path:
    root = Path(workspace_root).resolve()
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise WorkspaceSecurityError(f"Path escapes workspace root: {relative_path}") from exc
    return target


def _detect_workspace_scope_root(path: Path) -> Path | None:
    current = path.resolve()
    while True:
        if (current / ".git").exists() or (current / "pyproject.toml").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def _detect_git_root(path: Path) -> Path | None:
    current = path.resolve()
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def _safe_resolve_with_resolved_path(
    workspace_root: str | Path,
    *,
    relative_path: str = ".",
    resolved_path: str | None = None,
) -> Path:
    if not isinstance(resolved_path, str) or not resolved_path.strip():
        return _safe_resolve(workspace_root, relative_path)

    root = Path(workspace_root).resolve()
    target = Path(resolved_path).resolve()
    scope_root = _detect_workspace_scope_root(root)
    allowed_roots = [root]
    if scope_root is not None and scope_root != root:
        allowed_roots.append(scope_root)
    for allowed_root in allowed_roots:
        try:
            target.relative_to(allowed_root)
            return target
        except ValueError:
            continue
    raise WorkspaceSecurityError(f"Resolved path escapes workspace scope: {relative_path or resolved_path}")


def _load_gitignore_pathspec(git_root: Path) -> tuple[Any | None, list[str]]:
    patterns: list[str] = []
    try:
        from pathspec import PathSpec  # type: ignore
    except Exception:
        return None, patterns

    for candidate in [git_root / ".gitignore", git_root / ".git" / "info" / "exclude"]:
        if not candidate.exists():
            continue
        for raw in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            patterns.append(line)
    if not patterns:
        return None, patterns
    return PathSpec.from_lines("gitwildmatch", patterns), patterns


def _path_is_gitignored(pathspec_obj: Any | None, rel_path: str) -> bool:
    if pathspec_obj is None:
        return False
    try:
        return bool(pathspec_obj.match_file(rel_path))
    except Exception:
        return False


def _detect_encoding(raw: bytes) -> tuple[str, str]:
    if not raw:
        return ("utf-8", "")
    try:
        from charset_normalizer import from_bytes  # type: ignore

        best = from_bytes(raw).best()
        if best is not None:
            encoding = str(best.encoding or "utf-8")
            return (encoding, str(best))
    except Exception:
        pass
    return ("utf-8", raw.decode("utf-8", errors="replace"))


def list_dir(
    *,
    workspace_root: str | Path,
    relative_path: str = ".",
    resolved_path: str | None = None,
    include_hidden: bool = False,
    max_entries: int = 500,
) -> dict[str, Any]:
    target = _safe_resolve_with_resolved_path(
        workspace_root,
        relative_path=relative_path,
        resolved_path=resolved_path,
    )
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_dir():
        raise NotADirectoryError(str(target))

    git_root = _detect_git_root(target if target.is_dir() else target.parent)
    gitignore_spec, gitignore_patterns = _load_gitignore_pathspec(git_root) if git_root else (None, [])
    entries = []
    for item in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        if not include_hidden and item.name.startswith("."):
            continue
        if git_root:
            rel = item.resolve().relative_to(git_root.resolve()).as_posix()
            if _path_is_gitignored(gitignore_spec, rel):
                continue
        stat = item.stat()
        entries.append(
            {
                "name": item.name,
                "path": str(item),
                "kind": "dir" if item.is_dir() else "file",
                "size_bytes": stat.st_size,
            }
        )
        if len(entries) >= max_entries:
            break
    return {
        "path": str(target),
        "entries": entries,
        "truncated": len(entries) >= max_entries,
        "git_root": str(git_root) if git_root else None,
        "gitignore_applied": bool(gitignore_spec),
        "gitignore_patterns_count": len(gitignore_patterns),
    }


def read_file(
    *,
    workspace_root: str | Path,
    relative_path: str,
    resolved_path: str | None = None,
    max_bytes: int = 65536,
    source: str | None = None,
) -> dict[str, Any]:
    """Read a workspace-relative file.

    Args:
        source: ``"filesystem"`` reads directly from disk (always returns
            post-mutation state).  ``"git_blob_head"`` or ``None`` (default)
            reads from the git HEAD blob when git is available, falling back
            to the filesystem when it is not.  Use ``source="filesystem"``
            after a mutation step to verify the written content.
    """
    root = _safe_resolve(workspace_root, ".")
    target = _safe_resolve_with_resolved_path(
        root,
        relative_path=relative_path,
        resolved_path=resolved_path,
    )
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_file():
        raise IsADirectoryError(str(target))

    raw = target.read_bytes()
    resolved_source = "filesystem"

    # Only attempt a git read when the caller has not explicitly requested
    # the filesystem source.
    if source != "filesystem":
        git_root = _detect_git_root(root)
        if git_root is not None:
            try:
                from git import Repo  # type: ignore

                repo = Repo(str(git_root))
                rel = target.resolve().relative_to(git_root.resolve()).as_posix()
                blob = repo.head.commit.tree / rel
                raw = bytes(blob.data_stream.read())
                resolved_source = "git_blob_head"
            except Exception:
                resolved_source = "filesystem"

    truncated = len(raw) > max_bytes
    sample = raw[:max_bytes]
    encoding, content = _detect_encoding(sample)
    # UTF-8 BOM must not become part of patch context (live S7 PATCH_CONTEXT_MISMATCH).
    if content.startswith("\ufeff"):
        content = content.lstrip("\ufeff")
    return {
        "path": str(target),
        "encoding": encoding,
        "content": content,
        "size_bytes": len(raw),
        "truncated": truncated,
        "source": resolved_source,
    }


def read_range(
    *,
    workspace_root: str | Path,
    relative_path: str,
    resolved_path: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    symbol: str | None = None,
    max_chars: int = 20000,
) -> dict[str, Any]:
    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")

    target = _safe_resolve_with_resolved_path(
        workspace_root,
        relative_path=relative_path,
        resolved_path=resolved_path,
    )
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_file():
        raise IsADirectoryError(str(target))
    normalized_symbol = str(symbol or "").strip()
    if normalized_symbol:
        symbol_range = resolve_python_symbol_range(file_path=target, symbol_name=normalized_symbol)
        start_line = int(symbol_range.start_line)
        end_line = int(symbol_range.end_line)
    if start_line is None or end_line is None:
        raise ValueError("start_line and end_line are required when symbol is not provided")
    if start_line < 1:
        raise ValueError("start_line must be >= 1")
    if end_line < start_line:
        raise ValueError("end_line must be >= start_line")

    text = target.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    # Convert inclusive 1-based range to Python slices.
    selected = lines[start_line - 1 : end_line]
    content = "".join(selected)
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]
    return {
        "path": str(target),
        "start_line": start_line,
        "end_line": end_line,
        "line_count": len(selected),
        "total_lines": total_lines,
        "content": content,
        "truncated": truncated,
        "max_chars": max_chars,
        "symbol": normalized_symbol or None,
    }


def search(
    *,
    workspace_root: str | Path,
    pattern: str,
    relative_path: str = ".",
    resolved_path: str | None = None,
    max_hits: int = 200,
) -> dict[str, Any]:
    target = _safe_resolve_with_resolved_path(
        workspace_root,
        relative_path=relative_path,
        resolved_path=resolved_path,
    )
    if not target.exists():
        raise FileNotFoundError(str(target))
    hits: list[dict[str, Any]] = []

    rg_cmd = [
        "rg",
        "--json",
        "-n",
        "--hidden",
        "--color",
        "never",
        "--glob",
        "!**/node_modules/**",
        "--glob",
        "!**/.git/**",
        pattern,
        str(target),
    ]
    try:
        proc = subprocess.run(
            rg_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        if proc.returncode in (0, 1):
            for line in proc.stdout.splitlines():
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if event.get("type") != "match":
                    continue
                data = event.get("data") or {}
                path_data = data.get("path") or {}
                line_num = int(data.get("line_number") or 0)
                submatches = list(data.get("submatches") or [])
                text = ""
                if submatches:
                    text = str(submatches[0].get("match", {}).get("text") or "")
                if not text:
                    text = str((data.get("lines") or {}).get("text") or "").rstrip("\n")
                hits.append({"path": str(path_data.get("text") or ""), "line": line_num, "text": text})
                if len(hits) >= max_hits:
                    break
            return {"pattern": pattern, "hits": hits, "truncated": len(hits) >= max_hits, "engine": "rg_json"}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    skip_dirs = {".git", "node_modules", "dist", "build", ".next", "__pycache__"}
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for name in files:
            path = Path(root) / name
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            for idx, line in enumerate(text.splitlines(), start=1):
                if pattern in line:
                    hits.append({"path": str(path), "line": idx, "text": line})
                    if len(hits) >= max_hits:
                        return {"pattern": pattern, "hits": hits, "truncated": True, "engine": "python_fallback"}
    return {"pattern": pattern, "hits": hits, "truncated": False, "engine": "python_fallback"}


def get_status(*, workspace_root: str | Path) -> dict[str, Any]:
    root = _safe_resolve(workspace_root, ".")
    git_dir = root / ".git"
    if not git_dir.exists():
        return {"is_git_repo": False, "root": str(root), "changes": []}

    proc = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v2", "--branch"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    changes: list[dict[str, Any]] = []
    branch: dict[str, Any] = {}
    for raw in proc.stdout.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("# "):
            meta = line[2:]
            if meta.startswith("branch.head "):
                branch["head"] = meta.replace("branch.head ", "", 1).strip()
            elif meta.startswith("branch.oid "):
                branch["oid"] = meta.replace("branch.oid ", "", 1).strip()
            elif meta.startswith("branch.upstream "):
                branch["upstream"] = meta.replace("branch.upstream ", "", 1).strip()
            elif meta.startswith("branch.ab "):
                branch["ahead_behind"] = meta.replace("branch.ab ", "", 1).strip()
            continue
        if line.startswith(("1 ", "2 ")):
            parts = line.split(" ", 9)
            if len(parts) >= 9:
                kind = parts[0]
                xy = parts[1]
                path = parts[-1]
                original_path = None
                if kind == "2" and "\t" in line:
                    path_parts = line.split("\t")
                    if len(path_parts) >= 2:
                        original_path = path_parts[-2]
                        path = path_parts[-1]
                changes.append({"kind": kind, "xy": xy, "path": path, "original_path": original_path})
            continue
        if line.startswith("? "):
            changes.append({"kind": "?", "xy": "??", "path": line[2:].strip(), "original_path": None})
    return {"is_git_repo": True, "root": str(root), "branch": branch, "changes": changes, "format": "porcelain_v2"}
