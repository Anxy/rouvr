"""Immutable full raw workspace snapshots for the Muse execution slice.

A snapshot is a complete lstat inventory of a workspace tree: every entry's
kind, mode, size, mtime, symlink target, and — for regular files — a
streamed SHA-256 digest. The diff between two snapshots reports additions,
deletions, type changes, mode changes, and content changes (digest equality,
so same-size rewrites with restored mtimes are still detected). The
snapshot includes the workspace root itself (kind + mode, path ``""``) so
root mode changes diff.

Traversal is anchored at a held root descriptor: the root is opened with
``O_NOFOLLOW | O_DIRECTORY`` and every entry is listed, stat, opened, and
read relative to that descriptor, so path races cannot redirect coverage.
Nothing is silently omitted: any traversal or read error marks the snapshot
incomplete and raises ``SnapshotIncomplete`` instead of returning partial
coverage. Callers treat an incomplete snapshot as a refusal, never as proof
of no change.
"""

from __future__ import annotations

import hashlib
import os
import stat as statmod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SnapshotIncomplete(RuntimeError):
    """Traversal/read fell short of full coverage. Carries the error list."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__(
            "WORKSPACE_SNAPSHOT_INCOMPLETE: " + "; ".join(self.errors[:5])
        )


@dataclass(frozen=True, slots=True)
class SnapshotEntry:
    """One inventoried path, relative to the snapshot root (POSIX form)."""

    path: str
    kind: str  # "file" | "dir" | "symlink" | "other"
    mode: int | None
    size: int | None
    mtime_ns: int | None
    sha256: str | None  # regular files only
    link_target: str | None  # symlinks only (raw readlink text)


@dataclass(frozen=True, slots=True)
class FileChange:
    """One observed mutation between two snapshots."""

    path: str
    change: str  # "added" | "deleted" | "modified" | "type_changed" | "mode_changed"
    kind: str
    mode: int | None = None
    old_mode: int | None = None
    size: int | None = None
    sha256: str | None = None
    link_target: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    root: str
    entries: tuple[SnapshotEntry, ...]


_HASH_CHUNK = 65536


def _hash_file_at(dfd: int, name: str, rel: str, errors: list[str]) -> str | None:
    """Stream a SHA-256 digest of ``name`` relative to the held ``dfd``."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
    except OSError as exc:
        errors.append(f"open {rel}: {exc}")
        return None
    try:
        digest = hashlib.sha256()
        while True:
            try:
                chunk = os.read(fd, _HASH_CHUNK)
            except OSError as exc:
                errors.append(f"read {rel}: {exc}")
                return None
            if not chunk:
                break
            digest.update(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest()


def _walk_fd(
    dfd: int, rel: str, entries: list[SnapshotEntry], errors: list[str]
) -> None:
    """Inventory one held directory level, recursing through held descriptors."""
    try:
        names = sorted(os.listdir(dfd))
    except OSError as exc:
        errors.append(f"listdir {rel or '.'}: {exc}")
        return
    for name in names:
        child = f"{rel}/{name}" if rel else name
        try:
            st = os.lstat(name, dir_fd=dfd)
        except OSError as exc:
            errors.append(f"lstat {child}: {exc}")
            continue
        mode = st.st_mode
        if statmod.S_ISLNK(mode):
            try:
                target = os.readlink(name, dir_fd=dfd)
            except OSError as exc:
                errors.append(f"readlink {child}: {exc}")
                continue
            entries.append(SnapshotEntry(
                path=child, kind="symlink",
                mode=statmod.S_IMODE(mode), size=None, mtime_ns=st.st_mtime_ns,
                sha256=None, link_target=target,
            ))
        elif statmod.S_ISDIR(mode):
            entries.append(SnapshotEntry(
                path=child, kind="dir",
                mode=statmod.S_IMODE(mode), size=None, mtime_ns=st.st_mtime_ns,
                sha256=None, link_target=None,
            ))
            try:
                sub = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=dfd,
                )
            except OSError as exc:
                errors.append(f"opendir {child}: {exc}")
                continue
            try:
                _walk_fd(sub, child, entries, errors)
            finally:
                os.close(sub)
        elif statmod.S_ISREG(mode):
            digest = _hash_file_at(dfd, name, child, errors)
            if digest is None:
                continue
            entries.append(SnapshotEntry(
                path=child, kind="file",
                mode=statmod.S_IMODE(mode), size=st.st_size,
                mtime_ns=st.st_mtime_ns, sha256=digest, link_target=None,
            ))
        else:
            entries.append(SnapshotEntry(
                path=child, kind="other",
                mode=statmod.S_IMODE(mode), size=None, mtime_ns=st.st_mtime_ns,
                sha256=None, link_target=None,
            ))


