"""AIX-E2E-CYCLE-001G-W05: workspace isolation for step execution.

Every step attempt gets a workspace rooted at a scoped, deterministic path:
`<base>/<tenant_id>/<YYYY-MM-DD>/<cycle_id>/<step_id>/<attempt_id>`.

When a discuss thread id is supplied (``session_id=disc_*``), the cycle nests under
that session folder so chat + execute share one parent:
`<base>/<tenant_id>/<YYYY-MM-DD>/<disc_*>/<cycle_id>/<step_id>/<attempt_id>`.

Greenfield deliverables (new files such as `schema.sql`) are written under a
cycle project folder named from the objective:
`<base>/<tenant_id>/<YYYY-MM-DD>/<cycle_id>/<objective-slug>/`
(or under the discuss session when ``session_id`` is set).

The ``YYYY-MM-DD`` segment is sticky per cycle: once a cycle folder exists under a
day, later steps/continues reuse that day (and merge any stray newer-day files)
so overnight continue does not fork a second project tree.

This module does not create an OS-level sandbox (container/chroot/jail) — like the
existing bounded adapters in this package (`adapters/devexecutor.py`), it is a
bounded proof: real, testable path-boundary logic that a future live executor
integration must call before touching disk, not a simulated result. What it
enforces is real: path traversal, symlink escape outside the scoped root, and
cross-tenant/cross-attempt path confusion are all rejected by actually resolving
paths on disk (`Path.resolve()`, `os.path.realpath`), not by string matching
alone — string matching can be defeated by a symlink; resolution cannot.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_OBJECTIVE_NAME_RE = re.compile(
    r"(?:for|named|called)\s+(?:an?\s+|the\s+)?([A-Za-z][A-Za-z0-9][A-Za-z0-9 \-]{2,80})",
    re.IGNORECASE,
)


def workspace_date_key(now: datetime | None = None) -> str:
    """UTC calendar day used as a single path component (no separators that fail isolation)."""
    dt = now or datetime.now(UTC)
    return dt.strftime("%Y-%m-%d")


def objective_folder_slug(objective: str, *, max_len: int = 48) -> str:
    """Stable folder name derived from the objective / object name.

    Prefers phrases like "for an Attendance Registration System", otherwise falls
    back to the leading meaningful words. Always path-safe (no separators).
    """
    text = " ".join(str(objective or "").strip().split())
    if not text:
        return "project"
    match = _OBJECTIVE_NAME_RE.search(text)
    candidate = match.group(1) if match else text
    candidate = re.split(
        r"\b(?:supporting|with|that|which|using|without|including)\b",
        candidate,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    slug = re.sub(r"[^A-Za-z0-9]+", "-", candidate).strip("-").lower()
    if not slug:
        slug = "project"
    slug = slug[: max(8, max_len)].rstrip("-") or "project"
    _reject_path_component(slug)
    return slug


def cycle_project_relative_path(
    *,
    tenant_id: str,
    cycle_id: str,
    objective: str,
    date_key: str = "",
    session_id: str = "",
) -> Path:
    """Relative path of the cycle deliverable root under the workspace base.

    When ``session_id`` is a discuss thread (``disc_*``), nest the cycle under that
    session folder so chat tokens and execute artifacts share one parent:
    ``<tenant>/<date>/<disc_*>/<cyc_*>/<slug>/``.
    """
    day = str(date_key or "").strip() or workspace_date_key()
    slug = objective_folder_slug(objective)
    session = str(session_id or "").strip()
    parts: list[str] = [tenant_id, day]
    if session and session != cycle_id:
        parts.append(session)
    parts.extend([cycle_id, slug])
    for part in parts:
        _reject_path_component(part)
    return Path(*parts)


class WorkspaceIsolationError(ValueError):
    """Raised for any workspace boundary violation. Fail closed — never auto-corrected."""


@dataclass(frozen=True)
class WorkspaceScopeV1:
    tenant_id: str
    cycle_id: str
    step_id: str
    attempt_id: str
    date_key: str = ""
    # Optional discuss thread id (disc_*). When set, workspaces nest under that folder.
    session_id: str = ""

    def resolved_date_key(self) -> str:
        return str(self.date_key or "").strip() or workspace_date_key()

    def resolved_session_id(self) -> str:
        session = str(self.session_id or "").strip()
        if session and session != self.cycle_id:
            return session
        return ""

    def relative_path(self) -> Path:
        date_key = self.resolved_date_key()
        parts: list[str] = [self.tenant_id, date_key]
        session = self.resolved_session_id()
        if session:
            parts.append(session)
        parts.extend([self.cycle_id, self.step_id, self.attempt_id])
        for part in parts:
            _reject_path_component(part)
        return Path(*parts)


_DATE_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def resolve_sticky_date_key(
    base_root: Path,
    *,
    tenant_id: str,
    cycle_id: str,
    session_id: str = "",
) -> str:
    """Return the calendar day folder this cycle already uses, else today (UTC).

    Prefers the earliest existing day so continue/resume stays in one project tree
    instead of forking into a new ``YYYY-MM-DD`` overnight.
    """
    tenant = str(tenant_id or "").strip()
    cycle = str(cycle_id or "").strip()
    if not tenant or not cycle:
        return workspace_date_key()
    tenant_dir = Path(base_root) / tenant
    if not tenant_dir.is_dir():
        return workspace_date_key()
    session = str(session_id or "").strip()
    found: list[str] = []
    try:
        day_dirs = sorted(p for p in tenant_dir.iterdir() if p.is_dir())
    except OSError:
        return workspace_date_key()
    for day_dir in day_dirs:
        day = day_dir.name
        if not _DATE_KEY_RE.match(day):
            continue
        candidates = [day_dir / cycle]
        if session and session != cycle:
            candidates.append(day_dir / session / cycle)
        # Discuss session may be present even when caller omitted session_id.
        try:
            for child in day_dir.iterdir():
                if child.is_dir() and child.name.startswith("disc_"):
                    candidates.append(child / cycle)
        except OSError:
            pass
        if any(c.is_dir() for c in candidates):
            found.append(day)
    if found:
        return sorted(set(found))[0]
    return workspace_date_key()


def _reject_path_component(value: str) -> None:
    if not value or not value.strip():
        raise WorkspaceIsolationError("workspace scope component must not be empty")
    if value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise WorkspaceIsolationError(f"unsafe workspace scope component: {value!r}")


class WorkspaceIsolationBoundary:
    """Resolves and validates one step attempt's workspace root under a fixed base
    directory that must already exist. Never creates the base directory itself — that
    is an infrastructure decision outside this packet's scope."""

    def __init__(self, base_root: str | os.PathLike[str]) -> None:
        resolved_base = Path(base_root).resolve()
        if not resolved_base.is_dir():
            raise WorkspaceIsolationError(f"workspace base_root does not exist or is not a directory: {resolved_base}")
        self._base_root = resolved_base

    @property
    def base_root(self) -> Path:
        return self._base_root

    def resolve_workspace(self, scope: WorkspaceScopeV1, *, must_exist: bool = False) -> Path:
        """Return the real, resolved workspace path for `scope`, guaranteed to be inside
        `base_root`. Raises `WorkspaceIsolationError` if the resolved path (following any
        symlinks) would escape `base_root`, or if `must_exist` is True and it doesn't."""
        candidate = self._base_root / scope.relative_path()
        # Resolve without requiring existence (strict=False) so a not-yet-created
        # workspace can still be validated before mkdir; a symlink planted anywhere in
        # an *existing* ancestor is still followed and checked.
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self._base_root)
        except ValueError as exc:
            raise WorkspaceIsolationError(
                f"workspace path escapes base_root: {resolved} not under {self._base_root}"
            ) from exc
        if must_exist and not resolved.is_dir():
            raise WorkspaceIsolationError(f"workspace does not exist: {resolved}")
        return resolved

    def create_workspace(self, scope: WorkspaceScopeV1) -> Path:
        """Create (if absent) and return the scoped workspace directory. Idempotent."""
        resolved = self.resolve_workspace(scope, must_exist=False)
        resolved.mkdir(parents=True, exist_ok=True)
        # Re-resolve after creation: if an attacker (or a stale prior run) planted a
        # symlink at one of the intermediate path components, the fully-created path
        # may now resolve somewhere else. Catch that instead of trusting the pre-create
        # resolution.
        post_create = resolved.resolve(strict=True)
        try:
            post_create.relative_to(self._base_root)
        except ValueError as exc:
            raise WorkspaceIsolationError(
                f"workspace escaped base_root after creation (symlink planted mid-path?): "
                f"{post_create} not under {self._base_root}"
            ) from exc
        self.write_current_pointers(scope, post_create)
        return post_create

    def create_cycle_project_root(
        self,
        *,
        tenant_id: str,
        cycle_id: str,
        objective: str,
        date_key: str = "",
        session_id: str = "",
    ) -> Path:
        """Create the objective-named deliverable folder under the cycle."""
        day = str(date_key or "").strip() or resolve_sticky_date_key(
            self._base_root,
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            session_id=session_id,
        )
        relative = cycle_project_relative_path(
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            objective=objective,
            date_key=day,
            session_id=session_id,
        )
        candidate = (self._base_root / relative).resolve(strict=False)
        try:
            candidate.relative_to(self._base_root)
        except ValueError as exc:
            raise WorkspaceIsolationError(
                f"cycle project path escapes base_root: {candidate} not under {self._base_root}"
            ) from exc
        candidate.mkdir(parents=True, exist_ok=True)
        post = candidate.resolve(strict=True)
        try:
            post.relative_to(self._base_root)
        except ValueError as exc:
            raise WorkspaceIsolationError(
                f"cycle project escaped base_root after creation: {post} not under {self._base_root}"
            ) from exc
        self._merge_stray_cycle_deliverables(
            sticky_root=post,
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            sticky_date=day,
            objective=objective,
            session_id=session_id,
        )
        cycle_folder = self._base_root / relative.parent
        pointer = cycle_folder / "DELIVERABLE_ROOT.json"
        try:
            pointer.write_text(
                json.dumps(
                    {
                        "tenant_id": tenant_id,
                        "date_key": day,
                        "cycle_id": cycle_id,
                        "session_id": str(session_id or "").strip() or None,
                        "objective_slug": objective_folder_slug(objective),
                        "deliverable_root": str(post),
                        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        return post

    def _merge_stray_cycle_deliverables(
        self,
        *,
        sticky_root: Path,
        tenant_id: str,
        cycle_id: str,
        sticky_date: str,
        objective: str,
        session_id: str = "",
    ) -> None:
        """Copy files from same-cycle folders on other days into the sticky project root.

        Overnight continue used to create a second ``YYYY-MM-DD`` tree; pull any
        newer-day deliverables back into the original project so one folder remains
        canonical.
        """
        import shutil

        slug = objective_folder_slug(objective)
        tenant_dir = self._base_root / tenant_id
        if not tenant_dir.is_dir():
            return
        session = str(session_id or "").strip()
        try:
            day_dirs = [p for p in tenant_dir.iterdir() if p.is_dir() and _DATE_KEY_RE.match(p.name)]
        except OSError:
            return
        for day_dir in day_dirs:
            if day_dir.name == sticky_date:
                continue
            roots: list[Path] = []
            if session and session != cycle_id:
                roots.append(day_dir / session / cycle_id / slug)
            roots.append(day_dir / cycle_id / slug)
            try:
                for child in day_dir.iterdir():
                    if child.is_dir() and child.name.startswith("disc_"):
                        roots.append(child / cycle_id / slug)
            except OSError:
                pass
            for other in roots:
                if not other.is_dir() or other.resolve() == sticky_root.resolve():
                    continue
                try:
                    for src in other.rglob("*"):
                        if not src.is_file():
                            continue
                        rel = src.relative_to(other)
                        dest = sticky_root / rel
                        if dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime:
                            continue
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dest)
                except OSError:
                    continue

    def resolve_target(self, scope: WorkspaceScopeV1, relative_path: str) -> Path:
        """Resolve a relative file path inside the workspace scope, guaranteeing it does not escape base_root."""
        ws_root = self.create_workspace(scope)
        rel = relative_path.replace("\\", "/").lstrip("/")
        if rel.startswith("..") or "/../" in f"/{rel}/":
            raise WorkspaceIsolationError(f"path traversal rejected: {relative_path!r}")
        target = (ws_root / rel).resolve(strict=False)
        try:
            target.relative_to(ws_root)
        except ValueError as exc:
            raise WorkspaceIsolationError(f"path {relative_path!r} escapes workspace root: {target}") from exc
        return target


    def write_current_pointers(self, scope: WorkspaceScopeV1, workspace: Path) -> None:
        """Write CURRENT.json under tenant and date folders so today's run is findable."""
        date_key = scope.resolved_date_key()
        payload = {
            "tenant_id": scope.tenant_id,
            "date_key": date_key,
            "cycle_id": scope.cycle_id,
            "step_id": scope.step_id,
            "attempt_id": scope.attempt_id,
            "workspace": str(workspace),
            "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        tenant_dir = self._base_root / scope.tenant_id
        date_dir = tenant_dir / date_key
        for folder in (tenant_dir, date_dir):
            try:
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "CURRENT.json").write_text(
                    json.dumps(payload, indent=2) + "\n", encoding="utf-8"
                )
            except OSError:
                continue

    def assert_no_foreign_symlinks(self, scope: WorkspaceScopeV1) -> None:
        """Walk an existing workspace and reject any symlink whose target resolves
        outside `base_root`. Call this before treating a workspace's contents as safe
        to read/package into evidence."""
        workspace = self.resolve_workspace(scope, must_exist=True)
        for dirpath, dirnames, filenames in os.walk(workspace, followlinks=False):
            for name in [*dirnames, *filenames]:
                entry = Path(dirpath) / name
                if entry.is_symlink():
                    target = entry.resolve(strict=False)
                    try:
                        target.relative_to(self._base_root)
                    except ValueError as exc:
                        raise WorkspaceIsolationError(
                            f"symlink {entry} escapes base_root: target {target}"
                        ) from exc

    def cleanup_contract(self, scope: WorkspaceScopeV1) -> Path:
        """Return the exact path a caller must remove after the attempt closes. This
        function does not delete anything itself — deletion is a step-lifecycle
        decision (only after evidence has been captured), not this boundary's call."""
        return self.resolve_workspace(scope, must_exist=False)


__all__ = [
    "WorkspaceIsolationError",
    "WorkspaceScopeV1",
    "WorkspaceIsolationBoundary",
    "workspace_date_key",
    "resolve_sticky_date_key",
    "objective_folder_slug",
    "cycle_project_relative_path",
]
