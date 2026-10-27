"""Governed filesystem capabilities: CREATE_DIRECTORY and RENAME_PATH.

These are program-owned primitives, not model-chosen tools. The model may help
extract a path when ambiguous; the executor is deterministic: confine to the
approved repository root, mutate, verify, stop. No patch codegen. No retry loop.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.aix_core.runtime.executor.read_file_execute import (
    _looks_like_a_file,
    extract_target_path,
    normalize_repo_relative_path,
    resolve_relative_path,
)

CAPABILITY_CREATE_DIRECTORY = "filesystem.create_directory"
CAPABILITY_RENAME_PATH = "filesystem.rename_path"

_SYMBOL_RENAME_MARKERS = (
    "function",
    "method",
    "class",
    "variable",
    "symbol",
    "parameter",
    "identifier",
    "import",
)

_FILENAME_RE = re.compile(r"[A-Za-z0-9._-]+\.[A-Za-z0-9]{1,10}")
# Destination may be a bare filename or a repo-relative path ("assets/test.png") --
# the char class includes "/" so a path-qualified destination is captured whole
# instead of stopping at the first path segment.
_RENAME_TO_RE = re.compile(
    r"(?i)\bto\s+[\"'`]?([A-Za-z0-9._/-]+\.[A-Za-z0-9]{1,10}|[A-Za-z][A-Za-z0-9._/-]{0,63})"
)


class FilesystemPrimitiveError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}:{detail}")


def _rename_operand_is_path_like(operand: str | None) -> bool:
    """True when a rename operand reads as a path (separator or file extension).

    Bare words (``berth_fee``, ``dock_fee``) are symbols, not paths -- treating
    them as paths is exactly how a symbol rename becomes a catastrophic file
    rename (live WIDEN-w3: ``tests/test_ship.py`` moved to ``tests/dock_fee``).
    """
    token = str(operand or "").strip().strip("\"'`")
    if not token:
        return False
    if "/" in token or "\\" in token:
        return True
    return bool(_FILENAME_RE.search(token) and "." in token)


def looks_like_rename_path_objective(text: str) -> bool:
    """True for filesystem rename/mv of a path. Never steals symbol/variable rename."""
    lowered = (text or "").casefold()
    if not lowered:
        return False
    if not re.search(r"\brename\b|\bmv\b", lowered):
        return False
    if any(
        f"rename the {marker}" in lowered or f"rename a {marker}" in lowered
        for marker in _SYMBOL_RENAME_MARKERS
    ):
        return False
    has_filename = bool(_FILENAME_RE.search(text or ""))
    if not has_filename and any(marker in lowered for marker in _SYMBOL_RENAME_MARKERS):
        return False
    if "rename the file" in lowered or "rename file" in lowered:
        return True
    if "rename the folder" in lowered or "rename folder" in lowered:
        return True
    if "rename the directory" in lowered or "filesystem rename" in lowered:
        return True
    if re.search(r"\brename\b.+\bto\b", lowered):
        # A filename elsewhere in the text (e.g. a test file mentioned for
        # context) must not launder a bare-word symbol rename into a file
        # rename. At least one operand itself must read as a path.
        source_match = re.search(r"(?i)\brename\s+(.+?)\s+\bto\b", text or "")
        dest_match = _RENAME_TO_RE.search(text or "")
        source = source_match.group(1) if source_match else None
        dest = dest_match.group(1) if dest_match else None
        if source is not None or dest is not None:
            return _rename_operand_is_path_like(source) or _rename_operand_is_path_like(dest)
        if has_filename:
            return True
    if re.search(r"\bmv\s+\S+\s+\S+", lowered):
        return True
    return False


def resolve_rename_paths(
    *,
    objective: str,
    allowed_files: tuple[str, ...] = (),
) -> tuple[str, str] | None:
    """Repo-relative (source, dest). Dest stays in the source directory when only a name is given.

    Bare-word symbol renames never resolve: every caller gates on
    `looks_like_rename_path_objective`, and this refuses them too so no future
    caller can turn ``rename old_symbol to new_symbol`` into a file move.
    """
    if not looks_like_rename_path_objective(objective):
        return None
    dest_name = _extract_rename_dest_name(objective)
    source = _extract_rename_source(objective, allowed_files, dest_name)
    if not source or not dest_name:
        return None
    source_rel = normalize_repo_relative_path(source).replace("\\", "/").strip("/")
    dest_rel = normalize_repo_relative_path(dest_name).replace("\\", "/").strip("/")
    if not source_rel or not dest_rel:
        return None
    if "/" not in dest_rel and "/" in source_rel:
        parent = str(Path(source_rel).parent).replace("\\", "/")
        if parent not in {"", "."}:
            dest_rel = f"{parent}/{Path(dest_rel).name}"
    elif "/" not in dest_rel:
        dest_rel = Path(dest_rel).name
    if source_rel == dest_rel:
        return None
    return source_rel, dest_rel


def rename_paths_from_step_contract(contract: object) -> tuple[str, str] | None:
    """Honor an ACCEPTED plan: source from target_scope, dest from purpose/acceptance.

    Dispatch reads the planner's call, not a new user-prompt phrase list.
    """
    purpose = str(getattr(contract, "purpose", "") or "")
    acceptance = tuple(getattr(contract, "acceptance_criteria", ()) or ())
    scope = tuple(str(p).strip() for p in (getattr(contract, "target_scope", ()) or ()) if str(p).strip())
    blob = " ".join((purpose, *acceptance))
    lowered = blob.casefold()
    if not looks_like_rename_path_objective(blob) and "no longer exists" not in lowered:
        return None
    return resolve_rename_paths(objective=blob, allowed_files=tuple(scope))


def apply_rename_path(
    *,
    repository_root: Path,
    source_rel: str,
    dest_rel: str,
    apply: bool = True,
) -> dict[str, object]:
    """Rename a confined path. Idempotent if source is gone and dest exists."""
    src_rel = _confine_relative(repository_root, source_rel)
    dst_rel = _confine_relative(repository_root, dest_rel)
    src = (repository_root / src_rel).resolve()
    dst = (repository_root / dst_rel).resolve()
    root = repository_root.resolve()
    _assert_inside(root, src)
    _assert_inside(root, dst)

    if dst.exists() and not src.exists():
        return {
            "source": src_rel,
            "dest": dst_rel,
            "already": True,
            "applied": False,
        }
    if not src.exists():
        raise FilesystemPrimitiveError("PATH_NOT_FOUND", src_rel)
    if dst.exists():
        raise FilesystemPrimitiveError(
            "RENAME_TARGET_EXISTS",
            f"{dst_rel} already exists; refuse overwrite",
        )
    if apply:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        if not dst.exists() or src.exists():
            raise FilesystemPrimitiveError(
                "RENAME_VERIFY_FAILED",
                f"after rename expected {dst_rel} present and {src_rel} absent",
            )
    return {
        "source": src_rel,
        "dest": dst_rel,
        "already": False,
        "applied": apply,
    }


def _confine_relative(repository_root: Path, relative: str) -> str:
    rel = resolve_relative_path(relative, repository_root)
    if rel is None:
        raise FilesystemPrimitiveError("PATH_NOT_ALLOWED", relative)
    return rel.replace("\\", "/")


def _assert_inside(root: Path, candidate: Path) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise FilesystemPrimitiveError("PATH_NOT_ALLOWED", str(candidate)) from exc


def _extract_rename_dest_name(objective: str) -> str | None:
    match = _RENAME_TO_RE.search(objective or "")
    if not match:
        return None
    name = match.group(1).strip().strip("\"'`")
    return name or None


def _extract_rename_source(
    objective: str,
    allowed_files: tuple[str, ...],
    dest_name: str | None,
) -> str | None:
    dest_base = Path(dest_name).name if dest_name else ""
    for raw in allowed_files:
        rel = normalize_repo_relative_path(str(raw))
        if not rel:
            continue
        posix = rel.replace("\\", "/").strip("/")
        if dest_base and Path(posix).name == dest_base:
            continue
        if _looks_like_a_file(posix) or posix:
            return posix
    quoted = extract_target_path(objective or "")
    if quoted:
        posix = normalize_repo_relative_path(quoted).replace("\\", "/").strip("/")
        name = Path(posix).name
        if name and name != dest_base:
            return posix
    names = _FILENAME_RE.findall(objective or "")
    for name in names:
        if dest_base and name == dest_base:
            continue
        return name
    return None


__all__ = [
    "CAPABILITY_CREATE_DIRECTORY",
    "CAPABILITY_RENAME_PATH",
    "FilesystemPrimitiveError",
    "looks_like_rename_path_objective",
    "resolve_rename_paths",
    "rename_paths_from_step_contract",
    "apply_rename_path",
]