def snapshot_workspace(root: Path | str) -> WorkspaceSnapshot:
    """Inventory ``root`` completely through a held directory descriptor.

    The root is opened with ``O_NOFOLLOW | O_DIRECTORY`` (a symlinked or
    missing root refuses) and the walk runs entirely relative to that
    descriptor. The root itself is recorded (kind + mode, path ``""``).
    Raises ``SnapshotIncomplete`` on any gap.
    """
    errors: list[str] = []
    entries: list[SnapshotEntry] = []
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise SnapshotIncomplete(
            [f"snapshot root is not a real directory: {root}: {exc}"]
        ) from exc
    try:
        try:
            st = os.fstat(root_fd)
        except OSError as exc:
            raise SnapshotIncomplete(
                [f"snapshot root fstat failed: {root}: {exc}"]
            ) from exc
        if not statmod.S_ISDIR(st.st_mode):
            raise SnapshotIncomplete([f"snapshot root is not a directory: {root}"])
        entries.append(SnapshotEntry(
            path="", kind="dir", mode=statmod.S_IMODE(st.st_mode),
            size=None, mtime_ns=None, sha256=None, link_target=None,
        ))
        _walk_fd(root_fd, "", entries, errors)
    finally:
        os.close(root_fd)
    if errors:
        raise SnapshotIncomplete(errors)
    entries.sort(key=lambda entry: entry.path)
    return WorkspaceSnapshot(root=str(root), entries=tuple(entries))


def diff_snapshots(before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> tuple[FileChange, ...]:
    """Report every mutation between two complete snapshots."""
    old = {entry.path: entry for entry in before.entries}
    new = {entry.path: entry for entry in after.entries}
    changes: list[FileChange] = []
    for path in sorted(set(old) | set(new)):
        first, second = old.get(path), new.get(path)
        if first is None and second is not None:
            changes.append(FileChange(
                path=path, change="added", kind=second.kind, mode=second.mode,
                size=second.size, sha256=second.sha256,
                link_target=second.link_target,
            ))
        elif first is not None and second is None:
            changes.append(FileChange(
                path=path, change="deleted", kind=first.kind, mode=first.mode,
                size=first.size, sha256=None, link_target=first.link_target,
            ))
        elif first is not None and second is not None:
            if first.kind != second.kind:
                changes.append(FileChange(
                    path=path, change="type_changed", kind=second.kind,
                    mode=second.mode, size=second.size, sha256=second.sha256,
                    link_target=second.link_target,
                ))
            elif first.kind == "symlink" and first.link_target != second.link_target:
                changes.append(FileChange(
                    path=path, change="modified", kind="symlink",
                    mode=second.mode, link_target=second.link_target,
                ))
            elif first.kind == "file" and first.sha256 != second.sha256:
                changes.append(FileChange(
                    path=path, change="modified", kind="file", mode=second.mode,
                    old_mode=first.mode if first.mode != second.mode else None,
                    size=second.size, sha256=second.sha256,
                ))
            elif first.kind == "dir" and first.mode != second.mode:
                changes.append(FileChange(
                    path=path, change="mode_changed", kind="dir",
                    mode=second.mode, old_mode=first.mode,
                ))
            elif first.kind == "file" and first.mode != second.mode:
                changes.append(FileChange(
                    path=path, change="mode_changed", kind="file",
                    mode=second.mode, old_mode=first.mode,
                    size=second.size, sha256=second.sha256,
                ))
            # Directory mtime-only drift (no entry or mode change) is not a
            # material mutation and is ignored; everything else compared above.
    return tuple(changes)


def changes_to_jsonable(changes: tuple[FileChange, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in changes:
        record: dict[str, Any] = {"path": item.path, "change": item.change, "kind": item.kind}
        if item.mode is not None:
            record["mode"] = oct(item.mode)
        if item.old_mode is not None:
            record["old_mode"] = oct(item.old_mode)
        if item.size is not None:
            record["size"] = item.size
        if item.sha256 is not None:
            record["sha256"] = item.sha256
        if item.link_target is not None:
            record["link_target"] = item.link_target
        out.append(record)
    return out


__all__ = [
    "FileChange",
    "SnapshotEntry",
    "SnapshotIncomplete",
    "WorkspaceSnapshot",
    "changes_to_jsonable",
    "diff_snapshots",
    "snapshot_workspace",
]
