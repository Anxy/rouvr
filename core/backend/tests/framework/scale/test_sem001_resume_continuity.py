"""F11-AC-05 companion — SEM-001 capsule continuity across durable resume."""

from __future__ import annotations

import tempfile
from pathlib import Path

from backend.aix_core.framework.scale.batch_scheduler import BatchScheduler
from backend.aix_core.framework.scale.hibernation import CycleHibernationManager
from backend.aix_core.framework.scale.work_graph_store import DurableWorkGraphStore
from backend.aix_core.framework.work.models import WorkContract, WorkGraph, WorkUnit


def test_sem001_resume_continuity():
    units = (
        WorkUnit(
            unit_id="step_root",
            work_id="w",
            unit_index=0,
            purpose="Implement login route with SEM continuity",
            target_scope=("src/login.py",),
            contract=WorkContract(unit_id="step_root"),
        ),
    )
    graph = WorkGraph(graph_id="g_sem", work_id="w", units=units)
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "sem.sqlite"
        store = DurableWorkGraphStore(db)
        store.save_work_graph(graph)
        mgr = CycleHibernationManager(store, BatchScheduler(store))
        mgr.suspend_cycle("cyc", "g_sem", simulated_elapsed_hours=24.0)
        store.close()
        store2 = DurableWorkGraphStore(db)
        mgr2 = CycleHibernationManager(store2, BatchScheduler(store2))
        _, capsule = mgr2.resume_cycle("cyc")
        assert capsule is not None
        assert "SEM continuity" in capsule.purpose
        assert capsule.target_scope == ("src/login.py",)
        assert len(capsule.capsule_hash) == 64
        store2.close()
