"""C016: isolated workspace / worktree lifecycle + lease.

DoD: each run has one isolated, recoverable root.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from backend.aix_core.runtime.objective.ulid_compat import new_ulid
class WorkspaceError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class WorkspaceLease:
    tenant_id: str
    run_id: str
    workspace_id: str
    root_path: str
    holder: str
    created_at: str
    last_heartbeat_at: str = ""
    status: str = "ACTIVE"  # ACTIVE | RELEASED | ORPHAN_ATTACHED | EXPIRED

    def __post_init__(self) -> None:
        if not self.last_heartbeat_at:
            self.last_heartbeat_at = self.created_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "root_path": self.root_path,
            "holder": self.holder,
            "created_at": self.created_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "status": self.status,
        }


class WorkspaceLeaseStore:
    """One active workspace root per (tenant, run) with atomic claims, heartbeat, and path locks."""

    def __init__(self, *, base_dir: str | Path | None = None) -> None:
        self._base = Path(base_dir) if base_dir else Path(os.environ.get("AIX_WORKSPACE_BASE", ".")).resolve() / ".aix_workspaces"
        self._leases: dict[tuple[str, str], WorkspaceLease] = {}
        self._path_locks: dict[tuple[str, str, str], str] = {}  # (tenant, run, path) -> holder

    def create(
        self,
        *,
        tenant_id: str,
        run_id: str,
        holder: str,
        workspace_id: str | None = None,
    ) -> WorkspaceLease:
        if not tenant_id.strip() or not run_id.strip():
            raise WorkspaceError("INVALID_WORKSPACE_TARGET")
        key = (tenant_id, run_id)
        existing = self._leases.get(key)
        if existing is not None and existing.status == "ACTIVE":
            if existing.holder != holder:
                raise WorkspaceError("WORKSPACE_LEASE_HELD", existing.holder)
            return existing
        wid = workspace_id or f"ws_{new_ulid()[:12]}"
        root = (self._base / tenant_id / run_id / wid).resolve()
        self._assert_under_base(root)
        root.mkdir(parents=True, exist_ok=True)
        now_ts = _utc()
        lease = WorkspaceLease(
            tenant_id=tenant_id,
            run_id=run_id,
            workspace_id=wid,
            root_path=str(root),
            holder=holder,
            created_at=now_ts,
            last_heartbeat_at=now_ts,
            status="ACTIVE",
        )
        self._leases[key] = lease
        return lease

    def heartbeat(self, *, tenant_id: str, run_id: str, holder: str) -> WorkspaceLease:
        key = (tenant_id, run_id)
        lease = self._leases.get(key)
        if lease is None or lease.status == "RELEASED":
            raise WorkspaceError("WORKSPACE_NOT_FOUND", run_id)
        if lease.holder != holder:
            raise WorkspaceError("WORKSPACE_LEASE_HELD", lease.holder)
        lease.last_heartbeat_at = _utc()
        return lease

    def attach(self, *, tenant_id: str, run_id: str, holder: str) -> WorkspaceLease:
        key = (tenant_id, run_id)
        lease = self._leases.get(key)
        if lease is None:
            raise WorkspaceError("WORKSPACE_NOT_FOUND", run_id)
        root = Path(lease.root_path)
        if not root.exists():
            raise WorkspaceError("WORKSPACE_ROOT_MISSING", lease.root_path)
        self._assert_under_base(root.resolve())
        lease.holder = holder
        lease.status = "ORPHAN_ATTACHED" if lease.status != "ACTIVE" else "ACTIVE"
        lease.last_heartbeat_at = _utc()
        return lease

    def acquire_path_lock(self, *, tenant_id: str, run_id: str, relative_path: str, holder: str) -> None:
        key = (tenant_id, run_id, relative_path.replace("\\", "/").lstrip("/"))
        current = self._path_locks.get(key)
        if current is not None and current != holder:
            raise WorkspaceError("PATH_LOCK_HELD", current)
        self._path_locks[key] = holder

    def release_path_lock(self, *, tenant_id: str, run_id: str, relative_path: str, holder: str) -> None:
        key = (tenant_id, run_id, relative_path.replace("\\", "/").lstrip("/"))
        current = self._path_locks.get(key)
        if current is not None:
            if current != holder:
                raise WorkspaceError("PATH_LOCK_NOT_OWNER", current)
            del self._path_locks[key]

    def release(self, *, tenant_id: str, run_id: str, holder: str, cleanup: bool = False) -> None:
        key = (tenant_id, run_id)
        lease = self._leases.get(key)
        if lease is None:
            return
        if lease.holder != holder:
            raise WorkspaceError("WORKSPACE_LEASE_NOT_OWNER", lease.holder)
        lease.status = "RELEASED"
        if cleanup:
            root = Path(lease.root_path)
            if root.exists():
                self._assert_under_base(root.resolve())
                shutil.rmtree(root, ignore_errors=True)

    def resolve_path(self, *, tenant_id: str, run_id: str, relative: str) -> Path:
        lease = self._leases.get((tenant_id, run_id))
        if lease is None or lease.status == "RELEASED":
            raise WorkspaceError("WORKSPACE_NOT_FOUND", run_id)
        base = Path(lease.root_path).resolve()
        rel = relative.replace("\\", "/").lstrip("/")
        if rel.startswith("..") or "/../" in f"/{rel}/":
            raise WorkspaceError("PATH_TRAVERSAL", relative)
        candidate = (base / rel).resolve()
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise WorkspaceError("PATH_ESCAPES_WORKSPACE_ROOT", relative) from exc
        if candidate.is_symlink():
            target = candidate.resolve()
            try:
                target.relative_to(base)
            except ValueError as exc:
                raise WorkspaceError("SYMLINK_ESCAPE", relative) from exc
        return candidate

    def get(self, *, tenant_id: str, run_id: str) -> WorkspaceLease | None:
        return self._leases.get((tenant_id, run_id))

    def _assert_under_base(self, path: Path) -> None:
        base = self._base.resolve()
        try:
            path.resolve().relative_to(base)
        except ValueError as exc:
            raise WorkspaceError("WORKSPACE_OUTSIDE_BASE", str(path)) from exc


__all__ = ["WorkspaceError", "WorkspaceLease", "WorkspaceLeaseStore"]
