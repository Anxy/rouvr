"""Freshness labels for CodeIntel v2 snapshots (Wave 4 Agent3, AIX lane).

Freshness is a label, never an authority grant: :func:`check_freshness`
classifies a snapshot against a workspace HEAD and returns a
:class:`Freshness` record. It performs no I/O, changes no state, and never
raises — unclassifiable inputs become ``unknown``, a classified failure that
must never be read as ``current``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from backend.codeintel_v2.snapshots import CodeIntelSnapshot

FreshnessLabel = Literal["current", "stale", "unknown"]


class Freshness(BaseModel):
    """Outcome of :func:`check_freshness`."""

    repo_id: str
    commit_sha: str
    snapshot_id: str
    workspace_head: str | None
    freshness: FreshnessLabel
    reason: str


def _norm_sha(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:
        return ""


def check_freshness(snapshot: Any, workspace_head: Any) -> Freshness:
    """Classify ``snapshot`` against ``workspace_head`` by construction.

    - ``commit_sha == workspace_head`` (both known) => ``current``.
    - mismatch => ``stale`` with an explicit reason naming both shas.
    - missing/unknown sha on either side, or an unreadable snapshot, =>
      ``unknown`` as a classified failure. Never ``current``, never raises.
    """
    try:
        repo_id = _norm_sha(getattr(snapshot, "repo_id", ""))
        commit_sha = _norm_sha(getattr(snapshot, "commit_sha", ""))
        snapshot_id = _norm_sha(getattr(snapshot, "snapshot_id", ""))
        head = _norm_sha(workspace_head)
        snapshot_ok = (
            snapshot is not None
            and isinstance(snapshot, CodeIntelSnapshot)
            and bool(repo_id)
            and bool(commit_sha)
            and bool(snapshot_id)
        )
        if not snapshot_ok or not head:
            if not snapshot_ok and not head:
                reason = (
                    "unknown: snapshot record is missing or unreadable and "
                    "workspace HEAD is missing or unreadable"
                )
            elif not snapshot_ok:
                reason = "unknown: snapshot record is missing or unreadable"
            else:
                reason = "unknown: workspace HEAD is missing or unreadable"
            return Freshness(
                repo_id=repo_id,
                commit_sha=commit_sha,
                snapshot_id=snapshot_id,
                workspace_head=head or None,
                freshness="unknown",
                reason=reason,
            )
        if commit_sha == head:
            return Freshness(
                repo_id=repo_id,
                commit_sha=commit_sha,
                snapshot_id=snapshot_id,
                workspace_head=head,
                freshness="current",
                reason="current: workspace HEAD matches snapshot commit_sha",
            )
        return Freshness(
            repo_id=repo_id,
            commit_sha=commit_sha,
            snapshot_id=snapshot_id,
            workspace_head=head,
            freshness="stale",
            reason=(
                f"stale: workspace HEAD {head!r} differs from "
                f"snapshot commit_sha {commit_sha!r}"
            ),
        )
    except Exception as exc:
        return Freshness(
            repo_id="",
            commit_sha="",
            snapshot_id="",
            workspace_head=None,
            freshness="unknown",
            reason=f"unknown: freshness check failed without raising ({exc!r})",
        )


def workspace_head(path: str | Path) -> str | None:
    """Return ``git rev-parse HEAD`` for ``path``, or ``None``.

    Returns ``None`` when ``path`` is not inside a git repo, git is missing,
    the command fails, or output is empty — never raises.
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(path),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    sha = (completed.stdout or "").strip()
    return sha or None


__all__ = [
    "Freshness",
    "FreshnessLabel",
    "check_freshness",
    "workspace_head",
]
