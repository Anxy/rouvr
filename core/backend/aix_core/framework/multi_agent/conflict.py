"""AIX Framework Multi-Agent Conflict Detector.

Sprint: F10 Work Compiler + Multi-Agent Runtime (F10-P4)
Grounded in: F10-MULTI-AGENT-PROTOCOL.md, invariant analysis.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict

from backend.aix_core.framework.work.models import WorkGraph, WorkUnit


class ScopeCollision(BaseModel):
    """Detected collision between two units modifying identical scopes without dependency."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit_a: str
    unit_b: str
    overlapping_scopes: tuple[str, ...]


class ConflictDetector:
    """Detects invalid concurrent writes and unmanaged scope overlaps."""

    @classmethod
    def detect_graph_collisions(cls, graph: WorkGraph) -> tuple[ScopeCollision, ...]:
        collisions: list[ScopeCollision] = []
        units = graph.units

        # Map each unit to its direct & transitive dependencies
        adj: dict[str, set[str]] = {u.unit_id: set(u.depends_on) for u in units}

        def get_all_ancestors(node: str, visited: set[str] | None = None) -> set[str]:
            if visited is None:
                visited = set()
            ancestors = set()
            for dep in adj.get(node, set()):
                if dep not in visited:
                    visited.add(dep)
                    ancestors.add(dep)
                    ancestors |= get_all_ancestors(dep, visited)
            return ancestors

        ancestors_map = {u.unit_id: get_all_ancestors(u.unit_id) for u in units}

        for i in range(len(units)):
            for j in range(i + 1, len(units)):
                u1 = units[i]
                u2 = units[j]

                # Check if there is an ordered dependency between u1 and u2
                is_ordered = (u2.unit_id in ancestors_map[u1.unit_id]) or (u1.unit_id in ancestors_map[u2.unit_id])
                if not is_ordered:
                    # Parallel units: check for overlapping target scopes
                    overlap = set(u1.target_scope) & set(u2.target_scope)
                    if overlap:
                        collisions.append(
                            ScopeCollision(
                                unit_a=u1.unit_id,
                                unit_b=u2.unit_id,
                                overlapping_scopes=tuple(sorted(overlap)),
                            )
                        )

        return tuple(collisions)
