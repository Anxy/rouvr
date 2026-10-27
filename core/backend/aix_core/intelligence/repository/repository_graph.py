"""AIX-W09-E01-T001: Persistent Repository Graph.

Provides a versioned, persistent entity-relationship graph covering files, symbols,
imports, calls, routes, services, tests, config, runtime, and commits with provenance
tracking and confidence metrics.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

EntityType = Literal[
    "FILE",
    "SYMBOL",
    "IMPORT",
    "CALL",
    "ROUTE",
    "SERVICE",
    "TEST",
    "CONFIG",
    "RUNTIME",
    "COMMIT",
]


@dataclass(frozen=True)
class GraphEntity:
    entity_id: str
    entity_type: EntityType
    name: str
    location: str
    confidence: float = 1.0
    provenance: str = "ast_analysis"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))


@dataclass(frozen=True)
class GraphEdge:
    source_id: str
    target_id: str
    relation: str
    confidence: float = 1.0
    provenance: str = "ast_analysis"


class PersistentRepositoryGraph:
    def __init__(self, commit_sha: str = "HEAD") -> None:
        self.commit_sha = commit_sha
        self._entities: dict[str, GraphEntity] = {}
        self._edges: list[GraphEdge] = []

    def add_entity(self, entity: GraphEntity) -> None:
        self._entities[entity.entity_id] = entity

    def add_edge(self, edge: GraphEdge) -> None:
        if edge.source_id not in self._entities:
            raise ValueError(f"source entity {edge.source_id!r} not in graph")
        if edge.target_id not in self._entities:
            raise ValueError(f"target entity {edge.target_id!r} not in graph")
        self._edges.append(edge)

    def get_entity(self, entity_id: str) -> GraphEntity | None:
        return self._entities.get(entity_id)

    def query_entities(self, entity_type: EntityType) -> tuple[GraphEntity, ...]:
        return tuple(e for e in self._entities.values() if e.entity_type == entity_type)

    def query_edges_from(self, source_id: str) -> tuple[GraphEdge, ...]:
        return tuple(e for e in self._edges if e.source_id == source_id)

    def save_to_file(self, target_path: Path) -> None:
        data = {
            "commit_sha": self.commit_sha,
            "entities": [asdict(e) for e in self._entities.values()],
            "edges": [asdict(e) for e in self._edges],
        }
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load_from_file(cls, source_path: Path) -> PersistentRepositoryGraph:
        content = json.loads(source_path.read_text(encoding="utf-8"))
        graph = cls(commit_sha=content.get("commit_sha", "HEAD"))
        for raw_ent in content.get("entities", []):
            graph.add_entity(GraphEntity(**raw_ent))
        for raw_edge in content.get("edges", []):
            graph.add_edge(GraphEdge(**raw_edge))
        return graph


__all__ = ["GraphEntity", "GraphEdge", "EntityType", "PersistentRepositoryGraph"]
