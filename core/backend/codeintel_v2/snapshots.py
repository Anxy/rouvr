"""CodeIntel v2 snapshot records and store (Wave 4 Agent3, AIX lane).

A snapshot binds a ``(repo_id, commit_sha)`` pair to a ``snapshot_id``.
Snapshots are labels for bookkeeping only: they never grant authority and
never select actions. See ``freshness.py`` for the freshness label.
"""

from __future__ import annotations

import threading
import uuid
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class CodeIntelSnapshot(BaseModel):
    """Frozen spec (shared with Agent1 lane): exactly these three fields."""

    repo_id: str
    commit_sha: str
    snapshot_id: str


def codeintel_posture() -> str:
    """Read the AIX_CODEINTEL posture without hard-importing settings at load.

    Returns one of ``"off"`` / ``"advisory"`` (any other value, including an
    unreadable settings module, fails closed to ``"off"``). The flag itself is
    declared in ``backend.hyeve_aix_service.config`` and is never redeclared
    here.
    """
    try:
        from backend.hyeve_aix_service.config import settings as _settings

        posture = str(getattr(_settings, "AIX_CODEINTEL", "off") or "off").strip().lower()
    except Exception:
        return "off"
    return posture if posture in ("off", "advisory") else "off"


def _store_key(repo_id: str, commit_sha: str) -> tuple[str, str]:
    return (repo_id, commit_sha)


@runtime_checkable
class SnapshotStore(Protocol):
    """Store interface for snapshot records; PG can implement this later."""

    def get(self, repo_id: str, commit_sha: str) -> CodeIntelSnapshot | None:
        """Return the snapshot bound to ``(repo_id, commit_sha)``, if any."""
        ...

    def put(self, snapshot: CodeIntelSnapshot) -> CodeIntelSnapshot:
        """Store ``snapshot``; a duplicate ``(repo_id, commit_sha)`` returns the
        existing record instead of adding a row."""
        ...

    def get_or_create(self, repo_id: str, commit_sha: str) -> CodeIntelSnapshot:
        """Return the existing record for ``(repo_id, commit_sha)`` or create,
        store, and return a new one with a generated ``snapshot_id``."""
        ...


class InMemorySnapshotStore:
    """In-memory :class:`SnapshotStore` keyed by ``(repo_id, commit_sha)``."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], CodeIntelSnapshot] = {}
        self._lock = threading.Lock()

    def get(self, repo_id: str, commit_sha: str) -> CodeIntelSnapshot | None:
        with self._lock:
            return self._records.get(_store_key(repo_id, commit_sha))

    def put(self, snapshot: CodeIntelSnapshot) -> CodeIntelSnapshot:
        key = _store_key(snapshot.repo_id, snapshot.commit_sha)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                return existing
            self._records[key] = snapshot
            return snapshot

    def get_or_create(self, repo_id: str, commit_sha: str) -> CodeIntelSnapshot:
        key = _store_key(repo_id, commit_sha)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                return existing
            created = CodeIntelSnapshot(
                repo_id=repo_id,
                commit_sha=commit_sha,
                snapshot_id=uuid.uuid4().hex,
            )
            self._records[key] = created
            return created

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def list_for_repo(self, repo_id: str) -> list[CodeIntelSnapshot]:
        """Return all snapshots bound to ``repo_id`` (empty when none)."""
        with self._lock:
            return [
                snapshot
                for (known_repo, _), snapshot in self._records.items()
                if known_repo == repo_id
            ]

    def get_by_id(self, snapshot_id: str) -> CodeIntelSnapshot | None:
        """Return the snapshot carrying ``snapshot_id``, if any."""
        with self._lock:
            for snapshot in self._records.values():
                if snapshot.snapshot_id == snapshot_id:
                    return snapshot
            return None

    def clear(self) -> None:
        """Drop all records (tests / ephemeral use)."""
        with self._lock:
            self._records.clear()


__all__ = [
    "CodeIntelSnapshot",
    "InMemorySnapshotStore",
    "SnapshotStore",
    "codeintel_posture",
]
