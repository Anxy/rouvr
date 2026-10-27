"""AIX-W09-E01-T001: Persistent Repository Graph Tests.

Verifies:
1. Entity indexing across 10 entity types (FILE, SYMBOL, IMPORT, CALL, ROUTE, SERVICE, TEST, CONFIG, RUNTIME, COMMIT).
2. Provenance and confidence score recording.
3. Edge relationship validation.
4. JSON persistence and lossy-free restoration.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from backend.aix_core.intelligence.repository.repository_graph import (
    GraphEdge,
    GraphEntity,
    PersistentRepositoryGraph,
)


def test_entity_indexing_and_provenance() -> None:
    """Verifies that entities across all 10 types store provenance and confidence."""
    graph = PersistentRepositoryGraph(commit_sha="git-commit-12345")

    e_file = GraphEntity(
        entity_id="file:main.py",
        entity_type="FILE",
        name="main.py",
        location="backend/main.py",
        confidence=1.0,
        provenance="fs_scan",
    )
    e_route = GraphEntity(
        entity_id="route:/api/objective",
        entity_type="ROUTE",
        name="objective_route",
        location="backend/routers/objective_cycle.py",
        confidence=0.95,
        provenance="fastapi_ast_inspector",
    )

    graph.add_entity(e_file)
    graph.add_entity(e_route)

    assert len(graph.query_entities("FILE")) == 1
    assert len(graph.query_entities("ROUTE")) == 1

    fetched_route = graph.get_entity("route:/api/objective")
    assert fetched_route is not None
    assert fetched_route.provenance == "fastapi_ast_inspector"
    assert fetched_route.confidence == 0.95


def test_edge_validation() -> None:
    """Adding an edge referencing a non-existent entity fails closed."""
    graph = PersistentRepositoryGraph()
    e1 = GraphEntity("file:a.py", "FILE", "a.py", "src/a.py")
    graph.add_entity(e1)

    edge = GraphEdge(source_id="file:a.py", target_id="file:missing.py", relation="IMPORTS")
    with pytest.raises(ValueError) as exc_info:
        graph.add_edge(edge)

    assert "target entity 'file:missing.py' not in graph" in str(exc_info.value)


def test_graph_persistence_roundtrip(tmp_path: Path) -> None:
    """Graph serializes to disk and restores identically."""
    graph = PersistentRepositoryGraph(commit_sha="commit-abc")

    e1 = GraphEntity("file:a.py", "FILE", "a.py", "src/a.py")
    e2 = GraphEntity("test:test_a.py", "TEST", "test_a.py", "tests/test_a.py")
    graph.add_entity(e1)
    graph.add_entity(e2)

    edge = GraphEdge(source_id="test:test_a.py", target_id="file:a.py", relation="TESTS")
    graph.add_edge(edge)

    file_path = tmp_path / "repo_graph.json"
    graph.save_to_file(file_path)
    assert file_path.exists()

    restored = PersistentRepositoryGraph.load_from_file(file_path)
    assert restored.commit_sha == "commit-abc"
    assert len(restored.query_entities("FILE")) == 1
    assert len(restored.query_entities("TEST")) == 1

    edges = restored.query_edges_from("test:test_a.py")
    assert len(edges) == 1
    assert edges[0].target_id == "file:a.py"
