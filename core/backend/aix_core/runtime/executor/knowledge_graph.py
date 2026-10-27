"""EX4-R2-P2-S002: Persistent Knowledge Graph — tenant-scoped edge store (Postgres-first model).

No dedicated graph database. DEPENDS_ON stays in DependencyGraph — do not duplicate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

EDGE_TYPES = frozenset(
    {
        "DERIVED_FROM",
        "SUPERSEDES",
        "ABOUT",
        "DECIDED_BY",
        "USES_PATTERN",
        "CONFLICTS_WITH",
    }
)
EDGE_STATUSES = frozenset({"ACTIVE", "SUPERSEDED"})
NODE_TYPES = frozenset(
    {
        "knowledge",
        "evidence",
        "work_item",
        "decision",
        "proposal",
        "pattern",
        "capability",
        "problem",
        "project",
    }
)

EdgeType = Literal[
    "DERIVED_FROM",
    "SUPERSEDES",
    "ABOUT",
    "DECIDED_BY",
    "USES_PATTERN",
    "CONFLICTS_WITH",
]
EdgeStatus = Literal["ACTIVE", "SUPERSEDED"]


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


@dataclass(frozen=True)
class KnowledgeGraphEdge:
    edge_id: str
    tenant_id: str
    edge_type: str
    from_type: str
    from_id: str
    to_type: str
    to_id: str
    command_id: str
    status: str = "ACTIVE"
    authority_context: str | None = None
    provenance: tuple[str, ...] = ()


@dataclass
class InMemoryKnowledgeGraph:
    """Governed relationship edges for Persistent Knowledge Graph v1."""

    _edges: list[KnowledgeGraphEdge] = field(default_factory=list)

    def add_edge(
        self,
        *,
        tenant_id: str,
        edge_type: str,
        from_type: str,
        from_id: str,
        to_type: str,
        to_id: str,
        command_id: str,
        edge_id: str | None = None,
        authority_context: str | None = None,
        provenance: list[str] | tuple[str, ...] | None = None,
        supersede_matching_active: bool = False,
    ) -> KnowledgeGraphEdge:
        tenant_id = _require_tenant(tenant_id)
        command_id = str(command_id or "").strip()
        if not command_id:
            raise ExecutionKernelRepositoryError("command_id is required")
        for existing in self._edges:
            if existing.tenant_id == tenant_id and existing.command_id == command_id:
                return existing
        if edge_type not in EDGE_TYPES:
            raise ExecutionKernelRepositoryError(f"invalid edge_type={edge_type!r}")
        if from_type not in NODE_TYPES or to_type not in NODE_TYPES:
            raise ExecutionKernelRepositoryError("invalid node type")
        from_id = str(from_id or "").strip()
        to_id = str(to_id or "").strip()
        if not from_id or not to_id:
            raise ExecutionKernelRepositoryError("from_id and to_id are required")

        if supersede_matching_active:
            replaced: list[KnowledgeGraphEdge] = []
            for e in self._edges:
                if (
                    e.tenant_id == tenant_id
                    and e.status == "ACTIVE"
                    and e.edge_type == edge_type
                    and e.from_type == from_type
                    and e.from_id == from_id
                    and e.to_type == to_type
                    and e.to_id == to_id
                ):
                    replaced.append(
                        KnowledgeGraphEdge(
                            edge_id=e.edge_id,
                            tenant_id=e.tenant_id,
                            edge_type=e.edge_type,
                            from_type=e.from_type,
                            from_id=e.from_id,
                            to_type=e.to_type,
                            to_id=e.to_id,
                            command_id=e.command_id,
                            status="SUPERSEDED",
                            authority_context=e.authority_context,
                            provenance=e.provenance,
                        )
                    )
                else:
                    replaced.append(e)
            self._edges = replaced

        edge = KnowledgeGraphEdge(
            edge_id=str(edge_id or f"kge_{new_ulid()}"),
            tenant_id=tenant_id,
            edge_type=edge_type,
            from_type=from_type,
            from_id=from_id,
            to_type=to_type,
            to_id=to_id,
            command_id=command_id,
            status="ACTIVE",
            authority_context=authority_context,
            provenance=tuple(provenance or ()),
        )
        self._edges.append(edge)
        return edge

    def get_edge(self, *, tenant_id: str, edge_id: str) -> KnowledgeGraphEdge | None:
        tenant_id = _require_tenant(tenant_id)
        for e in self._edges:
            if e.tenant_id == tenant_id and e.edge_id == edge_id:
                return e
        return None

    def supersede_edge(self, *, tenant_id: str, edge_id: str) -> KnowledgeGraphEdge:
        """Resolve an edge while retaining its immutable identity and history."""
        tenant_id = _require_tenant(tenant_id)
        for index, edge in enumerate(self._edges):
            if edge.tenant_id == tenant_id and edge.edge_id == edge_id:
                if edge.status == "SUPERSEDED":
                    return edge
                updated = KnowledgeGraphEdge(
                    edge_id=edge.edge_id,
                    tenant_id=edge.tenant_id,
                    edge_type=edge.edge_type,
                    from_type=edge.from_type,
                    from_id=edge.from_id,
                    to_type=edge.to_type,
                    to_id=edge.to_id,
                    command_id=edge.command_id,
                    status="SUPERSEDED",
                    authority_context=edge.authority_context,
                    provenance=edge.provenance,
                )
                self._edges[index] = updated
                return updated
        raise ExecutionKernelRepositoryError(f"knowledge graph edge not found: {edge_id}")

    def list_edges(
        self,
        *,
        tenant_id: str,
        edge_type: str | None = None,
        from_type: str | None = None,
        from_id: str | None = None,
        to_type: str | None = None,
        to_id: str | None = None,
        active_only: bool = True,
    ) -> list[KnowledgeGraphEdge]:
        tenant_id = _require_tenant(tenant_id)
        out: list[KnowledgeGraphEdge] = []
        for e in self._edges:
            if e.tenant_id != tenant_id:
                continue
            if active_only and e.status != "ACTIVE":
                continue
            if edge_type and e.edge_type != edge_type:
                continue
            if from_type and e.from_type != from_type:
                continue
            if from_id and e.from_id != from_id:
                continue
            if to_type and e.to_type != to_type:
                continue
            if to_id and e.to_id != to_id:
                continue
            out.append(e)
        return out

    def get_edges(self, **filters: Any) -> list[KnowledgeGraphEdge]:
        """Repository-contract name; list_edges remains for compatibility."""
        return self.list_edges(**filters)

    def traverse(
        self,
        *,
        tenant_id: str,
        start_type: str,
        start_id: str,
        edge_types: list[str] | tuple[str, ...] | None = None,
        direction: Literal["out", "in", "both"] = "out",
        max_depth: int = 2,
    ) -> list[dict[str, Any]]:
        """Bounded BFS over ACTIVE edges. max_depth capped at 5."""
        tenant_id = _require_tenant(tenant_id)
        start_type = str(start_type or "").strip()
        start_id = str(start_id or "").strip()
        if start_type not in NODE_TYPES or not start_id:
            raise ExecutionKernelRepositoryError("valid start_type and start_id are required")
        if direction not in {"out", "in", "both"}:
            raise ExecutionKernelRepositoryError("direction must be out, in, or both")
        if max_depth < 1:
            raise ExecutionKernelRepositoryError("max_depth must be >= 1")
        max_depth = min(max_depth, 5)
        allowed = set(edge_types) if edge_types else set(EDGE_TYPES)
        invalid_edge_types = allowed - EDGE_TYPES
        if invalid_edge_types:
            raise ExecutionKernelRepositoryError(
                f"invalid edge types: {sorted(invalid_edge_types)!r}"
            )
        start = (start_type, start_id)
        visited: set[tuple[str, str]] = {start}
        frontier: list[tuple[str, str, int]] = [(start_type, start_id, 0)]
        paths: list[dict[str, Any]] = []

        while frontier:
            node_type, node_id, depth = frontier.pop(0)
            if depth >= max_depth:
                continue
            candidates: list[KnowledgeGraphEdge] = []
            if direction in {"out", "both"}:
                candidates.extend(
                    self.list_edges(
                        tenant_id=tenant_id, from_type=node_type, from_id=node_id, active_only=True
                    )
                )
            if direction in {"in", "both"}:
                candidates.extend(
                    self.list_edges(
                        tenant_id=tenant_id, to_type=node_type, to_id=node_id, active_only=True
                    )
                )
            for edge in candidates:
                if edge.edge_type not in allowed:
                    continue
                outbound = edge.from_type == node_type and edge.from_id == node_id
                if direction == "out" and not outbound:
                    continue
                if direction == "in" and outbound:
                    continue
                nxt = (edge.to_type, edge.to_id) if outbound else (edge.from_type, edge.from_id)
                paths.append(
                    {
                        "depth": depth + 1,
                        "edge_id": edge.edge_id,
                        "edge_type": edge.edge_type,
                        "from_type": edge.from_type,
                        "from_id": edge.from_id,
                        "to_type": edge.to_type,
                        "to_id": edge.to_id,
                    }
                )
                if nxt not in visited:
                    visited.add(nxt)
                    frontier.append((nxt[0], nxt[1], depth + 1))
        return paths


def edge_to_dict(edge: KnowledgeGraphEdge) -> dict[str, Any]:
    return {
        "edge_id": edge.edge_id,
        "tenant_id": edge.tenant_id,
        "edge_type": edge.edge_type,
        "from_type": edge.from_type,
        "from_id": edge.from_id,
        "to_type": edge.to_type,
        "to_id": edge.to_id,
        "command_id": edge.command_id,
        "status": edge.status,
        "authority_context": edge.authority_context,
        "provenance": list(edge.provenance),
    }


__all__ = [
    "EDGE_TYPES",
    "EDGE_STATUSES",
    "NODE_TYPES",
    "KnowledgeGraphEdge",
    "InMemoryKnowledgeGraph",
    "edge_to_dict",
]
