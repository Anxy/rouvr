"""Ownership fencing at execution call sites (Stage 2 core).

A stale worker (lock taken over after expiry) must fail before its
first mutation, not after. Two mechanisms, both pure/testable:

1. Canonical resource identities: every file_path is resolved against
   the workspace root (symlinks resolved, escapes rejected) so the
   lock row and the write target name the same file.
2. Ownership gate: ``assert_ownership`` re-checks ``check_owner``
   immediately before mutation; loss raises ``OwnershipLost``.

Retry/timeout/cancellation policy rides on ``attempt_count``-bounded
attempts (see ``should_retry``); Temporal workflow wiring itself is
owner-gated (no client bootstrap in this env — recorded, not stubbed).
"""

from __future__ import annotations

import os
from typing import Any

from backend.session_factory.locks import LockConn, check_owner


class OwnershipLost(RuntimeError):
    """Lock taken over by another unit; this worker must stop."""


class ResourceEscape(ValueError):
    """file_path escapes the workspace root."""


def canonical_resource(workspace_root: str, file_path: str) -> str:
    """Resolve file_path inside workspace_root (symlinks resolved).

    Raises ResourceEscape when the resolved path leaves the root.
    Pure filesystem read (no writes); unit-tested with tmp dirs.
    """
    root = os.path.realpath(workspace_root)
    target = os.path.realpath(os.path.join(root, file_path))
    if target != root and not target.startswith(root + os.sep):
        raise ResourceEscape(f"escapes workspace: {file_path!r}")
    return target


def assert_ownership(conn: LockConn, *, file_path: str, unit_id: str) -> None:
    """Raise OwnershipLost unless unit_id still owns file_path."""
    if not check_owner(conn, file_path=file_path, unit_id=unit_id):
        raise OwnershipLost(f"{unit_id} lost {file_path}")


def should_retry(*, attempt_count: int, max_attempts: int) -> bool:
    """Attempt-bounded retry policy (mirrors work_units.attempt_count)."""
    return attempt_count < max_attempts


def guarded_write(
    conn: LockConn,
    *,
    workspace_root: str,
    file_path: str,
    unit_id: str,
    content: str,
) -> str:
    """Write content to the canonical path iff ownership holds.

    Returns the resolved path. Raises OwnershipLost / ResourceEscape
    before any mutation. The write itself is the only side effect.
    """
    target = canonical_resource(workspace_root, file_path)
    assert_ownership(conn, file_path=file_path, unit_id=unit_id)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(content)
    return target
