"""F11-AC-01: Durable 1000-unit WorkGraph persist + reload after restart."""

from __future__ import annotations

import tempfile
from pathlib import Path

from backend.aix_core.framework.scale.work_graph_store import DurableWorkGraphStore
from backend.aix_core.framework.work.models import WorkContract, WorkGraph, WorkUnit


def _build_graph(n: int = 1000) -> WorkGraph:
    units: list[WorkUnit] = []
    domains = 10
    per = n // domains
    for d in range(domains):
        for i in range(per):
            uid = f"unit_d{d}_{i}"
            deps = (f"unit_d{d}_{i-1}",) if i > 0 else ()
            units.append(
                WorkUnit(
                    unit_id=uid,
                    work_id="work_scale_1000",
                    unit_index=(d * per) + i,
                    purpose=f"domain {d} unit {i}",
                    depends_on=deps,
                    target_scope=(f"src/d{d}/u{i}.py",),
                    contract=WorkContract(unit_id=uid),
                )
            )
    return WorkGraph(graph_id="graph_scale_1000", work_id="work_scale_1000", units=tuple(units))


def test_work_graph_1000_durable_persist_reload_restart():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "work_graph.sqlite"
        store = DurableWorkGraphStore(db)
        graph = _build_graph(1000)
        snap = store.save_work_graph(graph)
        assert snap.total_units == 1000
        edges = store.dependency_edges(graph.graph_id)
        assert len(edges) == 990  # 10 domains * 99 deps
        ready = store.query_ready_wavefront(graph.graph_id, limit=20)
        assert len(ready) == 10  # one root per domain
        store.close()

        # Process restart simulation
        store2 = DurableWorkGraphStore(db)
        loaded = store2.load_work_graph(graph.graph_id)
        assert loaded is not None
        assert loaded.graph_id == graph.graph_id
        assert len(loaded.units) == 1000
        assert store2.dependency_edges(graph.graph_id) == edges
        ready2 = store2.query_ready_wavefront(graph.graph_id, limit=20)
        assert {u.unit_id for u in ready2} == {u.unit_id for u in ready}
        store2.close()


def test_work_graph_1000_scale_persistence_and_wavefront():
    """Backward-compatible in-memory path still works for fast unit checks."""
    from backend.aix_core.framework.scale.work_graph_store import WorkGraphStore

    store = WorkGraphStore()
    graph = _build_graph(1000)
    snap = store.save_work_graph(graph)
    assert snap.total_units == 1000
    ready = store.query_ready_wavefront(graph.graph_id, limit=20)
    assert len(ready) == 10
