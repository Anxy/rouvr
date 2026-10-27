"""C012: per-run session lease locks for restart-safe commands.

In-process leases prove mutual exclusion for concurrent resume/cancel on the
same run. Postgres advisory locks can replace the store later without changing
the acquire/release contract.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Any


class SessionLockError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass
class SessionLease:
    tenant_id: str
    run_id: str
    holder: str
    expires_at: float
    version_at_acquire: int | None = None


class InMemorySessionLockStore:
    """Fail-closed exclusive lease per (tenant_id, run_id)."""

    def __init__(self, *, default_ttl_seconds: float = 30.0) -> None:
        self._default_ttl = float(default_ttl_seconds)
        self._lock = Lock()
        self._leases: dict[tuple[str, str], SessionLease] = {}

    def acquire(
        self,
        *,
        tenant_id: str,
        run_id: str,
        holder: str,
        ttl_seconds: float | None = None,
        version_at_acquire: int | None = None,
        now: float | None = None,
    ) -> SessionLease:
        if not tenant_id.strip() or not run_id.strip():
            raise SessionLockError("INVALID_LOCK_TARGET", "tenant_id and run_id required")
        if not holder.strip():
            raise SessionLockError("INVALID_LOCK_HOLDER", "holder required")
        ts = time.time() if now is None else float(now)
        ttl = self._default_ttl if ttl_seconds is None else float(ttl_seconds)
        key = (tenant_id, run_id)
        with self._lock:
            existing = self._leases.get(key)
            if existing is not None and existing.expires_at > ts and existing.holder != holder:
                raise SessionLockError(
                    "RUN_LOCK_HELD",
                    f"holder={existing.holder} expires_at={existing.expires_at}",
                )
            lease = SessionLease(
                tenant_id=tenant_id,
                run_id=run_id,
                holder=holder,
                expires_at=ts + ttl,
                version_at_acquire=version_at_acquire,
            )
            self._leases[key] = lease
            return lease

    def release(self, *, tenant_id: str, run_id: str, holder: str) -> None:
        key = (tenant_id, run_id)
        with self._lock:
            existing = self._leases.get(key)
            if existing is None:
                return
            if existing.holder != holder:
                raise SessionLockError("RUN_LOCK_NOT_OWNER", existing.holder)
            del self._leases[key]

    def peek(self, *, tenant_id: str, run_id: str, now: float | None = None) -> SessionLease | None:
        ts = time.time() if now is None else float(now)
        key = (tenant_id, run_id)
        with self._lock:
            existing = self._leases.get(key)
            if existing is None:
                return None
            if existing.expires_at <= ts:
                del self._leases[key]
                return None
            return existing


def lock_conflict_payload(*, operation: str, tenant_id: str, exc: SessionLockError) -> dict[str, Any]:
    return {
        "ok": False,
        "operation": operation,
        "tenant_id": tenant_id,
        "error": {"code": exc.code, "detail": exc.detail},
    }


__all__ = [
    "SessionLockError",
    "SessionLease",
    "InMemorySessionLockStore",
    "lock_conflict_payload",
]
