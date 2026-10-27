"""F11-AC-03 / AC-05: Durable hibernate/resume + SEM-001-style capsule continuity."""

from __future__ import annotations

import tempfile
from pathlib import Path

from backend.aix_core.framework.scale.batch_scheduler import BatchScheduler
from backend.aix_core.framework.scale.hibernation import CycleHibernationManager, WakeupTrigger
from backend.aix_core.framework.scale.work_graph_store import DurableWorkGraphStore
from backend.aix_core.framework.work.models import WorkContract, WorkGraph, WorkUnit


def _graph() -> WorkGraph:
    units = [
        WorkUnit(
            unit_id="u0",
            work_id="w_hib",
            unit_index=0,
            purpose="first",
            target_scope=("a.py",),
            contract=WorkContract(unit_id="u0"),
        ),
        WorkUnit(
            unit_id="u1",
            work_id="w_hib",
            unit_index=1,
            purpose="second",
            depends_on=("u0",),
            target_scope=("b.py",),
            contract=WorkContract(unit_id="u1"),
        ),
    ]
    return WorkGraph(graph_id="g_hib", work_id="w_hib", units=tuple(units))


def test_hibernate_24h_simulated_durable_restart():
    checkpoints: list[dict] = []

    def append(**kwargs):
        cid = f"chk_{len(checkpoints)+1}"
        checkpoints.append({"id": cid, **kwargs})
        return cid

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "hib.sqlite"
        store = DurableWorkGraphStore(db)
        store.save_work_graph(_graph())
        sched = BatchScheduler(store)
        mgr = CycleHibernationManager(store, sched, checkpoint_append=append)

        rec = mgr.suspend_cycle(
            "cyc_hib",
            "g_hib",
            reason="SCHEDULED_HIBERNATION",
            simulated_elapsed_hours=24.0,
            wakeup_trigger=WakeupTrigger(
                trigger_type="TIMER",
                trigger_payload={"simulated_elapsed_hours": 24.0},
            ),
        )
        assert rec.status == "SUSPENDED"
        assert rec.context_capsule is not None
        assert rec.checkpoint_id
        assert checkpoints
        store.close()

        # Restart
        store2 = DurableWorkGraphStore(db)
        sched2 = BatchScheduler(store2)
        mgr2 = CycleHibernationManager(store2, sched2, checkpoint_append=append)
        resumed, capsule = mgr2.resume_cycle("cyc_hib")
        assert resumed.status == "RESUMED"
        assert capsule is not None
        assert len(capsule.capsule_hash) == 64
        assert capsule.unit_id == "u0"
        # Idempotent second resume
        resumed2, capsule2 = mgr2.resume_cycle("cyc_hib")
        assert resumed2.status == "RESUMED"
        assert capsule2 is not None
        assert capsule2.capsule_hash == capsule.capsule_hash
        store2.close()


def test_sem001_resume_continuity_capsule_hash_stable():
    with tempfile.TemporaryDirectory() as tmp:
        store = DurableWorkGraphStore(Path(tmp) / "sem2.sqlite")
        store.save_work_graph(_graph())
        mgr = CycleHibernationManager(store, BatchScheduler(store))
        mgr.suspend_cycle("cyc_sem", "g_hib", simulated_elapsed_hours=24.0)
        _, cap1 = mgr.resume_cycle("cyc_sem")
        assert cap1 is not None
        assert cap1.purpose == "first"
        assert len(cap1.compute_hash()) == 64
        store.close()
