"""C035: incremental refresh + staleness / repository snapshot binding."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


class StalenessError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class RepositorySnapshot:
    snapshot_id: str
    fingerprint: str
    root: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "fingerprint": self.fingerprint,
            "root": self.root,
        }


def fingerprint_files(files: dict[str, str]) -> str:
    """Content fingerprint over relative path -> content map."""
    parts = []
    for path in sorted(files):
        digest = hashlib.sha256(files[path].encode("utf-8")).hexdigest()
        parts.append(f"{path}:{digest}")
    return "repo_" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def bind_snapshot(*, root: str, files: dict[str, str], snapshot_id: str | None = None) -> RepositorySnapshot:
    fp = fingerprint_files(files)
    return RepositorySnapshot(
        snapshot_id=snapshot_id or f"snap_{fp[5:]}",
        fingerprint=fp,
        root=root,
    )


@dataclass
class PlanBinding:
    plan_id: str
    bound_snapshot: RepositorySnapshot
    expected_delta: str | None = None  # optional explicit delta token


def assert_plan_current(
    binding: PlanBinding,
    *,
    current: RepositorySnapshot,
    replan_authorized: bool = False,
) -> None:
    if binding.bound_snapshot.fingerprint == current.fingerprint:
        return
    if binding.expected_delta and binding.expected_delta == current.fingerprint:
        return
    if replan_authorized:
        raise StalenessError("REPLAN_REQUIRED", current.fingerprint)
    raise StalenessError(
        "STALE_REPOSITORY_SNAPSHOT",
        f"plan={binding.bound_snapshot.fingerprint} current={current.fingerprint}",
    )


def invalidate_on_change(
    *,
    previous: RepositorySnapshot,
    files: dict[str, str],
) -> tuple[RepositorySnapshot, list[str]]:
    changed = []
    # Recompute; caller supplies full current map.
    current = bind_snapshot(root=previous.root, files=files)
    if current.fingerprint != previous.fingerprint:
        # Identify changed paths by comparing if we had old map — here files is current only,
        # so report fingerprint drift.
        changed.append("*")
    return current, changed


__all__ = [
    "StalenessError",
    "RepositorySnapshot",
    "fingerprint_files",
    "bind_snapshot",
    "PlanBinding",
    "assert_plan_current",
    "invalidate_on_change",
]
