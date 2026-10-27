"""001N-W06: Same-boundary rollback — app/schema/workflow/config/event compatibility."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Boundary = Literal["app", "schema", "workflow", "config", "event"]


@dataclass(frozen=True)
class BoundaryVersion:
    boundary: Boundary
    version: int
    compatible_with: tuple[int, ...]


@dataclass(frozen=True)
class RollbackPlanV1:
    target_sha: str
    boundaries: tuple[BoundaryVersion, ...]
    allowed: bool
    reason_codes: tuple[str, ...]


def evaluate_same_boundary_rollback(
    *,
    current: dict[Boundary, int],
    target: dict[Boundary, int],
    compatibility: dict[Boundary, tuple[int, ...]],
    target_sha: str,
) -> RollbackPlanV1:
    reasons: list[str] = []
    versions: list[BoundaryVersion] = []
    for boundary, cur in current.items():
        tgt = target.get(boundary)
        if tgt is None:
            reasons.append(f"MISSING_TARGET:{boundary}")
            continue
        compat = compatibility.get(boundary, (cur,))
        versions.append(BoundaryVersion(boundary, tgt, compat))
        if tgt > cur:
            reasons.append(f"FORWARD_NOT_ROLLBACK:{boundary}")
        elif tgt not in compat and tgt != cur:
            reasons.append(f"INCOMPATIBLE:{boundary}:{tgt}")
    allowed = not reasons
    if allowed:
        reasons.append("SAME_BOUNDARY_ROLLBACK_OK")
    return RollbackPlanV1(target_sha=target_sha, boundaries=tuple(versions), allowed=allowed, reason_codes=tuple(reasons))
