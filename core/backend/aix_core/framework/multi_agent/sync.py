"""AIX Framework Multi-Agent Scope Synchronization.

Sprint: F10 Work Compiler + Multi-Agent Runtime (F10-P4)
Prevents overlapping concurrent mutations on identical files/scopes.
"""

from __future__ import annotations

import threading
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class ScopeLockConflictError(RuntimeError):
    """Raised when an agent attempts to lock a scope already held by another unit."""


class ScopeLock(BaseModel):
    """Lock grant on a specified scope path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope_path: str
    held_by_unit_id: str
    locked_at: str


class ScopeLockManager:
    """In-memory exclusive scope lock manager enforcing single-writer semantics."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held_scopes: dict[str, str] = {}  # scope_path -> unit_id

    def acquire_scope_locks(self, unit_id: str, scopes: tuple[str, ...]) -> tuple[str, ...]:
        with self._lock:
            # Check for conflicts
            conflicts = [s for s in scopes if s in self._held_scopes and self._held_scopes[s] != unit_id]
            if conflicts:
                holders = [f"{s} (held by {self._held_scopes[s]})" for s in conflicts]
                raise ScopeLockConflictError(f"Scope lock conflict for unit {unit_id}: {', '.join(holders)}")

            # Grant locks
            for s in scopes:
                self._held_scopes[s] = unit_id
            return tuple(scopes)

    def release_scope_locks(self, unit_id: str) -> tuple[str, ...]:
        with self._lock:
            released = [s for s, uid in self._held_scopes.items() if uid == unit_id]
            for s in released:
                del self._held_scopes[s]
            return tuple(released)

    def is_scope_locked(self, scope_path: str) -> bool:
        with self._lock:
            return scope_path in self._held_scopes

    def get_holder(self, scope_path: str) -> str | None:
        with self._lock:
            return self._held_scopes.get(scope_path)
